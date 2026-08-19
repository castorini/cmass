#!/usr/bin/env python3
"""Collect per-record verdicts from the Stage 1 Answerability check.

Reads every q_all/projections/bcp_<qid>.json
(`stage1_hop_clue_decomposition_and_grounding.js` output) and writes:
  - q_all/all_830_verdicts.jsonl
  - merged/verdicts.jsonl
each row {record_id, verdict, method:"inference_allowed_1agent"}, sorted by record_id.
qid 393 is AUP-blocked (content-policy refusal; no projection file) -> verdict AUP_SKIP.
"""
import json, os
from collections import Counter

TOOLS = os.path.dirname(os.path.abspath(__file__))
QALL = os.path.dirname(TOOLS)
BASE = os.path.dirname(QALL)
MERGED = os.path.join(BASE, "merged"); os.makedirs(MERGED, exist_ok=True)
projdir = os.path.join(QALL, "projections")

allv = {}
for f in os.listdir(projdir):
    if not f.endswith(".json"):
        continue
    qid = f[4:-5]
    if qid == "None":
        continue
    allv[qid] = json.load(open(os.path.join(projdir, f))).get("verdict", "?")
allv.setdefault("393", "AUP_SKIP")  # AUP-blocked record (no projection produced)

rows = [{"record_id": q, "verdict": allv[q], "method": ("content_policy_block" if allv[q] == "AUP_SKIP" else "inference_allowed_1agent")}
        for q in sorted(allv, key=lambda x: int(x))]
for path in (os.path.join(QALL, "all_830_verdicts.jsonl"), os.path.join(MERGED, "verdicts.jsonl")):
    with open(path, "w") as fo:
        for r in rows:
            fo.write(json.dumps(r) + "\n")

print(f"wrote {len(rows)} verdicts; tally = {dict(Counter(r['verdict'] for r in rows))}")
