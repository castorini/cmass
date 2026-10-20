"""The run directory: one place per pipeline run, holding every stage's per-question records.

  <run>/cmass.json                      config (config.py)
  <run>/inputs/<set>.jsonl              Stage 1 output: projected candidates or near misses (records.py)
  <run>/cache/docs/<docid>.json         document text cache ({"docid", "text"})
  <run>/cache/searches/<key>.json       search result cache
  <run>/work/<set>/<qid>.audit.json     C2-C3 audit (validate.py checks it)
  <run>/work/<set>/<qid>.searchlog.jsonl every search made for the question
  <run>/packets/<set>/<qid>.md          the auditor's packet
  <run>/revisions/<set>/<qid>.r<N>.json  C4 revision attempts
  <run>/adversarial/<set>/<qid>.hunt.json, .verify.json   C5 hunter findings and verifier rulings
  <run>/review/                         C5 human review: site data, reviews/reviews.json, reviewed/*.jsonl
  <run>/final_check/<set>/<qid>.json    C6 final check
  <run>/final/<set>_final.jsonl, <set>_excluded.jsonl     output records
  <run>/qrels/<set>/...                 Stage 3

The run directory is the current directory unless $CMASS_RUN names another.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import config as _config


class Run:
    def __init__(self, root: str | os.PathLike | None = None, config_path=None):
        self.root = Path(root or os.environ.get("CMASS_RUN") or ".").resolve()
        self.config = _config.load(self.root, config_path)
        self.cache = self.root / "cache"
        self.docs = self.cache / "docs"
        self.searches = self.cache / "searches"

    # ------------------------------------------------------------------ paths
    def input_file(self, sid: str) -> Path:
        return self.root / self.config.set(sid)["input"]

    def work_dir(self, sid: str) -> Path:
        return self.root / "work" / sid

    def audit_file(self, sid: str, qid: str) -> Path:
        return self.work_dir(sid) / f"{qid}.audit.json"

    def searchlog(self, sid: str, qid: str) -> Path:
        return self.work_dir(sid) / f"{qid}.searchlog.jsonl"

    def packet(self, sid: str, qid: str) -> Path:
        return self.root / "packets" / sid / f"{qid}.md"

    def revision_file(self, sid: str, qid: str, attempt: int) -> Path:
        return self.root / "revisions" / sid / f"{qid}.r{attempt}.json"

    def adversarial_file(self, sid: str, qid: str, kind: str) -> Path:
        return self.root / "adversarial" / sid / f"{qid}.{kind}.json"

    def final_check_file(self, sid: str, qid: str) -> Path:
        return self.root / "final_check" / sid / f"{qid}.json"

    def audited_file(self, sid: str) -> Path:
        return self.root / "audited" / f"{sid}.jsonl"

    def review_dir(self) -> Path:
        return self.root / "review"

    def final_file(self, sid: str, kind: str = "final") -> Path:
        return self.root / "final" / f"{sid}_{kind}.jsonl"

    def qrels_dir(self, sid: str) -> Path:
        return self.root / "qrels" / sid

    # ------------------------------------------------------------------ records
    def records(self, sid: str) -> list[dict]:
        p = self.input_file(sid)
        if not p.exists():
            raise SystemExit(f"input for set {sid} not found: {p}")
        out = []
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                r.setdefault("record_id", str(r.get("id", "")))
                r["record_id"] = str(r["record_id"])
                out.append(r)
        return out

    def record(self, sid: str, qid: str) -> dict:
        for r in self.records(sid):
            if r["record_id"] == str(qid):
                return r
        raise SystemExit(f"{sid}/{qid}: not in {self.input_file(sid)}")

    def load_audit(self, sid: str, qid: str) -> dict | None:
        p = self.audit_file(sid, qid)
        return json.loads(p.read_text()) if p.exists() else None


def write_json(path: Path, obj) -> None:
    """Atomic JSON write (agents of a running workflow may be reading the file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    tmp.replace(path)


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
