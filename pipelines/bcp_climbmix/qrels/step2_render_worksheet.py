#!/usr/bin/env python3
"""Step 2: render the documents a qid still needs a judgment on.

Rows already voted "Supports" / "Partial support" are settled and are not
rendered. What remains is everything undecided plus, with --show-excluded, the
"Does not support" rows a conservative override would have to read first.

Text comes from the step-1 cache, so the judgment is made against the full
document rather than a workbook excerpt. This step only prints; the reading and
the decision happen outside it (see README, "The judging loop").

Usage:
  python3 step2_render_worksheet.py --qid 78
  python3 step2_render_worksheet.py --qid 25 --show-excluded
  python3 step2_render_worksheet.py --qid 78 --slice 0:40 --max-chars 4500
"""

from __future__ import annotations

import argparse
import json

import config
from step1_fetch_docs import doc_path

UNDECIDED = (None, "Unclear")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--qid", required=True)
    config.add_work_dir(parser)
    parser.add_argument("--show-excluded", action="store_true",
                        help="render the 'Does not support' rows instead")
    parser.add_argument("--slice", help="start:end over the pending rows")
    parser.add_argument("--max-chars", type=int, default=4500,
                        help="truncation guard; raise it rather than judge a cut document")
    args = parser.parse_args()

    p = config.paths(args)
    entry = json.loads(p["candidates"].read_text())[args.qid]
    cache = p["doc_cache"]

    print(f"QID {args.qid}  {entry['question']}")
    print(f"ANSWER: {entry['answer']}\n")
    for hop in entry["hops"]:
        mark = "  [skipped: not needed]" if hop["skip"] else ""
        print(f"  h{hop['index']} ({hop['hop_type']}){mark}: {hop['clue']}")
    print()

    rows = [r for r in entry["candidates"] if not r["hop_skipped"]]
    if args.show_excluded:
        rows = [r for r in rows if r["force_exclude"]]
    else:
        rows = [r for r in rows if not r["force_exclude"]
                and r["human_judgment"] in UNDECIDED]
    if args.slice:
        lo, hi = (int(x) if x else None for x in args.slice.split(":"))
        rows = rows[lo:hi]

    print(f"{len(rows)} row(s) to read\n" + "=" * 72)
    for i, row in enumerate(rows):
        path = doc_path(cache, row["doc_id"])
        payload = json.loads(path.read_text()) if path.exists() else {"doc": None}
        text = payload.get("doc") or "<NOT CACHED - run step 1>"
        note = f"  note: {row['human_note']}" if row.get("human_note") else ""
        print(f"\n[{i}] {row['doc_id']}  hop h{row['hop_index']}  "
              f"vote={row['human_judgment']!r}{note}")
        print(f"    length={len(text)} chars")
        print(text[:args.max_chars])
        if len(text) > args.max_chars:
            print(f"    ... TRUNCATED at {args.max_chars}; re-run with a larger --max-chars")
        print("-" * 72)


if __name__ == "__main__":
    main()
