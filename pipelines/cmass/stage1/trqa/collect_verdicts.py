#!/usr/bin/env python3
"""Answerability check, collection: one row per question from the projector's files -> stage1/<set>/verdicts.jsonl.

  python3 collect_verdicts.py [--run RUN] [--set trqa] [--partial]

Row: {record_id, subset, verdict, matches_gold, recall_complete, n_exp, n_rec}, copied from
stage1/<set>/project/<qid>.json (the record id is the file's question id). Prints the tables the real run
reported: verdicts overall and per subset, the "loose" count (matches_gold regardless of grounding, an upper
bound the real run did NOT use), and wiki2 split into "classic" (`wiki2__<n>_p<prop>`) and "quest"
(`wiki2__quest_*`). The strict verdict is the one carried forward: SUPPORTED requires complete recall of the
entity set and its attributes from the corpus AND a computed aggregate equal to the gold answer.

Reconstructed: the private repo has the output (artifacts/trqa/verdicts.jsonl, FINAL_RESULTS.md) but not the
script; this rebuilds the same rows from the projection files (checked against the real run's files).
Without --partial it refuses to run while a question has no valid projection.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from cmass.run import Run, write_jsonl  # noqa: E402
from trqa_state import Stage1, validate  # noqa: E402


def population(qid: str) -> str:
    sub = qid.split("__", 1)[0]
    if sub != "wiki2":
        return sub
    return "wiki2-quest" if "quest_" in qid.split("__", 1)[1] else "wiki2-classic"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run")
    p.add_argument("--set", default="trqa")
    p.add_argument("--partial", action="store_true", help="write the rows that exist and list the missing ones")
    a = p.parse_args()
    s1 = Stage1(Run(a.run), a.set)
    rows, missing = [], []
    for qid, task in s1.tasks().items():
        errs, _ = validate(s1, "project", qid)
        if errs:
            missing.append(qid)
            continue
        d = s1.load("project", qid)
        rows.append({"record_id": qid, "subset": d.get("subset") or task.get("subset"), "verdict": d["verdict"],
                     "matches_gold": d["matches_gold"], "recall_complete": d["recall_complete"],
                     "n_exp": d.get("n_entities_expected"), "n_rec": d.get("n_entities_recovered")})
    if missing and not a.partial:
        raise SystemExit(f"{len(missing)} question(s) have no valid projection yet (e.g. {missing[:3]}); "
                         "run the projector or pass --partial")
    out = s1.list_file("verdicts.jsonl")
    write_jsonl(out, rows)
    print(f"wrote {len(rows)} rows to {out}" + (f"; {len(missing)} without a projection" if missing else ""))
    c = Counter(r["verdict"] for r in rows)
    print(f"all: n={len(rows)} " + " ".join(f"{v}={c[v]}" for v in ("SUPPORTED", "PARTIAL", "NOT")))
    by_subset = lambda r: r["subset"]
    by_wiki2_population = lambda r: population(r["record_id"])
    for key, sel_rows in ((by_subset, rows), (by_wiki2_population, [r for r in rows if r["subset"] == "wiki2"])):
        for pop in sorted({key(r) for r in sel_rows}):
            sel = [r for r in sel_rows if key(r) == pop]
            cc = Counter(r["verdict"] for r in sel)
            rate = 100 * cc["SUPPORTED"] / len(sel) if sel else 0
            print(f"  {pop:14} n={len(sel):5} SUPPORTED={cc['SUPPORTED']} ({rate:.1f}%) PARTIAL={cc['PARTIAL']} NOT={cc['NOT']}")
    print(f"loose (matches_gold regardless of grounding; not used): {sum(1 for r in rows if r['matches_gold'])}")


if __name__ == "__main__":
    main()
