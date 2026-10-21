#!/usr/bin/env python3
"""Total Recall QA (TRQA) source data: load the test queries (and, for the expansion, the qrels) and decrypt
them in memory.

  python3 trqa_source.py download --out DIR      # raw HF files: test_queries_<subset>.jsonl, test_qrels_<subset>.jsonl

TRQA (Hugging Face `mahtaa/trqa`) canary-encrypts `question`, `answer` and the qrels' `doc_id`:
base64(plaintext XOR keystream), keystream = sha256(<canary>) repeated (period 32), where <canary> is the
dataset's own `canary` field (it starts with "trqa:"). The canary is read from each row, or from the
environment variable TRQA_CANARY when that is set; it is never written in this code. Base64 decoding alone
gives plausible but WRONG text: always XOR-decrypt.

Two loaders, both used in the real run:
  - `load_queries_hf`  the `datasets` loader, config "queries", splits <subset>_test (Stage 0 task construction,
                       private repo artifacts/trqa/tools/build_trqa_tasks.py)
  - `load_raw`         the raw JSONL files downloaded by `download` (the expansion's universe, private repo
                       artifacts/projection_audit/tools/trqa_hf.py), which also carry the qrels
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import urllib.request
from collections import defaultdict
from pathlib import Path

HF_DATASET = "mahtaa/trqa"
SUBSETS = ("wiki1", "wiki2", "ecommerce")
HF_SPLITS = {"wiki1": "wiki1_test", "wiki2": "wiki2_test", "ecommerce": "ecommerce_test"}
RAW_URL = "https://huggingface.co/datasets/mahtaa/trqa/resolve/main/data/{sub}/test_{kind}_{sub}.jsonl"
CANARY_ENV = "TRQA_CANARY"


def key_for(row: dict | None) -> bytes:
    """The period-32 keystream: sha256 of the canary (from $TRQA_CANARY, else the row's own canary field)."""
    canary = os.environ.get(CANARY_ENV) or ((row or {}).get("canary") or "")
    if not canary:
        raise SystemExit(f"no TRQA canary: the rows carry none and ${CANARY_ENV} is not set")
    return hashlib.sha256(canary.encode()).digest()


def decrypt(value, key: bytes):
    """XOR-decrypt one base64 field; non-strings, empty strings and non-base64 values pass through unchanged
    (as in the original task builder)."""
    if not isinstance(value, str) or not value:
        return value
    try:
        c = base64.b64decode(value)
    except Exception:
        return value
    return bytes(c[i] ^ key[i % 32] for i in range(len(c))).decode("utf-8", "replace")


def stem(subset: str, rid: str) -> str:
    """The question's record id in every CMASS file: <subset>__<TRQA id>."""
    return f"{subset}__{rid}".replace("/", "_").replace(" ", "_")


def load_queries_hf(subset: str) -> list[dict]:
    """Decrypted test queries of one subset via `datasets` (needs network and the `datasets` package)."""
    from datasets import load_dataset  # noqa: PLC0415  (only Stage 0 needs it)
    ds = load_dataset(HF_DATASET, "queries", split=HF_SPLITS[subset])
    out = []
    for r in ds:
        r = dict(r)
        key = key_for(r)
        out.append({"id": str(r["id"]), "question": decrypt(r.get("question", ""), key),
                    "answer": decrypt(r.get("answer"), key),
                    "datatype": r.get("datatype") if r.get("datatype") is not None else r.get("src")})
    return out


def load_raw(raw_dir: Path, subset: str, with_qrels: bool = False):
    """Decrypted queries of one subset from the raw files (and {query id: [decrypted qrel doc ids]})."""
    raw_dir = Path(raw_dir)
    rows = [json.loads(l) for l in open(raw_dir / f"test_queries_{subset}.jsonl") if l.strip()]
    key = key_for(rows[0] if rows else None)
    queries = [{"id": str(r["id"]), "question": decrypt(r.get("question", ""), key),
                "answer": decrypt(r.get("answer"), key),
                "datatype": r.get("datatype") if r.get("datatype") is not None else r.get("src")} for r in rows]
    if not with_qrels:
        return queries
    qrels = defaultdict(list)
    qp = raw_dir / f"test_qrels_{subset}.jsonl"
    if qp.exists():
        for line in open(qp):
            if line.strip():
                x = json.loads(line)
                qrels[str(x["query_id"])].append(decrypt(x["doc_id"], key))
    return queries, qrels


def download(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for s in SUBSETS:
        for kind in ("queries", "qrels"):
            dst = out / f"test_{kind}_{s}.jsonl"
            urllib.request.urlretrieve(RAW_URL.format(sub=s, kind=kind), dst)
            print("saved", dst)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--out", required=True, help="directory for the raw files (keep it out of any public repository)")
    a = p.parse_args()
    if a.cmd == "download":
        download(Path(a.out))


if __name__ == "__main__":
    main()
