"""Near-dup step 3 -- LSH buckets -> candidate connected components.

Processes one band at a time: mmap that band's keys across all shards into a single
uint64 array, radix-sort it, and union every run of equal keys. Peak memory is one band
(~4.3 GB of keys + 4.3 GB sort index + 2.2 GB union-find), not the whole 555 GB signature
store.

Components here are *candidates only*. Union-find is single-linkage, so a component
guarantees nothing more than a chain of shared band keys -- it is deliberately generous.
Stage 4 computes exact all-pairs Jaccard inside each component and partitions it into
cliques, which is where the actual similarity guarantee comes from.

Buckets above --max-bucket are skipped and reported rather than unioned: a single band key
shared by that many documents is a template flood, and merging it wholesale would fuse an
enormous component that stage 4 could never verify pairwise.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import time

import numpy as np
from numba import njit

from common import OUT_DIR, human, load_manifest, log
from near.nd_common import ND_DIR


@njit(cache=True)
def _find(parent, x):
    root = x
    while parent[root] != root:
        root = parent[root]
    while parent[x] != root:          # path compression
        parent[x], x = root, parent[x]
    return root


@njit(cache=True)
def _union_runs(parent, size, order, keys, max_bucket):
    """Union each run of equal sorted keys. Returns (n_buckets, n_unions, n_skipped)."""
    n = order.shape[0]
    i = 0
    nb = nu = nskip = 0
    while i < n:
        j = i + 1
        while j < n and keys[j] == keys[i]:
            j += 1
        k = j - i
        if k > 1:
            nb += 1
            if k > max_bucket:
                nskip += 1
            else:
                ra = _find(parent, order[i])
                for t in range(i + 1, j):
                    rb = _find(parent, order[t])
                    if ra != rb:
                        if size[ra] < size[rb]:
                            ra, rb = rb, ra
                        parent[rb] = ra
                        size[ra] += size[rb]
                        nu += 1
        i = j
    return nb, nu, nskip


@njit(cache=True, inline="always")
def _link(parent, size, a, b):
    ra = _find(parent, a)
    rb = _find(parent, b)
    if ra == rb:
        return 0
    if size[ra] < size[rb]:
        ra, rb = rb, ra
    parent[rb] = ra
    size[ra] += size[rb]
    return 1


@njit(cache=True)
def _union_runs_prefiltered(parent, size, order, keys, max_bucket, psig, min_match, small):
    """As above, but a pair may only merge if its MinHash-estimated Jaccard clears the bar.

    Sharing one band key is weak evidence: two unrelated documents that happen to collide
    fuse their groups permanently, and repeated collisions are what chained 155,009
    unrelated documents into a single component in the un-prefiltered run. Components that
    outgrow the verification cap get skipped wholesale, so that chaining destroys real
    duplicates -- it does not merely add noise.

    The gate counts matching MinHash values over ``psig``'s permutations, which is an
    unbiased Jaccard estimator, and requires at least ``min_match`` of them. It is set far
    below the 0.7 output threshold (a true-0.7 pair sits ~5 sigma above it at 64 perms), so
    it is generous towards anything real while rejecting most low-similarity junk.

    Small buckets are prefiltered pairwise; larger ones anchor on the first member to stay
    linear. Nothing here decides the output -- exact Jaccard still does that in nd4/nd7.
    """
    n = order.shape[0]
    P = psig.shape[1]
    i = 0
    nb = nu = nskip = nrej = 0
    while i < n:
        j = i + 1
        while j < n and keys[j] == keys[i]:
            j += 1
        k = j - i
        if k > 1:
            nb += 1
            if k > max_bucket:
                nskip += 1
            elif k <= small:
                for a in range(i, j):
                    ia = order[a]
                    for b in range(a + 1, j):
                        ib = order[b]
                        c = 0
                        for t in range(P):
                            if psig[ia, t] == psig[ib, t]:
                                c += 1
                        if c >= min_match:
                            nu += _link(parent, size, ia, ib)
                        else:
                            nrej += 1
            else:
                ia = order[i]
                for b in range(i + 1, j):
                    ib = order[b]
                    c = 0
                    for t in range(P):
                        if psig[ia, t] == psig[ib, t]:
                            c += 1
                    if c >= min_match:
                        nu += _link(parent, size, ia, ib)
                    else:
                        nrej += 1
        i = j
    return nb, nu, nskip, nrej


@njit(cache=True)
def _flatten(parent):
    for i in range(parent.shape[0]):
        parent[i] = _find(parent, i)
    return parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--nd-dir", default=ND_DIR)
    ap.add_argument("--max-bucket", type=int, default=50000)
    ap.add_argument("--prefilter-threshold", type=float, default=0.0,
                    help="estimated-Jaccard gate on merging; 0 disables. Set well below the "
                         "0.7 output threshold (0.4 is ~5 sigma clear at 64 perms).")
    ap.add_argument("--prefilter-small", type=int, default=16,
                    help="buckets up to this size are prefiltered pairwise, larger anchor "
                         "on their first member")
    args = ap.parse_args()

    t0 = time.time()
    man = load_manifest(args.out_dir)
    nums = [s["num"] for s in man["shards"]]
    nd = args.nd_dir

    # global doc index = concatenation of per-shard representative lists, in shard order
    counts = np.array([np.load(os.path.join(nd, "bandkeys", f"shard_{n:05d}.npy"),
                               mmap_mode="r").shape[1] for n in nums], dtype=np.int64)
    offs = np.concatenate([[0], np.cumsum(counts)])
    N = int(offs[-1])
    n_bands = np.load(os.path.join(nd, "bandkeys", f"shard_{nums[0]:05d}.npy"),
                      mmap_mode="r").shape[0]
    log(f"nd3: {N:,} documents, {n_bands} bands, max_bucket={args.max_bucket:,}")
    np.save(os.path.join(nd, "shard_offsets.npy"), offs)

    psig = None
    min_match = 0
    if args.prefilter_threshold > 0:
        pf_dir = os.path.join(nd, "prefilter")
        if not os.path.isdir(pf_dir):
            raise SystemExit("--prefilter-threshold set but no prefilter/ dir; "
                             "rerun nd2_band.py with --prefilter-perms 64")
        P = np.load(os.path.join(pf_dir, f"shard_{nums[0]:05d}.npy"), mmap_mode="r").shape[1]
        min_match = int(np.ceil(args.prefilter_threshold * P))
        log(f"nd3: loading prefilter signatures ({P} perms, "
            f"{human(N * P * 4)}) -- gate at estimated J >= "
            f"{args.prefilter_threshold} ({min_match}/{P} matches)")
        psig = np.empty((N, P), dtype=np.uint32)
        for i, n in enumerate(nums):
            psig[offs[i]:offs[i + 1]] = np.load(
                os.path.join(pf_dir, f"shard_{n:05d}.npy"))
        se = (args.prefilter_threshold * (1 - args.prefilter_threshold) / P) ** 0.5
        log(f"  estimator sd ~{se:.3f}; a true-0.7 pair is "
            f"{(0.7 - args.prefilter_threshold) / ((0.7*0.3/P)**0.5):.1f} sigma clear")

    parent = np.arange(N, dtype=np.int64)
    size = np.ones(N, dtype=np.int64)
    tot_b = tot_u = tot_skip = tot_rej = 0
    keys = np.empty(N, dtype=np.uint64)
    for band in range(n_bands):
        tb = time.time()
        for i, n in enumerate(nums):
            mm = np.load(os.path.join(nd, "bandkeys", f"shard_{n:05d}.npy"), mmap_mode="r")
            keys[offs[i]:offs[i + 1]] = mm[band]
        order = np.argsort(keys, kind="stable")
        if psig is None:
            nb, nu, nskip = _union_runs(parent, size, order, keys[order], args.max_bucket)
            nrej = 0
        else:
            nb, nu, nskip, nrej = _union_runs_prefiltered(
                parent, size, order, keys[order], args.max_bucket, psig, min_match,
                args.prefilter_small)
        tot_b += nb
        tot_u += nu
        tot_skip += nskip
        tot_rej += nrej
        log(f"  band {band+1}/{n_bands}: {nb:,} buckets>=2, {nu:,} unions, "
            f"{nrej:,} prefilter-rejected, {nskip} oversized skipped "
            f"({time.time()-tb:.0f}s)")
    del keys, order
    psig = None

    labels = _flatten(parent)
    uniq, inv, comp_size = np.unique(labels, return_inverse=True, return_counts=True)
    multi = comp_size > 1
    log(f"nd3: {len(uniq):,} components, {int(multi.sum()):,} with >=2 members, "
        f"{int(comp_size[multi].sum()):,} documents in them, "
        f"largest {int(comp_size.max()):,}")

    np.save(os.path.join(nd, "component_id.npy"), inv.astype(np.int64))
    np.save(os.path.join(nd, "component_size.npy"), comp_size.astype(np.int64))

    big = np.sort(comp_size[multi])[::-1][:20]
    hist = {}
    for lo, hi in [(2, 2), (3, 9), (10, 99), (100, 999), (1000, 9999), (10000, 10**9)]:
        m = comp_size[(comp_size >= lo) & (comp_size <= hi)]
        hist[f"{lo}-{hi if hi < 10**9 else '+'}"] = {"components": int(len(m)),
                                                     "documents": int(m.sum())}
    stats = {"documents": N, "bands": int(n_bands), "buckets_ge2": int(tot_b),
             "unions": int(tot_u), "oversized_buckets_skipped": int(tot_skip),
             "max_bucket": args.max_bucket,
             "prefilter_threshold": args.prefilter_threshold,
             "prefilter_min_match": min_match,
             "prefilter_rejected_pairs": int(tot_rej),
             "components_ge2": int(multi.sum()),
             "documents_in_components": int(comp_size[multi].sum()),
             "largest_components": [int(x) for x in big],
             "size_histogram": hist}
    with open(os.path.join(nd, "nd3_stats.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
    log(f"nd3 done in {(time.time()-t0)/60:.1f}m")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
