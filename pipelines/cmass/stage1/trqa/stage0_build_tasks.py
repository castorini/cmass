#!/usr/bin/env python3
"""Stage 0, task construction for Total Recall QA: one task per TRQA test question -> tasks/<set>.jsonl.

  python3 stage0_build_tasks.py [--run RUN] [--set trqa] [--raw DIR] [--subsets wiki1,wiki2,ecommerce]

Source: Hugging Face `mahtaa/trqa`, config "queries", splits wiki1_test / wiki2_test / ecommerce_test
(169 + 1,258 + 900 = 2,327 questions in the real run). `question` and `answer` are canary-encrypted upstream
and are decrypted here (trqa_source.py); `datatype` (or `src`) is plaintext. With --raw DIR the raw JSONL
files from `trqa_source.py download` are read instead of the `datasets` loader (same rows).

Each task: {"record_id": "<subset>__<TRQA id>", "question", "answer" (a string, as decrypted), "answer_type":
"aggregate", "source": "<TRQA id>", "subset", "datatype"}. TRQA tasks carry no hints. The record id is the
"stem" every later file of the real run used.

Ported from the private repo's artifacts/trqa/tools/build_trqa_tasks.py (which wrote one tasks/<stem>.json per
question with the same fields); the run directory needs a cmass.json naming the set (see README.md).
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))          # pipelines/cmass: the shared package
sys.path.insert(0, str(HERE))
from cmass.run import Run, write_jsonl  # noqa: E402
import trqa_source  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", help="run directory (default: $CMASS_RUN or the current directory)")
    p.add_argument("--set", default="trqa", help="the set's name in cmass.json (default trqa)")
    p.add_argument("--raw", help="read the raw HF JSONL files in this directory instead of the datasets loader")
    p.add_argument("--subsets", default=",".join(trqa_source.SUBSETS))
    a = p.parse_args()
    run = Run(a.run)
    tasks, per = [], Counter()
    for sub in a.subsets.split(","):
        rows = trqa_source.load_raw(Path(a.raw), sub) if a.raw else trqa_source.load_queries_hf(sub)
        for r in rows:
            tasks.append({"record_id": trqa_source.stem(sub, r["id"]), "question": r["question"],
                          "answer": r["answer"], "answer_type": "aggregate", "source": r["id"],
                          "subset": sub, "datatype": r["datatype"]})
            per[sub] += 1
        print(f"  {sub}: {per[sub]} queries")
    ids = [t["record_id"] for t in tasks]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate record ids after building stems")
    out = run.task_file(a.set)
    write_jsonl(out, tasks)
    print(f"wrote {len(tasks)} tasks to {out}")


if __name__ == "__main__":
    main()
