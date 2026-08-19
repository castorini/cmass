#!/usr/bin/env python3
"""Build the Stage 1 All-hops verification output (deterministic).

Reads the per-question verification outputs (runs/<qid>.json) and keeps a question iff both checks
pass:
  1. ALL HOPS GROUNDED - every hop of the original decomposition has a ClimbMix document whose full
     text states its fact. No hop is dropped to let a question pass.
  2. COVERAGE - every information-bearing content clue of the question is represented by a hop.

Independently projected candidates verified by the same All-hops verification workflow land in the
same runs/ directory and are included automatically.

Reads : runs/*.json, inputs/*.json (prepared by stage1_all_hops_verification_build_inputs.py)
Writes: projected_questions_final.jsonl   the final set
        projected_questions_final.md      index
"""
import glob
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
load = lambda f: json.load(open(f))

runs = {str(load(f)["qid"]): load(f) for f in glob.glob(f"{BASE}/runs/*.json")}

kept = []
for q in sorted(runs, key=int):
    r = runs[q]
    hops = r.get("hops") or []
    if not hops or not all(h.get("supported") for h in hops):
        continue
    if not r.get("coverage_ok", False):
        continue
    m = load(f"{BASE}/inputs/{q}.json")
    kept.append({
        "record_id": q,
        "question": m.get("question", ""),
        "answer": m.get("answer", ""),
        "n_hops": len(hops),
        "hops": [{"clue": h.get("clue", ""),
                  "redundant": bool(h.get("redundant", False)),
                  "supporting_doc_ids": h.get("doc_ids") or []} for h in hops],
    })

with open(f"{BASE}/projected_questions_final.jsonl", "w") as fo:
    for r in kept:
        fo.write(json.dumps(r, ensure_ascii=False) + "\n")

md = [f"# Final corpus-grounded benchmark ({len(kept)})", "",
      "Every hop grounded by the full text of a ClimbMix document, and every content clue of the",
      "question represented by a hop.", "",
      "| qid | question | answer | #hops |", "|---|---|---|---:|"]
for r in kept:
    md.append(f"| {r['record_id']} | {r['question'].replace('|', ' ').replace(chr(10), ' ')[:88]} "
              f"| {str(r['answer']).replace('|', ' ')[:36]} | {r['n_hops']} |")
open(f"{BASE}/projected_questions_final.md", "w").write("\n".join(md) + "\n")

print(f"kept {len(kept)} of {len(runs)} verified questions")
