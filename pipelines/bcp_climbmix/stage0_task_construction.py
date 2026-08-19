#!/usr/bin/env python3
"""Build per-record task files for ALL BrowseComp-plus records (q+a+qrel docs),
via column-projected streaming (skips the bulky negative_docs) + deobfuscation.

RESUMABLE: skips any record whose task file already exists. Re-run freely.

Output: q_all/subagent_tasks/bcp_<qid>.task.json  (record_id, question, answer,
provided_docs = gold_docs ∪ evidence_docs).
"""
import json, os, hashlib, sys
from datasets import load_dataset
from data.datasets import decrypt_browsecomp_plus_row

OUT = "/Users/lingweigu/Research/agent-plus/artifacts/bcp_stage1/q_all/subagent_tasks"
os.makedirs(OUT, exist_ok=True)
# resumable: skip any record whose task file already exists in OUT
have = set(f for f in os.listdir(OUT)) if os.path.isdir(OUT) else set()

ds = (load_dataset("Tevatron/browsecomp-plus", split="test", streaming=True)
      .select_columns(["query_id", "query", "answer", "gold_docs", "evidence_docs"]))

built = skipped = 0
for row in ds:
    dec = decrypt_browsecomp_plus_row(row)
    qid = str(dec.get("query_id"))
    fn = f"bcp_{qid}.task.json"
    if fn in have or os.path.exists(f"{OUT}/{fn}"):
        skipped += 1
        continue
    docs, seen = [], set()
    for fld in ("gold_docs", "evidence_docs"):
        for d in (dec.get(fld) or []):
            if not isinstance(d, dict):
                continue
            did = str(d.get("docid") or "").strip()
            txt = d.get("text") or ""
            k = (did, hashlib.md5(txt.encode("utf-8", "replace")).hexdigest())
            if not did or k in seen:
                continue
            seen.add(k)
            docs.append({"doc_id": did, "text": txt, "title": d.get("url", "")})
    task = {"dataset_name": "BrowseComp-plus", "record_id": qid,
            "question": dec.get("query", ""), "answer": dec.get("answer", ""),
            "provided_docs": docs}
    json.dump(task, open(f"{OUT}/{fn}", "w"), ensure_ascii=False)
    built += 1
    if built % 50 == 0:
        print(f"... built {built}", flush=True)

print(f"DONE: built {built}, skipped {skipped}, total task files now {len(os.listdir(OUT))}")
