#!/usr/bin/env python3
"""Stage 0, task construction for BrowseComp-Plus: one task per question of the test split.

  python3 stage0_tasks.py --run RUN --set bcp                       # Tevatron/browsecomp-plus, split test
  python3 stage0_tasks.py --run RUN --set bcp --source-jsonl FILE   # the same columns from a local JSONL file

Writes tasks/<set>.jsonl (Run.task_file), one line per question in dataset order:

  {"record_id": <query_id>, "source": <query_id>, "dataset": "browsecomp-plus",
   "question": ..., "answer": ..., "answer_type": "single",
   "hints": [{"doc_id", "title", "text"}, ...]}

`hints` are BrowseComp-Plus's own gold_docs and evidence_docs for the question, in that order, deduplicated
on (docid, md5 of the text): the "provided_docs" the projection agent reads to recover the intended reasoning
chain. They are BrowseComp-Plus web pages, not ClimbMix documents, and never count as grounding.

The dataset stores every field except query_id XOR-encrypted (base64 of plaintext XOR sha256(canary)
repeated). The canary is read from the environment variable named by --canary-env (default
BROWSECOMP_PLUS_CANARY); it is printed on the dataset card and never written here. A value that is not
base64 or does not decode to UTF-8 is kept as is, as the original decryption did.

Ported from the private q_all/tools/build_all_tasks.py, which streamed the split with only these five
columns selected (the negative_docs column is large and unused) and wrote one task file per question.
The source column format is read through `datasets` at run time; nothing is vendored.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from cmass.run import Run, write_jsonl  # noqa: E402

COLUMNS = ["query_id", "query", "answer", "gold_docs", "evidence_docs"]


def xor_key(password: str, length: int) -> bytes:
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return digest * (length // len(digest)) + digest[: length % len(digest)]


def decrypt_string(value: str, password: str) -> str:
    try:
        encrypted = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return value
    if not encrypted:
        return value
    plain = bytes(a ^ b for a, b in zip(encrypted, xor_key(password, len(encrypted))))
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return value


def decrypt_value(value, password: str):
    if isinstance(value, str):
        return decrypt_string(value, password)
    if isinstance(value, list):
        return [decrypt_value(v, password) for v in value]
    if isinstance(value, dict):
        return {k: (v if k == "query_id" else decrypt_value(v, password)) for k, v in value.items()}
    return value


def decrypt_row(row: dict, password: str) -> dict:
    return {k: (v if k == "query_id" else decrypt_value(v, password)) for k, v in row.items()}


def task(dec: dict) -> dict:
    qid = str(dec.get("query_id"))
    docs, seen = [], set()
    for fld in ("gold_docs", "evidence_docs"):
        for d in dec.get(fld) or []:
            if not isinstance(d, dict):
                continue
            did = str(d.get("docid") or "").strip()
            txt = d.get("text") or ""
            k = (did, hashlib.md5(txt.encode("utf-8", "replace")).hexdigest())
            if not did or k in seen:
                continue
            seen.add(k)
            docs.append({"doc_id": did, "title": d.get("url", ""), "text": txt})
    return {"record_id": qid, "source": qid, "dataset": "browsecomp-plus",
            "question": dec.get("query", ""), "answer": dec.get("answer", ""), "answer_type": "single",
            "hints": docs}


def rows_from_hf(name: str, split: str):
    from datasets import load_dataset          # imported here so --source-jsonl needs no third-party package
    return load_dataset(name, split=split, streaming=True).select_columns(COLUMNS)


def rows_from_jsonl(path: Path):
    with open(path) as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                yield {k: r.get(k) for k in COLUMNS}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", help="run directory (default: $CMASS_RUN or the current directory)")
    ap.add_argument("--set", required=True, help="the set id in cmass.json, e.g. bcp")
    ap.add_argument("--dataset", default="Tevatron/browsecomp-plus")
    ap.add_argument("--split", default="test")
    ap.add_argument("--source-jsonl", type=Path, help="read rows with the dataset's columns from this file instead")
    ap.add_argument("--canary-env", default="BROWSECOMP_PLUS_CANARY",
                    help="environment variable holding the decryption canary (default BROWSECOMP_PLUS_CANARY)")
    ap.add_argument("--limit", type=int, help="stop after this many questions (a trial run)")
    a = ap.parse_args()
    password = os.environ.get(a.canary_env, "")
    if not password:
        sys.exit(f"set ${a.canary_env} to the BrowseComp-Plus canary (printed on the dataset card); "
                 "it is never stored in this repository")
    run = Run(a.run)
    rows = rows_from_jsonl(a.source_jsonl) if a.source_jsonl else rows_from_hf(a.dataset, a.split)
    tasks, ids = [], set()
    for row in rows:
        t = task(decrypt_row(row, password))
        if t["record_id"] in ids:
            sys.exit(f"duplicate query_id {t['record_id']}")
        ids.add(t["record_id"])
        tasks.append(t)
        if a.limit and len(tasks) >= a.limit:
            break
    empty = sum(1 for t in tasks if not t["question"] or not t["answer"])
    out = run.task_file(a.set)
    write_jsonl(out, tasks)
    print(f"wrote {len(tasks)} tasks to {out} ({empty} without question or answer; "
          f"{sum(len(t['hints']) for t in tasks)} hint documents)")


if __name__ == "__main__":
    main()
