#!/usr/bin/env python3
"""Inputs of the strict audit: every SUPPORTED question -> stage1/<set>/strict_inputs.jsonl.

  python3 build_strict_inputs.py [--run RUN] [--set trqa]

Row: {stem, subset, question, gold_answer, operator, target_attribute, entity_set_description,
computed_value, evidence}: the question from the task, everything else copied from the projector's file
(gold_answer is the projector's copy of the gold; a mismatch with the task's answer is reported).
Only verdict SUPPORTED goes on: PARTIAL and NOT questions leave Stage 1 here (no near-miss list was made
from them; README.md).

Reconstructed: the private repo has the 119 input files (artifacts/trqa/strict_audit/inputs/) but not the
script that wrote them; their fields are exactly these (checked against the real run's files).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from cmass.run import Run, read_jsonl, write_jsonl  # noqa: E402
from trqa_state import Stage1  # noqa: E402

FIELDS = ("subset", "gold_answer", "operator", "target_attribute", "entity_set_description", "computed_value", "evidence")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run")
    p.add_argument("--set", default="trqa")
    a = p.parse_args()
    s1 = Stage1(Run(a.run), a.set)
    verdicts = read_jsonl(s1.list_file("verdicts.jsonl"))
    if not verdicts:
        raise SystemExit("no verdicts.jsonl: run collect_verdicts.py first")
    rows, mismatch = [], []
    for v in verdicts:
        if v["verdict"] != "SUPPORTED":
            continue
        qid = v["record_id"]
        d, task = s1.load("project", qid), s1.tasks()[qid]
        rows.append({"stem": qid, "question": task["question"], **{k: d.get(k) for k in FIELDS}})
        if str(d.get("gold_answer")).strip() != str(task["answer"]).strip():
            mismatch.append(qid)
    out = s1.list_file("strict_inputs.jsonl")
    write_jsonl(out, rows)
    print(f"wrote {len(rows)} strict-audit inputs (verdict SUPPORTED) to {out}")
    if mismatch:
        print(f"note: {len(mismatch)} projection(s) copied a gold answer that differs from the task's: {mismatch[:5]}")


if __name__ == "__main__":
    main()
