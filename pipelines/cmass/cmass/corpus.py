"""The corpus: `search` (ranked results) and `get_document` (full text by id), behind one of two backends.

  backend "cli"   a two-command CLI you supply (the same contract as pipelines/bcp_climbmix):
                    <search cmd> "<query>" <hits> <preview_chars>  -> {"results": [{"rank", "docid", "score", "preview"}]}
                    <doc cmd> <docid>                               -> {"docid", "doc"}   (full text)
  backend "rest"  a Pyserini-style REST server:
                    GET <base_url>/v1/<index>/search?query=&hits=   -> {"candidates": [{"docid", "rank", "score", "doc"}]}
                    GET <base_url>/v1/<index>/doc/<docid>           -> {"doc": ...}
                  The bearer token is read from the environment variable named by "token_env", or else from
                  "token_file", a dotenv-style file with a NAME=value line for that variable (useful when agents'
                  shells do not share one exported environment). It is never printed or written anywhere.

Every document fetched is cached under <run>/cache/docs, every search under <run>/cache/searches, so
re-reading costs nothing. Requests are paced across all processes on the host (one request start every
"min_gap_s" seconds, at most "slots" requests open), and an identical search already in flight in
another process is waited for, not sent again: parallel agents share one corpus service, which may
also limit requests per token.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
import random
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .run import Run


class CorpusError(RuntimeError):
    pass


class Corpus:
    def __init__(self, run: Run):
        self.run = run
        self.cfg = run.config.corpus
        self.backend = self.cfg.get("backend", "cli")
        if self.backend not in ("cli", "rest"):
            raise SystemExit(f"corpus.backend must be 'cli' or 'rest', got {self.backend!r}")
        self.lock_dir = Path(self.cfg.get("lock_dir") or Path(tempfile.gettempdir()) / f"cmass_locks_{os.getuid()}")
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        run.docs.mkdir(parents=True, exist_ok=True)
        run.searches.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ cache
    def _doc_path(self, docid: str) -> Path:
        return self.run.docs / f"{docid}.json"

    def cached_doc(self, docid: str) -> str | None:
        p = self._doc_path(docid)
        return json.loads(p.read_text())["text"] if p.exists() else None

    def save_doc(self, docid: str, text: str) -> None:
        p = self._doc_path(docid)
        if not p.exists():
            tmp = p.with_name(f"{p.name}.tmp{os.getpid()}.{threading.get_ident()}")
            tmp.write_text(json.dumps({"docid": docid, "text": text}, ensure_ascii=False))
            tmp.replace(p)

    # ------------------------------------------------------------------ pacing
    def _pace(self) -> None:
        gap = float(self.cfg.get("min_gap_s", 2.0))
        with open(self.lock_dir / "pace", "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                txt = f.read().strip()
                wait = (float(txt) if txt else 0.0) + gap - time.time()
                if wait > 0:
                    time.sleep(wait)
                f.seek(0)
                f.truncate()
                f.write(str(time.time()))
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    class _Slot:
        def __init__(self, corpus):
            self.c = corpus

        def __enter__(self):
            while True:
                for i in range(int(self.c.cfg.get("slots", 1))):
                    f = open(self.c.lock_dir / f"slot_{i}", "a+")
                    try:
                        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        self.f = f
                        return self
                    except OSError:
                        f.close()
                time.sleep(random.uniform(0.2, 0.6))

        def __exit__(self, *exc):
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()

    # ------------------------------------------------------------------ backends
    def _token(self) -> str:
        rest = self.cfg.get("rest") or {}
        name = rest.get("token_env", "PYSERINI_API_TOKEN")
        tok = os.environ.get(name, "")
        if not tok and rest.get("token_file"):
            f = Path(rest["token_file"]).expanduser()
            if f.exists():
                for line in f.read_text().splitlines():
                    k, _, v = line.partition("=")
                    if k.strip().removeprefix("export ").strip() == name:
                        tok = v.strip().strip("'\"")
        return tok

    def _rest_get(self, path: str, params: dict | None) -> dict:
        rest = self.cfg.get("rest") or {}
        base = rest.get("base_url", "").rstrip("/")
        token = self._token()
        url = f"{base}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        delay = 3.0
        for _ in range(int(self.cfg.get("retries", 20))):
            self._pace()
            req = urllib.request.Request(url)
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            try:
                with self._Slot(self), urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return {"status": 404}
                if e.code == 429 or e.code >= 500:      # overload or a per-token request limit: back off
                    ra = e.headers.get("Retry-After") if e.headers else None
                    ra = float(ra) if ra and ra.replace(".", "").isdigit() else 0.0
                    time.sleep(max(ra, delay) * random.uniform(1.0, 1.25))
                    delay = min(delay * 2, 120)
                    continue
                if e.code in (401, 403):
                    raise SystemExit("corpus authorization failed: check the token environment variable")
                raise CorpusError(f"HTTP {e.code} for {path}")
            except (urllib.error.URLError, TimeoutError):
                time.sleep(delay)
                delay = min(delay * 2, 60)
        raise CorpusError(f"request failed after {self.cfg.get('retries', 20)} attempts: {path}")

    def _cli(self, which: str, args: list[str]) -> dict:
        cmd = (self.cfg.get("cli") or {}).get(which)
        if not cmd:
            raise SystemExit(f"corpus.cli.{which} is not configured")
        cmd = cmd.split() if isinstance(cmd, str) else list(cmd)
        delay = 3.0
        for _ in range(int(self.cfg.get("retries", 20))):
            self._pace()
            with self._Slot(self):
                r = subprocess.run(cmd + args, capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                try:
                    return json.loads(r.stdout)
                except json.JSONDecodeError:
                    raise CorpusError(f"{which}: output is not JSON: {r.stdout[:200]!r}")
            time.sleep(delay)
            delay = min(delay * 2, 120)
        raise CorpusError(f"{which} failed after retries: {r.stderr[-300:]}")

    # ------------------------------------------------------------------ public API
    def get_document(self, docid: str) -> str | None:
        """Full text of a document, or None when the corpus does not have it."""
        text = self.cached_doc(docid)
        if text is not None:
            return text
        if self.backend == "rest":
            idx = (self.cfg.get("rest") or {}).get("index", self.cfg.get("name"))
            d = self._rest_get(f"/v1/{idx}/doc/{urllib.parse.quote(docid)}", None)
            if d.get("status") == 404 or "doc" not in d:
                return None
            doc = d["doc"]
        else:
            d = self._cli("doc", [docid])
            if not d or d.get("doc") is None:
                return None
            doc = d["doc"]
        text = doc if isinstance(doc, str) else (doc.get("text") or json.dumps(doc, ensure_ascii=False))
        self.save_doc(docid, text)
        return text

    def search(self, query: str, hits: int = 10, sid: str | None = None, qid: str | None = None,
               preview_chars: int = 220) -> list[dict]:
        """Ranked results [{rank, docid, score, preview}]. Logged to the question's search log when sid/qid
        are given."""
        key = hashlib.sha1(f"{query}|{hits}".encode()).hexdigest()[:16]
        p = self.run.searches / f"{key}.json"
        with open(self.run.searches / f".{key}.lock", "a+") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                rec = json.loads(p.read_text()) if p.exists() else None
                if rec is None:
                    if self.backend == "rest":
                        idx = (self.cfg.get("rest") or {}).get("index", self.cfg.get("name"))
                        d = self._rest_get(f"/v1/{idx}/search", {"query": query, "hits": hits})
                        res = []
                        for c in d.get("candidates", []):
                            doc = c.get("doc")
                            text = doc if isinstance(doc, str) else ((doc or {}).get("text") or "")
                            if text:
                                self.save_doc(c["docid"], text)
                            res.append({"rank": c["rank"], "docid": c["docid"], "score": c["score"],
                                        "preview": " ".join(text.split())[:preview_chars]})
                    else:
                        d = self._cli("search", [query, str(hits), str(preview_chars)])
                        res = [{"rank": c.get("rank", i + 1), "docid": c["docid"], "score": c.get("score"),
                                "preview": c.get("preview", "")} for i, c in enumerate(d.get("results", []))]
                    rec = {"query": query, "hits": hits, "results": res, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
                    tmp = p.with_name(f"{p.name}.tmp{os.getpid()}.{threading.get_ident()}")
                    tmp.write_text(json.dumps(rec, ensure_ascii=False))
                    tmp.replace(p)
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
        if sid and qid:
            log = self.run.searchlog(sid, qid)
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "query": query, "hits": hits,
                                    "results": [r["docid"] for r in rec["results"]]}, ensure_ascii=False) + "\n")
        return rec["results"]
