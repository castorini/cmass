#!/usr/bin/env python3
"""ClimbMix retrieval helper with query-match-centered previews.

Self-contained (stdlib). Reads ``PYSERINI_API_TOKEN`` from the environment or
from a repo-local ``.env.local`` file.

Usage:
  python3 cm.py search "<natural-language query>" [hits=30] [preview_chars=600]
  python3 cm.py doc <docid>

CHANGE vs v1: `search` previews are now a window CENTERED ON THE BEST
QUERY-TERM MATCH inside each document (a reconstructed highlight), not the
document head. BM25 can match deep in a long doc, so the head was often
uninformative; this surfaces the actually-relevant passage. Falls back to the
head if no query content-term is found in the doc.
"""
from __future__ import annotations

import bisect
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("CM_BASE_URL", "http://api.castorini.uwaterloo.ca").rstrip("/")
INDEX = os.environ.get("CM_INDEX", "climbmix-400b")
TIMEOUT = float(os.environ.get("CM_TIMEOUT", "30"))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ENV_FALLBACKS = (
    os.path.join(REPO_ROOT, ".env.local"),
    os.path.join(os.getcwd(), ".env.local"),
)
_STOP = set(
    "the a an of in on and or to for with is are was were be by as at from that this "
    "their they it its his her he she who which what when where how into over under more "
    "less than but not only also can could would should may might will did does do has have "
    "had been being you your we our us i me my one two three first second name real life "
    "series character played play plays year years".split()
)


def _token() -> str:
    tok = os.environ.get("PYSERINI_API_TOKEN")
    if tok:
        return tok
    for path in _ENV_FALLBACKS:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    s = line.strip()
                    if s.startswith("PYSERINI_API_TOKEN="):
                        return s.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def _request(path: str, params: dict) -> dict:
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    tok = _token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "body": exc.read().decode("utf-8", "replace")[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _as_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _snippet(text: str, query: str, width: int) -> str:
    """Return a ~width-char window centered on the densest query-term match."""
    if width <= 0 or not text:
        return text
    toks = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2 and t not in _STOP]
    if not toks:
        return text[:width]
    low = text.lower()
    hits = []  # (pos, term)
    for t in set(toks):
        start = 0
        while True:
            i = low.find(t, start)
            if i == -1:
                break
            hits.append((i, t))
            start = i + len(t)
            if len(hits) > 4000:
                break
    if not hits:
        return text[:width]
    hits.sort()
    pos = [h[0] for h in hits]
    terms = [h[1] for h in hits]
    best_i, best_distinct, best_total = 0, -1, -1
    for s in range(len(hits)):
        e = bisect.bisect_left(pos, pos[s] + width)
        distinct = len(set(terms[s:e]))
        total = e - s
        if distinct > best_distinct or (distinct == best_distinct and total > best_total):
            best_distinct, best_total, best_i = distinct, total, s
    start = max(0, pos[best_i] - width // 4)
    snip = text[start:start + width]
    return ("…" if start > 0 else "") + snip + ("…" if start + width < len(text) else "")


def search(query: str, hits: int, preview: int) -> dict:
    data = _request(f"/v1/{INDEX}/search", {"query": query, "hits": hits})
    if "error" in data:
        return data
    results = []
    for cand in data.get("candidates", []):
        text = _as_text(cand.get("doc"))
        results.append({
            "rank": cand.get("rank"),
            "docid": cand.get("docid"),
            "score": round(float(cand.get("score", 0) or 0), 2),
            "preview": _snippet(text, query, preview),
        })
    return {"query": query, "k": hits, "n": len(results), "results": results}


def doc(docid: str) -> dict:
    data = _request(f"/v1/{INDEX}/doc/{docid}", {})
    if "error" in data:
        return data
    return {"docid": data.get("docid", docid), "doc": _as_text(data.get("doc"))}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__); return 2
    cmd = argv[1]
    if cmd == "search":
        if len(argv) < 3:
            print(json.dumps({"error": "usage: cm.py search <query> [hits] [preview]"})); return 2
        query = argv[2]
        hits = int(argv[3]) if len(argv) > 3 else 30
        preview = int(argv[4]) if len(argv) > 4 else 600
        print(json.dumps(search(query, hits, preview), ensure_ascii=False)); return 0
    if cmd == "doc":
        if len(argv) < 3:
            print(json.dumps({"error": "usage: cm.py doc <docid>"})); return 2
        print(json.dumps(doc(argv[2]), ensure_ascii=False)); return 0
    print(json.dumps({"error": f"unknown command '{cmd}'"})); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
