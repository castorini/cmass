#!/usr/bin/env python3
"""Stage 0, task construction for DeepSearchQA: one task per DSQA prompt -> <run>/tasks/<set>.jsonl.

  python3 stage0_tasks.py [--set dsqa]                  # download DSQA-full.csv from Hugging Face (google/deepsearchqa)
  python3 stage0_tasks.py --csv /path/DSQA-full.csv     # or read a local copy of the same file

As run: the 900 rows of the dataset's single split ("eval", the file DSQA-full.csv; columns problem,
problem_category, answer, answer_type) became one task each, in file order, with record_id dsqa_000 ...
dsqa_899 (the row number: DSQA has no id column). The task carries the problem, the gold answer exactly as
DSQA writes it (a Set Answer is one string, usually comma-separated: the projector splits it itself), DSQA's
answer_type label and the problem category. Nothing is filtered or rewritten. No hints: DSQA has none.

Task fields (cmass/run.py, Run.tasks): record_id, question (DSQA's "problem"), answer, answer_type ("single" or
"set", from DSQA's "Single Answer" / "Set Answer"), dsqa_answer_type (the original label, which every DSQA agent
prompt uses), problem_category, source {dataset, split, row}.

The run used the CSV downloaded on 2026-07-23; its SHA-256 is EXPECTED_SHA256 below. A different checksum means
the dataset changed since (the script warns and continues). Set HF_TOKEN if the download needs a token.
Run inside a run directory (or set $CMASS_RUN); the run needs a cmass.json naming the set.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsqa_common import ANSWER_TYPE, DEFAULT_SET, Run, write_jsonl  # noqa: E402

HF_DATASET = "google/deepsearchqa"
CSV_URL = f"https://huggingface.co/datasets/{HF_DATASET}/resolve/main/DSQA-full.csv"
EXPECTED_SHA256 = "25d48dcf7efa872e5467032e8b8eedf38d301f59a252d0da95cda584baa78396"
COLUMNS = ("problem", "problem_category", "answer", "answer_type")


def fetch_csv() -> bytes:
    req = urllib.request.Request(CSV_URL)
    tok = os.environ.get("HF_TOKEN", "")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def build(raw: bytes, prefix: str = "dsqa") -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    missing = [c for c in COLUMNS if rows and c not in rows[0]]
    if missing:
        raise SystemExit(f"the CSV lacks columns {missing}; expected {COLUMNS}")
    width = max(3, len(str(len(rows) - 1)))
    tasks = []
    for i, r in enumerate(rows):
        at = r["answer_type"].strip()
        if at not in ANSWER_TYPE:
            raise SystemExit(f"row {i}: unknown answer_type {at!r}")
        tasks.append({"record_id": f"{prefix}_{i:0{width}d}", "question": r["problem"], "answer": r["answer"],
                      "answer_type": ANSWER_TYPE[at], "dsqa_answer_type": at,
                      "problem_category": r["problem_category"],
                      "source": {"dataset": HF_DATASET, "split": "eval", "row": i}})
    return tasks


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--set", default=DEFAULT_SET)
    ap.add_argument("--csv", help="a local copy of DSQA-full.csv (default: download it)")
    ap.add_argument("--prefix", default="dsqa", help="record_id prefix (default dsqa)")
    a = ap.parse_args()
    run = Run()
    raw = Path(a.csv).read_bytes() if a.csv else fetch_csv()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != EXPECTED_SHA256:
        print(f"warning: the CSV's SHA-256 is {sha}, not the one the CMASS run used ({EXPECTED_SHA256})",
              file=sys.stderr)
    tasks = build(raw, a.prefix)
    out = run.task_file(a.set)
    write_jsonl(out, tasks)
    c = Counter(t["dsqa_answer_type"] for t in tasks)
    print(f"wrote {len(tasks)} tasks to {out} ({', '.join(f'{k} {n}' for k, n in sorted(c.items()))})")


if __name__ == "__main__":
    main()
