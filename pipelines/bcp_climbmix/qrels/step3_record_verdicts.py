#!/usr/bin/env python3
"""Step 3: turn a reviewed rejection list into a verdicts file.

Every pending (document, hop) pair for a qid is read during step 2. Rather than
restate hundreds of accepted pairs, only the pairs that FAILED are recorded,
each with the reason; everything else is accepted. That keeps the decision
auditable and the file small.

Rejections live in <work-dir>/rejections/<qid>.json, keyed by document id, then
by hop index, with the reason as the value:

  {"<doc_id>": {"<hop_index>": "<why this document does not support that hop>"}}

Write the reason as evidence, not a verdict: quote the sentence that decides it,
and say what a passing document would have said instead. A rejection with no
contrast case is the one most likely to be wrong.

Usage:
  python3 step3_record_verdicts.py --qid 78
  python3 step3_record_verdicts.py --all
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import config

UNDECIDED = (None, "Unclear")


def record(qid: str, candidates: dict, rejections_dir: Path, out_dir: Path) -> dict:
    entry = candidates[qid]
    reject_file = rejections_dir / f"{qid}.json"
    rejections = json.loads(reject_file.read_text()) if reject_file.exists() else {}

    judged: dict[str, list[int]] = {}
    overrides: dict[str, list[int]] = {}
    for row in entry["candidates"]:
        if row["hop_skipped"]:
            continue
        doc, hop = row["doc_id"], row["hop_index"]
        rejected = str(hop) in (rejections.get(doc) or {})
        if row["force_exclude"]:
            # A "Does not support" row is only revived by an explicit override,
            # which is recorded as the ABSENCE of a rejection plus a note; the
            # default here stays with the human.
            continue
        if row["human_judgment"] in UNDECIDED and not rejected:
            judged.setdefault(doc, []).append(hop)

    out = {
        "qid": qid,
        "judged": {d: sorted(set(h)) for d, h in sorted(judged.items())},
        "rejections": rejections,
        "overrides": overrides,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{qid}.json").write_text(json.dumps(out, indent=1))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    config.add_work_dir(parser)
    parser.add_argument("--qid")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if not args.qid and not args.all:
        parser.error("pass --qid or --all")

    p = config.paths(args)
    candidates = json.loads(p["candidates"].read_text())
    qids = list(candidates) if args.all else [args.qid]
    for qid in qids:
        out = record(qid, candidates, p["rejections"], p["verdicts"])
        n_rej = sum(len(v) for v in out["rejections"].values())
        print(f"qid {qid}: accepted {sum(len(v) for v in out['judged'].values())} pairs, "
              f"{n_rej} rejected")


if __name__ == "__main__":
    main()
