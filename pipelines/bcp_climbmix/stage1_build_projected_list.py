#!/usr/bin/env python3
"""Stage 4 (final) of the projection pipeline: the canonical list of projected questions.

Reads every q_all/projections/bcp_<qid>.json (stage1_project.js output), keeps verdict==PROJECTABLE,
and writes:
  merged/projected_questions.jsonl : one row/question
    {record_id, question, answer, n_hops, hops:[{clue, corpus_evidence:[{doc_id, snippet}]}], support_doc_ids}
  merged/projected_questions.md    : human index (qid, question, answer, #support docs)
"""
import json, os

TOOLS = os.path.dirname(os.path.abspath(__file__))
QALL = os.path.dirname(TOOLS)
BASE = os.path.dirname(QALL)
PROJ = os.path.join(QALL, "projections")
OUT = os.path.join(BASE, "merged"); os.makedirs(OUT, exist_ok=True)

rows = []
for f in os.listdir(PROJ):
    if not f.endswith(".json"):
        continue
    d = json.load(open(os.path.join(PROJ, f)))
    if d.get("verdict") != "PROJECTABLE":
        continue
    hops = d.get("discriminating_hops") or d.get("hops") or d.get("Traces") or []
    clean_hops, doc_ids = [], []
    for h in hops:
        ev = []
        for e in (h.get("corpus_evidence") or []):
            did = str(e.get("doc_id", "")).strip()
            if not did:
                continue
            doc_ids.append(did)
            ev.append({"doc_id": did, "snippet": e.get("snippet", "")})
        if h.get("clue"):
            clean_hops.append({"clue": h.get("clue", ""), "corpus_evidence": ev})
    rows.append({"record_id": str(d.get("record_id", f[4:-5])), "question": d.get("question", ""),
                 "answer": d.get("answer", ""), "n_hops": len(clean_hops), "hops": clean_hops,
                 "support_doc_ids": list(dict.fromkeys(doc_ids))})

rows.sort(key=lambda r: int(r["record_id"]))
with open(os.path.join(OUT, "projected_questions.jsonl"), "w") as fo:
    for r in rows:
        fo.write(json.dumps(r, ensure_ascii=False) + "\n")

md = [f"# Projected questions — {len(rows)} BrowseComp-plus questions supported on ClimbMix", "",
      "Questions whose answer is uniquely derivable from a chain of hops each grounded in ClimbMix docs.",
      "Full per-question hops + supporting doc-ids/snippets: `projected_questions.jsonl`.",
      "", "| qid | question | answer | #support docs |", "|---|---|---|---:|"]
for r in rows:
    q = r["question"].replace("|", " ").replace("\n", " ")[:90]
    a = str(r["answer"]).replace("|", " ")[:40]
    md.append(f"| {r['record_id']} | {q} | {a} | {len(r['support_doc_ids'])} |")
open(os.path.join(OUT, "projected_questions.md"), "w").write("\n".join(md) + "\n")
print(f"wrote merged/projected_questions.jsonl + .md : {len(rows)} projected questions")
