#!/usr/bin/env python3
"""Build Stage 1 All-hops verification inputs for the 326 PROJECTABLE questions.

For each question, emit {qid, question, answer, hops:[clue strings]}, where `hops` is the FULL
decomposition (discriminating + redundant/unsupported for new-set; Traces for q100), so the
verification agent can independently check each hop for support and necessity.
"""
import json, os

BASE = "artifacts/bcp_stage1"
MP = f"{BASE}/merged/projections"
OUT = f"{BASE}/stage2/inputs"; os.makedirs(OUT, exist_ok=True)

P = [json.loads(l)["record_id"] for l in open(f"{BASE}/merged/verdicts.jsonl")
     if json.loads(l)["verdict"] == "PROJECTABLE"]

gold = {}
for q in P:
    d = json.load(open(f"{MP}/bcp_{q}.json"))
    if "discriminating_hops" in d:
        hops = [(h.get("clue") or "") for h in (d.get("discriminating_hops") or [])]
        hops += [(h.get("clue") or "") for h in (d.get("redundant_or_unsupported_hops") or [])]
    else:  # q100 Traces schema
        hops = [(t.get("clue") or "") for t in (d.get("Traces") or [])]
    hops = [h for h in hops if h.strip()]
    gold[q] = d.get("answer", "")
    json.dump({"qid": q, "question": d.get("question", ""), "answer": d.get("answer", ""),
               "n_hops": len(hops), "hops": hops},
              open(f"{OUT}/{q}.json", "w"), ensure_ascii=False)

json.dump(gold, open(f"{BASE}/stage2/gold_map.json", "w"), ensure_ascii=False)
print(f"wrote {len(P)} scrutiny inputs to {OUT} (median hops shown below)")
import statistics
print("hop counts: min %d med %d max %d" % (
    min(len(json.load(open(f'{OUT}/{q}.json'))['hops']) for q in P),
    int(statistics.median([len(json.load(open(f'{OUT}/{q}.json'))['hops']) for q in P])),
    max(len(json.load(open(f'{OUT}/{q}.json'))['hops']) for q in P)))
