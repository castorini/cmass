"""Accessors for Stage 1 candidate records, tolerant of the field names different projections use.

A candidate record (one line of inputs/<set>.jsonl) has at least:

  {"record_id": "...",
   "question": "...",                        # or "problem"
   "answer": "..." | ["member", ...],        # or "answers" / "gold_answer"; a list for set answers
   "answer_type": "single" | "set" | "aggregate",   # optional; else the set's default
   "hops": [{"clue": "...", "doc_id": "...", "supporting_doc_ids": ["..."], "snippet": "...",
             "kind": "derivation", "redundant": false}]}

Optional fields are kept and passed through: "hints" (source paragraphs; hints only, never grounding
evidence), "source" (the original benchmark id), and anything dataset-specific.
"""
from __future__ import annotations


def question_text(rec: dict) -> str:
    return rec.get("question") or rec.get("problem") or ""


def gold_answer(rec: dict):
    """The gold answer as stored: a string, or a list for set answers."""
    for k in ("answer", "answers", "gold_answer"):
        if k in rec and rec[k] not in (None, ""):
            v = rec[k]
            if k == "answers" and isinstance(v, list) and len(v) == 1 and answer_type(rec) == "single":
                return v[0]
            return v
    return ""


def answer_text(rec: dict) -> str:
    g = gold_answer(rec)
    return " | ".join(map(str, g)) if isinstance(g, list) else str(g)


def answer_type(rec: dict, default: str = "single") -> str:
    return rec.get("answer_type") or default


def hops(rec: dict) -> list[dict]:
    return list(rec.get("hops") or [])


def hop_doc_ids(hop: dict) -> list[str]:
    """Cited documents of a hop, primary first, deduplicated."""
    out = []
    for d in [hop.get("doc_id")] + list(hop.get("supporting_doc_ids") or []) + list(hop.get("doc_ids") or []):
        if d and d not in out:
            out.append(d)
    return out


def cited_doc_ids(rec: dict) -> list[str]:
    out = []
    for h in hops(rec):
        for d in hop_doc_ids(h):
            if d not in out:
                out.append(d)
    return out
