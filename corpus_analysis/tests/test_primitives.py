"""Correctness tests for the near-duplicate primitives."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from near.nd_common import (shingles, minhash, perm_seeds, jaccard_exact,
                       pairwise_jaccard, greedy_clique_partition, lsh_prob, N_PERM)

rng = np.random.default_rng(0)

# --- jaccard_exact vs Python sets
for _ in range(300):
    a = np.unique(rng.integers(0, 500, rng.integers(1, 200), dtype=np.uint64))
    b = np.unique(rng.integers(0, 500, rng.integers(1, 200), dtype=np.uint64))
    want = len(set(a.tolist()) & set(b.tolist())) / len(set(a.tolist()) | set(b.tolist()))
    assert abs(jaccard_exact(a, b) - want) < 1e-12
print("PASS jaccard_exact matches Python set arithmetic (300 random cases)")

# --- minhash estimator is unbiased vs true Jaccard
seeds = perm_seeds(N_PERM)
rows = []
for target in (0.5, 0.7, 0.8, 0.9, 0.95):
    errs = []
    for _ in range(60):
        base = np.unique(rng.integers(0, 2**63, 500, dtype=np.uint64))
        n_shared = int(len(base) * target)
        a = base
        b = np.unique(np.concatenate([base[:n_shared],
                                      rng.integers(0, 2**63, len(base) - n_shared, dtype=np.uint64)]))
        true = jaccard_exact(np.sort(a), np.sort(b))
        est = (minhash(np.sort(a), seeds) == minhash(np.sort(b), seeds)).mean()
        errs.append(est - true)
    errs = np.array(errs)
    rows.append((target, errs.mean(), errs.std()))
    print(f"  target~{target:.2f}: bias {errs.mean():+.4f}  sd {errs.std():.4f} "
          f"(theory {np.sqrt(target*(1-target)/N_PERM):.4f})")
assert all(abs(m) < 0.01 for _, m, _ in rows), "minhash estimator is biased"
print(f"PASS minhash unbiased at n={N_PERM}")

# --- greedy_clique_partition invariant: every pair within a cluster >= thr
for trial in range(200):
    k = int(rng.integers(2, 25))
    sim = rng.random((k, k)).astype(np.float32)
    sim = np.maximum(sim, sim.T); np.fill_diagonal(sim, 1.0)
    thr = float(rng.uniform(0.3, 0.9))
    order = np.argsort(-(sim >= thr).sum(1), kind="stable").astype(np.int64)
    lab = greedy_clique_partition(sim, thr, order)
    for c in np.unique(lab):
        idx = np.flatnonzero(lab == c)
        for i in range(len(idx)):
            for j in range(i+1, len(idx)):
                assert sim[idx[i], idx[j]] >= thr, (trial, thr, sim[idx[i], idx[j]])
    assert (lab >= 0).all()
print("PASS greedy_clique_partition: every intra-cluster pair >= threshold (200 cases)")

# --- pairwise matrix agrees with direct computation
sets = [np.unique(rng.integers(0, 300, int(rng.integers(5, 80)), dtype=np.uint64)) for _ in range(12)]
offs = np.cumsum([0] + [len(s) for s in sets]).astype(np.int64)
flat = np.concatenate(sets)
out = np.zeros((12, 12), dtype=np.float32)
pairwise_jaccard(flat, offs, out)
for i in range(12):
    for j in range(12):
        if i != j: assert abs(out[i, j] - jaccard_exact(sets[i], sets[j])) < 1e-6
print("PASS pairwise_jaccard matches jaccard_exact")

# --- end-to-end: real text edited to a known degree must be caught by LSH
txt = " ".join(f"word{i}" for i in range(600))
a = shingles(txt)
w = txt.split()
for frac in (0.02, 0.10, 0.25):
    m = w.copy()
    for i in rng.choice(len(w), int(len(w)*frac), replace=False): m[i] = "zzz"
    b = shingles(" ".join(m))
    j = jaccard_exact(a, b)
    est = (minhash(a, seeds) == minhash(b, seeds)).mean()
    print(f"  edited {frac:.0%} of words -> exact J={j:.3f}  minhash est={est:.3f}  "
          f"P(candidate | 32x8)={lsh_prob(j,32,8):.3f}")
print("\nALL PRIMITIVE TESTS PASS")
