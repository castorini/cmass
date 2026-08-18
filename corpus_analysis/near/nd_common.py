"""Shared machinery for the near-duplicate (MinHash/LSH) pass.

Operates on exact-duplicate *representatives* -- the corpus minus the redundant copies
found by the exact pass (542,305,160 of 553,240,576 for ClimbMix). Excluding those is
not tidiness: exact duplicates are Jaccard 1.0, so they collide in every LSH band and
would have injected 124.8M meaningless candidate pairs, 56% of them from just 24 groups.

Two performance notes, both measured on real shards:

* MinHash uses bijective 64-bit mixing (a splitmix64 finalizer applied to ``h XOR seed_i``)
  rather than the textbook ``(a*h + b) mod (2**61 - 1)``. Both are permutations of the hash
  space, so MinHash's guarantees are identical, but dropping the modulo took the 128-hash
  signature from 413 us/doc to 33 us/doc — a 12x speedup that makes the permutation count
  essentially free next to tokenisation.
* Shingles are built by hashing each word once and combining adjacent words with a rolling
  polynomial, instead of materialising ~465 joined 5-gram strings per document. 404 us/doc
  -> 155 us/doc for the shingling half.
"""

import os
import re

import numpy as np
import xxhash
from numba import njit
from numpy.lib.stride_tricks import sliding_window_view

from common import OUT_DIR
from config import N_PERM, SHINGLE_K as K   # noqa: F401

ND_DIR = os.path.join(OUT_DIR, "near_dup")

SIG_DTYPE = np.uint32    # signatures stored as the top 32 bits of each 64-bit minhash

_NON = re.compile(r"[^a-z0-9]+")
_P = np.uint64(0x100000001B3)
_COEF = np.array([_P ** np.uint64(K - 1 - i) for i in range(K)], dtype=np.uint64)
_M1 = np.uint64(0xBF58476D1CE4E5B9)
_M2 = np.uint64(0x94D049BB133111EB)


def perm_seeds(n=N_PERM, seed=20260728):
    """Fixed seeds so every stage and every rerun produces identical signatures."""
    return np.random.default_rng(seed).integers(0, 2 ** 63, n, dtype=np.uint64)


def _mix64(x):
    x = x.copy()
    x ^= x >> np.uint64(30)
    x *= _M1
    x ^= x >> np.uint64(27)
    x *= _M2
    x ^= x >> np.uint64(31)
    return x


def shingles(text):
    """Normalised word-5-gram hash set, sorted and deduplicated (uint64)."""
    w = _NON.sub(" ", text.lower()).split()
    if len(w) < K:
        return np.empty(0, dtype=np.uint64)
    wh = np.fromiter((xxhash.xxh64_intdigest(x) for x in w), dtype=np.uint64, count=len(w))
    return np.unique(_mix64((sliding_window_view(wh, K) * _COEF).sum(axis=1)))


@njit(cache=True, nogil=True)
def _minhash(h, seeds, out):
    m1 = np.uint64(0xBF58476D1CE4E5B9)
    m2 = np.uint64(0x94D049BB133111EB)
    s30 = np.uint64(30)
    s27 = np.uint64(27)
    s31 = np.uint64(31)
    for i in range(seeds.shape[0]):
        sd = seeds[i]
        m = np.uint64(0xFFFFFFFFFFFFFFFF)
        for j in range(h.shape[0]):
            x = h[j] ^ sd
            x ^= x >> s30
            x *= m1
            x ^= x >> s27
            x *= m2
            x ^= x >> s31
            if x < m:
                m = x
        out[i] = np.uint32(m >> np.uint64(32))


def minhash(h, seeds):
    out = np.empty(seeds.shape[0], dtype=SIG_DTYPE)
    _minhash(h, seeds, out)
    return out


@njit(cache=True, nogil=True)
def jaccard_exact(a, b):
    """Exact Jaccard of two sorted, deduplicated uint64 shingle arrays."""
    i = j = inter = 0
    na, nb = a.shape[0], b.shape[0]
    while i < na and j < nb:
        if a[i] == b[j]:
            inter += 1
            i += 1
            j += 1
        elif a[i] < b[j]:
            i += 1
        else:
            j += 1
    union = na + nb - inter
    if union == 0:
        return 0.0
    return inter / union


@njit(cache=True, nogil=True)
def pairwise_jaccard(flat, offs, out):
    """Exact all-pairs Jaccard for one component.

    ``flat``/``offs`` are a ragged array of sorted shingle sets; ``out`` is a preallocated
    (k, k) float32 matrix. Only candidate edges would be cheaper, but complete-linkage has
    to know adjacency for *arbitrary* pairs inside a cluster -- building cliques on the LSH
    candidate graph alone would split clusters wherever LSH missed a true edge.
    """
    k = offs.shape[0] - 1
    for i in range(k):
        out[i, i] = 1.0
        for j in range(i + 1, k):
            s = jaccard_exact(flat[offs[i]:offs[i + 1]], flat[offs[j]:offs[j + 1]])
            out[i, j] = s
            out[j, i] = s


@njit(cache=True)
def greedy_clique_partition(sim, thr, order):
    """Partition a component into cliques: every pair inside a cluster is >= thr.

    Greedy and deterministic -- seeds are taken in ``order`` (degree-descending, doc id
    breaking ties), and a candidate joins only if it is >= thr to *every* current member.
    Optimal clique partition is NP-hard; this is the standard greedy cover, and its
    order-dependence is why the summary reports how it compares with single-linkage.

    Returns label[i] for each vertex; singletons get their own label.
    """
    k = sim.shape[0]
    label = np.full(k, -1, dtype=np.int64)
    nxt = 0
    members = np.empty(k, dtype=np.int64)
    for oi in range(k):
        s = order[oi]
        if label[s] != -1:
            continue
        label[s] = nxt
        nm = 1
        members[0] = s
        for oj in range(oi + 1, k):
            c = order[oj]
            if label[c] != -1:
                continue
            ok = True
            for t in range(nm):
                if sim[c, members[t]] < thr:
                    ok = False
                    break
            if ok:
                label[c] = nxt
                members[nm] = c
                nm += 1
        nxt += 1
    return label


def sig_path(shard_num, nd_dir=ND_DIR):
    return os.path.join(nd_dir, "sigs", f"shard_{shard_num:05d}.sig.npy")


def rep_path(shard_num, nd_dir=ND_DIR):
    return os.path.join(nd_dir, "reps", f"shard_{shard_num:05d}.npy")


def band_layout(n_perm, b, r):
    if b * r > n_perm:
        raise ValueError(f"{b} bands x {r} rows exceeds {n_perm} permutations")
    return [(i * r, (i + 1) * r) for i in range(b)]


def lsh_prob(s, b, r):
    """P(pair becomes a candidate) at true Jaccard s."""
    return 1.0 - (1.0 - s ** r) ** b
