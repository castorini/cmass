"""Near-dup step 1 -- MinHash signatures for every exact-dup representative.

Per shard: read text, drop the rows nd0 marked as redundant exact copies, normalise,
shingle into word 5-grams, and take N_PERM minhashes. Writes

  sigs/shard_NNNNN.sig.npy   uint32[n_reps, N_PERM]
  reps/shard_NNNNN.npy       uint32[n_reps]   row index of each signature row

Documents with fewer than K words produce no shingles and therefore no signature; they are
counted and reported, never silently dropped.

Measured on real shards: 359 us/doc at N_PERM=128, 391 us/doc at 256 -- the permutation
count is nearly free next to the 171 us/doc spent normalising text, which is why this uses
256 (finer Jaccard estimates, more banding headroom) rather than economising.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from common import CORPUS_DIR, OUT_DIR, atomic_save, load_manifest, log
from near.nd_common import K, ND_DIR, N_PERM, SIG_DTYPE, minhash, perm_seeds, rep_path, shingles, sig_path


def process_shard(task):
    num, path, nd_dir, n_perm = task
    sp, rp = sig_path(num, nd_dir), rep_path(num, nd_dir)
    if os.path.exists(sp) and os.path.exists(rp):
        return num, -1, 0, 0.0

    t0 = time.time()
    ex_path = os.path.join(nd_dir, "excluded", f"shard_{num:05d}.npy")
    col = pq.read_table(path, columns=["text"]).column("text")
    n_rows = len(col)
    keep = np.ones(n_rows, dtype=bool)
    if os.path.exists(ex_path):
        keep[np.load(ex_path).astype(np.int64)] = False
    rows = np.flatnonzero(keep).astype(np.int64)

    texts = col.take(pa.array(rows)).to_pylist()
    seeds = perm_seeds(n_perm)
    sigs = np.empty((rows.shape[0], n_perm), dtype=SIG_DTYPE)
    kept = np.empty(rows.shape[0], dtype=np.uint32)
    n = n_short = 0
    for i, t in enumerate(texts):
        sh = shingles(t or "")
        if sh.shape[0] == 0:            # fewer than K words -> no 5-gram exists
            n_short += 1
            continue
        sigs[n] = minhash(sh, seeds)
        kept[n] = rows[i]
        n += 1

    atomic_save(sp, sigs[:n])
    atomic_save(rp, kept[:n])
    return num, n, n_short, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-dir", default=CORPUS_DIR)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--nd-dir", default=ND_DIR)
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--n-perm", type=int, default=N_PERM)
    ap.add_argument("--limit-shards", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(os.path.join(args.nd_dir, "sigs"), exist_ok=True)
    os.makedirs(os.path.join(args.nd_dir, "reps"), exist_ok=True)
    man = load_manifest(args.out_dir)
    shards = [(s["num"], s["path"], args.nd_dir, args.n_perm) for s in man["shards"]]
    if args.limit_shards:
        shards = shards[: args.limit_shards]

    log(f"nd1: {len(shards)} shards, {args.workers} workers, K={K}, n_perm={args.n_perm}")
    t0 = time.time()
    done = cached = n_sig = n_short = 0
    with ProcessPoolExecutor(args.workers) as ex:
        futs = [ex.submit(process_shard, s) for s in shards]
        for fut in as_completed(futs):
            num, n, ns, dt = fut.result()
            done += 1
            if n < 0:
                cached += 1
            else:
                n_sig += n
                n_short += ns
            if done % 100 == 0 or done == len(shards):
                el = time.time() - t0
                rate = done / el if el else 0
                log(f"  {done}/{len(shards)} shards ({cached} cached) "
                    f"{rate:.1f}/s eta {(len(shards)-done)/rate/60 if rate else 0:.1f}m")

    stats = {"signatures": n_sig, "too_short_for_shingling": n_short,
             "n_perm": args.n_perm, "shingle_k": K,
             "signature_bytes": n_sig * args.n_perm * 4}
    with open(os.path.join(args.nd_dir, "nd1_stats.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
    log(f"nd1 done in {(time.time()-t0)/60:.1f}m: {json.dumps(stats)}")


if __name__ == "__main__":
    main()
