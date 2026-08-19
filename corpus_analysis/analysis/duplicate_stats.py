"""Exact + near-duplicate statistics at J >= 0.7 / 0.8 / 0.9, vectorised.

All three thresholds derive from the single 0.7 record set by filtering on the stored exact
Jaccard, so they are mutually consistent by construction and need no extra corpus pass.

Note the direction of completeness: LSH candidate recall RISES with the threshold (0.9981 at
0.7, ~0.99999997 at 0.8, ~1.0 at 0.9), so the stricter figures are the more complete ones,
not the less.

The earlier version of this looped over 218M rows in Python and needed hours; flattening the
list column and counting with numpy does the same work in minutes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collections
import json
import time

import numpy as np
import pyarrow.compute as pc
import pyarrow.dataset as ds

O = "/store/collections/climbmix-400b-shuffle-official-corpus_analysis"
ND = f"{O}/near_dup"          # records live under near_dup/, group list at the analysis root
THR = (0.7, 0.8, 0.9)
CORPUS = 553240576
BK = [(1, 1), (2, 4), (5, 9), (10, 99), (100, 10 ** 9)]


def main():
    docs = {t: 0 for t in THR}
    edges = {t: 0 for t in THR}
    nb = {t: collections.Counter() for t in THR}
    jh = np.zeros(102, dtype=np.int64)
    recs = 0

    d = ds.dataset(f"{ND}/doc_duplicates_t70", format="parquet")
    t0 = time.time()
    for i, b in enumerate(d.to_batches(columns=["near_duplicates"], batch_size=200_000)):
        col = b.column("near_duplicates")
        recs += len(col)
        if len(col) == 0:
            continue
        j = pc.list_flatten(col).field("jaccard").to_numpy(zero_copy_only=False)
        if j.size == 0:
            continue
        par = pc.list_parent_indices(col).to_numpy(zero_copy_only=False)
        jh += np.bincount(np.minimum((j * 100).astype(np.int32), 101), minlength=102)
        for t in THR:
            m = j >= t
            if not m.any():
                continue
            cnt = np.bincount(par[m])
            cnt = cnt[cnt > 0]                  # neighbours per qualifying document
            edges[t] += int(cnt.sum())
            docs[t] += int(cnt.size)
            for lo, hi in BK:
                nb[t][(lo, hi)] += int(((cnt >= lo) & (cnt <= hi)).sum())
        if i % 200 == 0:
            print(f"  {recs:,} records  {time.time()-t0:.0f}s", flush=True)

    near = {}
    for t in THR:
        near[str(t)] = {
            "documents_with_near_duplicates": docs[t],
            "share_of_corpus": round(docs[t] / CORPUS, 5),
            "undirected_pairs": edges[t] // 2,
            "mean_neighbours": round(edges[t] / docs[t], 3) if docs[t] else 0,
            "neighbours_by_bucket": {
                (f"{lo}-{hi}" if hi < 10 ** 9 else f"{lo}+"): nb[t][(lo, hi)] for lo, hi in BK},
        }
        print(f"J>={t}: {docs[t]:,} docs ({docs[t]/CORPUS*100:.2f}%), "
              f"{edges[t]//2:,} pairs, mean {near[str(t)]['mean_neighbours']} nbrs", flush=True)

    gs = collections.Counter()
    with open(f"{O}/duplicate_groups.jsonl") as fh:
        for line in fh:
            gs[len(json.loads(line)["doc_ids"])] += 1
    sz = np.array(sorted(gs))
    ct = np.array([gs[int(s)] for s in sz])
    exact = {
        "groups": int(ct.sum()), "documents": int((sz * ct).sum()),
        "redundant_copies": int(((sz - 1) * ct).sum()), "largest_group": int(sz.max()),
        "by_group_size": {
            (f"{lo}-{hi}" if hi < 10 ** 9 else f"{lo}+"): {
                "groups": int(ct[(sz >= lo) & (sz <= hi)].sum()),
                "documents": int((sz * ct)[(sz >= lo) & (sz <= hi)].sum())}
            for lo, hi in [(2, 2), (3, 4), (5, 9), (10, 99), (100, 999), (1000, 10 ** 9)]},
    }
    print("exact:", json.dumps(exact["by_group_size"]), flush=True)

    json.dump({"corpus_documents": CORPUS, "records_in_file": recs,
               "exact": exact, "near": near,
               "jaccard_histogram_pct": {str(k): int(jh[k]) for k in range(70, 102) if jh[k]}},
              open(f"{O}/corpus_duplicate_stats.json", "w"), indent=2)
    print(f"\nwrote corpus_duplicate_stats.json in {(time.time()-t0)/60:.1f}m", flush=True)


if __name__ == "__main__":
    main()
