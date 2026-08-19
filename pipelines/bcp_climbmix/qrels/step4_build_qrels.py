#!/usr/bin/env python3
"""Step 4: assemble the finalized qrels.

One line per query: qid, question, answer, the hop list, and `qrels` - a mapping
from document id to the hop indexes that document supports.

A (document, hop) pair lands in `qrels` when either
  * the reviewer voted "Supports" / "Partial support" on it, or
  * it was read in full during step 2 and accepted in step 3.

It is excluded when the vote was "Does not support" with no recorded override,
when it was read and rejected, or when its hop was dropped as not needed.

Hop indexes are the ORIGINAL numbering: skipped hops keep their index and are
marked "skipped", so downstream indexes never shift.

A kept hop that ends with zero supporting documents is reported in
`needs_review` rather than silently dropped - that list should be empty before
the output is used.

Usage:
  python3 step4_build_qrels.py --out work/qrels.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import config

FORCE_INCLUDE = {"Supports", "Partial support"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    config.add_work_dir(parser)
    parser.add_argument("--qids", help="comma-separated; default = every qid with a verdicts file")
    parser.add_argument("--out", help="default <work-dir>/qrels.jsonl")
    args = parser.parse_args()

    p = config.paths(args)
    out_path = Path(args.out) if args.out else p["qrels"]
    candidates = json.loads(p["candidates"].read_text())
    qids = ([q.strip() for q in args.qids.split(",")] if args.qids
            else sorted((f.stem for f in p["verdicts"].glob("*.json")), key=int))

    lines, needs_review = [], []
    for qid in qids:
        entry = candidates[qid]
        verdicts = json.loads((p["verdicts"] / f"{qid}.json").read_text())
        judged = {d: set(h) for d, h in verdicts["judged"].items()}
        rejected = {(d, int(h)) for d, hs in verdicts["rejections"].items() for h in hs}

        qrels: dict[str, set[int]] = {}
        for row in entry["candidates"]:
            if row["hop_skipped"]:
                continue
            doc, hop = row["doc_id"], row["hop_index"]
            if (doc, hop) in rejected:
                continue
            if row["human_judgment"] in FORCE_INCLUDE or hop in judged.get(doc, ()):
                qrels.setdefault(doc, set()).add(hop)

        supported = {h for hs in qrels.values() for h in hs}
        for hop in entry["hops"]:
            if not hop["skip"] and hop["index"] not in supported:
                needs_review.append({"qid": qid, "hop": hop["index"]})

        lines.append({
            "qid": qid,
            "question": entry["question"],
            "answer": entry["answer"],
            "hops": [{"index": h["index"], "clue": h["clue"], "hop_type": h["hop_type"],
                      "skipped": h["skip"]} for h in entry["hops"]],
            "qrels": {d: sorted(hs) for d, hs in sorted(qrels.items())},
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as fh:
        for line in lines:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    docs = sum(len(l["qrels"]) for l in lines)
    pairs = sum(len(v) for l in lines for v in l["qrels"].values())
    print(f"queries {len(lines)}  documents {docs}  document-hop pairs {pairs}")
    print(f"needs_review: {needs_review or 'none'}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
