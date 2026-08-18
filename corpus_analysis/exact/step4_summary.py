"""Step 4 -- reconcile the artifacts against each other and write summary.json.

The point is to catch a silently truncated or mis-grouped output, so the counts are
re-derived by scanning the written files rather than trusted from the stage that wrote
them.  The big files are counted by byte scanning (no JSON parse); only
duplicate_groups.jsonl, which is far smaller, is parsed line by line.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import time

from common import OUT_DIR, human, load_manifest, log

CHUNK = 1 << 24


def scan_counts(path, needles):
    """Count occurrences of each needle across a file read in chunks.

    A needle can straddle a read boundary, so each window carries the previous window's
    last maxlen-1 bytes.  The subtlety is that counting the whole window would then
    double-count any needle lying entirely inside that carry, so a match is counted only
    when its *start* index is at or before W - maxlen; anything starting later belongs to
    the carry and is counted on the next pass, or after the loop for the final tail.
    """
    counts = {k: 0 for k in needles}
    maxlen = max(len(k) for k in needles)
    carry = b""
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(CHUNK)
            if not buf:
                break
            window = carry + buf
            w = len(window)
            if w < maxlen:
                carry = window
                continue
            for k in needles:
                # region length w - maxlen + len(k)  =>  matches with start <= w - maxlen
                counts[k] += window[: w - maxlen + len(k)].count(k)
            carry = window[w - maxlen + 1:]
    for k in needles:
        counts[k] += carry.count(k)
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    t0 = time.time()
    man = load_manifest(args.out_dir)
    total = int(man["offsets"][-1])
    P = lambda name: os.path.join(args.out_dir, name)

    log("stage4: scanning length_to_doc_ids.json")
    c = scan_counts(P("length_to_doc_ids.json"), [b'"shard_', b'": ['])
    n_ids_len, n_lengths = c[b'"shard_'], c[b'": [']

    log("stage4: scanning hash_to_doc_ids.jsonl")
    c = scan_counts(P("hash_to_doc_ids.jsonl"), [b'"shard_', b"\n"])
    n_ids_hash, n_hash_lines = c[b'"shard_'], c[b"\n"]

    log("stage4: parsing duplicate_groups.jsonl")
    n_groups = n_docs = redundant_docs = redundant_chars = 0
    largest = []
    with open(P("duplicate_groups.jsonl"), "rb") as fh:
        for line in fh:
            g = json.loads(line)
            cnt, ln = g["count"], g["length"]
            n_groups += 1
            n_docs += cnt
            redundant_docs += cnt - 1
            redundant_chars += ln * (cnt - 1)
            if len(largest) < args.top or cnt > largest[-1][0]:
                largest.append((cnt, ln, g["sha256"], g["doc_ids"][:5]))
                largest.sort(key=lambda t: -t[0])
                del largest[args.top:]

    n_failed = sum(1 for _ in open(P("verification_failures.jsonl"), "rb"))
    dup_stats = json.load(open(P("dup_stats.json")))
    verify_stats = json.load(open(P("verify_stats.json")))

    checks = {
        "length_json_ids_eq_total": n_ids_len == total,
        "hash_jsonl_ids_eq_total": n_ids_hash == total,
        "hash_jsonl_lines_eq_distinct_sha256": n_hash_lines == dup_stats["distinct_sha256"],
        "verified_groups_eq_stage2b_dup_groups":
            n_groups + n_failed == dup_stats["duplicate_groups"],
        "verified_docs_eq_stage2b_dup_docs":
            verify_stats["documents_compared"] == dup_stats["duplicate_docs"],
        "no_verification_failures": n_failed == 0,
    }

    summary = {
        "corpus_dir": man["corpus_dir"],
        "shards": man["n_shards"],
        "total_documents": total,
        "distinct_lengths": n_lengths,
        "distinct_sha256": dup_stats["distinct_sha256"],
        "duplicate_groups_confirmed": n_groups,
        "duplicate_groups_failed_verification": n_failed,
        "documents_in_duplicate_groups": n_docs,
        "redundant_documents": redundant_docs,
        "redundant_document_rate": redundant_docs / total if total else 0.0,
        "redundant_characters": redundant_chars,
        "largest_groups": [
            {"count": c, "length": l, "sha256": h, "doc_ids_sample": d}
            for c, l, h, d in largest
        ],
        "artifact_bytes": {
            n: os.path.getsize(P(n)) for n in
            ["length_to_doc_ids.json", "hash_to_doc_ids.jsonl",
             "duplicate_groups.jsonl", "verification_failures.jsonl"]
        },
        "checks": checks,
    }
    with open(P("summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)

    print(f"\n{'='*68}\nClimbMix-400B exact-duplicate summary\n{'='*68}")
    print(f"  documents              {total:,}")
    print(f"  distinct sha256        {dup_stats['distinct_sha256']:,}")
    print(f"  distinct lengths       {n_lengths:,}")
    print(f"  duplicate groups       {n_groups:,} confirmed, {n_failed:,} failed")
    print(f"  docs in those groups   {n_docs:,}")
    print(f"  redundant docs         {redundant_docs:,} "
          f"({100*redundant_docs/total:.2f}% of corpus)")
    print(f"  redundant characters   {redundant_chars:,}")
    for name, size in summary["artifact_bytes"].items():
        print(f"  {name:<32} {human(size)}")
    print(f"{'-'*68}")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print(f"{'='*68}\n")

    log(f"stage4 done in {(time.time()-t0)/60:.1f}m -> {P('summary.json')}")
    if not all(checks.values()):
        raise SystemExit("one or more consistency checks FAILED")


if __name__ == "__main__":
    main()
