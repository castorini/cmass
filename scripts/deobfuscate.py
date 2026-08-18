#!/usr/bin/env python3
"""De-obfuscate the CMASS BrowseComp-plus -> ClimbMix release.

Obfuscated fields (``id``, ``question``, ``answer`` in the queries config; ``query_id``, ``doc_id`` in the qrels config)
are base64( plaintext XOR keystream ), where the keystream is the SHA-256 digest of the row's
``canary`` string repeated: key = sha256(canary.encode()).digest(); pt[i] = ct[i] ^ key[i % 32].
This is the same scheme used by BrowseComp-style releases; it exists to keep the plain text out of
web crawls and pre-training corpora, not to be cryptographically strong.

Usage:
  python3 deobfuscate.py queries.jsonl > queries_plain.jsonl
  python3 deobfuscate.py qrels.jsonl   > qrels_plain.jsonl

or with the datasets library:

  from datasets import load_dataset
  from deobfuscate import decode_field
  ds = load_dataset("<repo>", "queries")
  row = ds["test"][0]
  question = decode_field(row["question"], row["canary"])
"""
import base64
import hashlib
import json
import sys

OBFUSCATED_FIELDS = ("id", "question", "answer", "query_id", "doc_id")


def decode_field(value: str, canary: str) -> str:
    key = hashlib.sha256(canary.encode("utf-8")).digest()
    raw = base64.b64decode(value)
    return bytes(c ^ key[i % len(key)] for i, c in enumerate(raw)).decode("utf-8")


def decode_row(row: dict) -> dict:
    canary = row.get("canary")
    if not canary:
        return row
    out = dict(row)
    for field in OBFUSCATED_FIELDS:
        if field in out and isinstance(out[field], str):
            out[field] = decode_field(out[field], canary)
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    with open(sys.argv[1], encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                print(json.dumps(decode_row(json.loads(line)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
