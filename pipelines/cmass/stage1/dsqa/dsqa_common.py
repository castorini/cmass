"""Shared helpers for the DeepSearchQA (DSQA) Stage 1 tools: paths inside the run directory, the lists every
step reads and writes, and the text checks the deterministic steps use.

Run layout (inside <run>/stage1/<set>/, see README.md for the order):

  <step>/<qid>.json              one agent output per question and step (Run.stage1_file)
  <step>/<qid>.searchlog.jsonl   that step's searches for the question (cm.py --stage 1 --step <step>)
  packets/<step>/<qid>.json      that agent's input, written by dsqa_state.py
  supported.jsonl, released.jsonl, audited.jsonl, extreme.jsonl, evidence.jsonl, final.jsonl, near_misses.jsonl
                                 the deterministic lists (dsqa_build.py)
  *.json                         verification maps, id lists, manual decision files, counts.json
  expansion/                     the expansion candidate selection (dsqa_expansion.py)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPELINE = HERE.parent.parent                      # pipelines/cmass
sys.path.insert(0, str(PIPELINE))
from cmass.run import Run, read_jsonl, write_json, write_jsonl  # noqa: E402,F401

SINGLE, SET = "Single Answer", "Set Answer"        # DSQA's own answer_type labels, as the agents saw them
ANSWER_TYPE = {SINGLE: "single", SET: "set"}       # the shared pipeline's answer types (cmass/records.py)
DEFAULT_SET = "dsqa"


def nkey(qid) -> list:
    """Natural sort key: dsqa_9 before dsqa_10."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(qid))]


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def contained(snippet, text) -> bool:
    """The verbatim test every DSQA verification step used: whitespace normalised on both sides, and a snippet
    made of fragments joined by ' ... ' passes when every fragment occurs in the document."""
    t = norm(text)
    parts = [x.strip() for x in norm(snippet).split(" ... ")]
    return bool(norm(snippet)) and bool(t) and all(p and p in t for p in parts)


def load_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class S1:
    """The Stage 1 directory of one set inside a run."""

    def __init__(self, run: Run, sid: str):
        self.run, self.sid = run, sid
        self.dir = run.stage1_dir(sid)

    def path(self, name: str) -> Path:
        return self.dir / name

    def out(self, step: str, qid: str) -> Path:
        return self.run.stage1_file(self.sid, step, qid)

    def packet(self, step: str, qid: str) -> Path:
        return self.dir / "packets" / step / f"{qid}.json"

    def outputs(self, step: str) -> dict:
        """{qid: output} for every readable agent output of a step (file name = qid)."""
        d = self.dir / step
        res = {}
        for f in sorted(d.glob("*.json"), key=lambda p: nkey(p.stem)) if d.exists() else []:
            obj = load_json(f)
            if isinstance(obj, dict):
                res[f.stem] = obj
        return res

    def jsonl(self, name: str) -> list[dict]:
        return read_jsonl(self.path(name))

    def by_id(self, name: str) -> dict:
        return {r["record_id"]: r for r in self.jsonl(name)}

    def ids(self, name: str) -> list[str]:
        return list(load_json(self.path(name), []) or [])

    def tasks(self) -> dict:
        return {t["record_id"]: t for t in self.run.tasks(self.sid)}

    def counts(self, step: str, values: dict) -> None:
        """Record a step's aggregate counts in counts.json (the README's funnel comes from these)."""
        p = self.path("counts.json")
        c = load_json(p, {}) or {}
        c[step] = values
        write_json(p, c)


def task_view(t: dict) -> dict:
    """A Stage 0 task as the original projector read it: {record_id, problem, answer, answer_type, problem_category}."""
    return {"record_id": t["record_id"], "problem": t.get("question") or t.get("problem") or "",
            "answer": t.get("answer", ""), "answer_type": t.get("dsqa_answer_type") or
            {v: k for k, v in ANSWER_TYPE.items()}.get(t.get("answer_type"), t.get("answer_type", "")),
            "problem_category": t.get("problem_category", "")}


def corpus(run: Run):
    from cmass.corpus import Corpus
    return Corpus(run)


def labels(s1: S1) -> dict:
    """{docid: {"label", "lines"}} from doc_labels.json (dsqa_build.py labels)."""
    raw = load_json(s1.path("doc_labels.json"), {}) or {}
    return {k: (v if isinstance(v, dict) else {"label": v, "lines": []}) for k, v in raw.items()}


def polished(s1: S1) -> dict:
    """{qid: {index: {"doc_id", "snippet"}}}: each extreme-list hop's evidence after the snippet polish. The
    polished snippet replaces the extreme audit's only where it verified (ok, or repaired: then the repaired
    text); otherwise the hop keeps the extreme audit's snippet and its first verified document."""
    ext = s1.by_id("extreme.jsonl")
    pol = s1.outputs("polish")
    ver = load_json(s1.path("polish_verification.json"), {}) or {}
    status, repaired = ver.get("status", {}), ver.get("repaired", {})
    out = {}
    for q, r in ext.items():
        ph = {h.get("index"): h for h in (pol.get(q) or {}).get("hops") or []}
        out[q] = {}
        for i, h in enumerate(r["hops"], 1):
            key = f"{q}/{i}"
            p = ph.get(i)
            if p is not None and status.get(key, "ok") in ("ok", "repaired") and q in pol:
                out[q][i] = {"doc_id": p.get("doc_id", ""), "snippet": repaired.get(key, p.get("snippet", ""))}
            else:
                out[q][i] = {"doc_id": (h["supporting_doc_ids"] or [""])[0], "snippet": h.get("snippet", "")}
    return out


def extreme_why(a: dict) -> str:
    """The extreme audit's one-line failure text for a dropped question (as its builder printed it)."""
    why = ("ungrounded hop(s) " + str(a.get("ungrounded_hops"))) if not a.get("all_hops_grounded") else ""
    if not a.get("all_atoms_covered"):
        why += ("; " if why else "") + "uncovered: " + "; ".join(map(str, (a.get("uncovered_atoms") or [])[:2]))
    return why
