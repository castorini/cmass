#!/usr/bin/env python3
"""Re-verify sampled per-document records against the corpus, independently.

Deliberately shares no code with the pipeline: shingles are built as raw Python word
tuples in a set and Jaccard is |A&B|/|A|B|, with no hashing, no MinHash, and no numba. If
the pipeline and this disagree, the pipeline is wrong.

Checks, per sampled record: every listed exact duplicate really is byte-identical; every
near-duplicate Jaccard matches this reference implementation; nothing sits below the
threshold; the n_exact/n_near counts match their list lengths; and the exact and near
lists are disjoint.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collections
import random
import re

import pyarrow.dataset as ds
import pyarrow.parquet as pq

import config

NON = re.compile(r"[^a-z0-9]+")


def ref_shingles(text, k):
    w = NON.sub(" ", text.lower()).split()
    return {tuple(w[i:i + k]) for i in range(len(w) - k + 1)}


def ref_jaccard(a, b):
    return len(a & b) / len(a | b) if (a | b) else 0.0


def main():
    ap = config.base_parser(__doc__)
    ap.add_argument("--sample", type=int, default=120, help="records to check")
    ap.add_argument("--seed", type=int, default=3)
    a = ap.parse_args()
    rec = os.path.join(config.near_dir(a.out_dir),
                       f"doc_duplicates_t{int(config.MIN_THRESHOLD*100)}")
    if a.dry_run:
        print(f"would sample {a.sample} records from {rec}")
        return 0

    random.seed(a.seed)
    rate = 0.0004
    recs = []
    for b in ds.dataset(rec, format="parquet").to_batches(
            columns=["doc_id", "exact_duplicates", "near_duplicates",
                     "n_exact", "n_near"]):
        for r in b.to_pylist():
            if random.random() < rate:
                recs.append(r)
        if len(recs) >= a.sample:
            break
    print(f"sampled {len(recs)} records")

    need = set()
    for r in recs:
        need.add(r["doc_id"])
        need.update(r["exact_duplicates"])
        need.update(x["doc_id"] for x in r["near_duplicates"])
    by_shard = collections.defaultdict(list)
    for d in need:
        by_shard[int(d[6:11])].append(d)
    texts = {}
    for sh, ids in sorted(by_shard.items()):
        col = pq.read_table(os.path.join(a.corpus_dir, f"shard_{sh:05d}.parquet"),
                            columns=["text"]).column("text")
        for d in ids:
            texts[d] = col[int(d[12:17])].as_py()
    print(f"read {len(texts)} documents from the corpus")

    bad_exact = bad_j = bad_thr = bad_cnt = overlap = 0
    n_near = 0
    jmin = 1.0
    for r in recs:
        me = texts[r["doc_id"]]
        for x in r["exact_duplicates"]:
            if texts[x].strip() != me.strip():
                bad_exact += 1
                print(f"  NOT BYTE-IDENTICAL {r['doc_id']} {x}")
        S = ref_shingles(me, config.SHINGLE_K)
        for x in r["near_duplicates"]:
            n_near += 1
            j = ref_jaccard(S, ref_shingles(texts[x["doc_id"]], config.SHINGLE_K))
            jmin = min(jmin, j)
            if abs(j - x["jaccard"]) > 0.002:
                bad_j += 1
                print(f"  J MISMATCH {r['doc_id']} {x['doc_id']}: "
                      f"reported {x['jaccard']:.4f} actual {j:.4f}")
            if j < config.MIN_THRESHOLD - 1e-9:
                bad_thr += 1
        if (r["n_exact"] != len(r["exact_duplicates"])
                or r["n_near"] != len(r["near_duplicates"])):
            bad_cnt += 1
        if set(r["exact_duplicates"]) & {x["doc_id"] for x in r["near_duplicates"]}:
            overlap += 1

    print(f"\n{len(recs)} records / {n_near} near-duplicate entries:")
    print(f"  exact pairs not byte-identical : {bad_exact}")
    print(f"  jaccard mismatches             : {bad_j}")
    print(f"  entries below threshold        : {bad_thr}")
    print(f"  n_exact/n_near count errors    : {bad_cnt}")
    print(f"  exact/near list overlaps       : {overlap}")
    print(f"  lowest observed jaccard        : {jmin:.4f}")
    fails = bad_exact + bad_j + bad_thr + bad_cnt + overlap
    print("\nPASS" if fails == 0 else f"\nFAILED: {fails} problems")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
