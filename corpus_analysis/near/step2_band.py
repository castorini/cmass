"""Near-dup step 2 -- LSH banding.

Splits each 256-value signature into B bands of R rows and hashes each band to a 64-bit
key. Two documents are candidates if any band key matches.

Default layout is 36 bands x 7 rows, chosen from the measured S-curve: 95.4% of genuinely
0.7-similar pairs become candidates, while only 24.6% of 0.5-similar pairs do. A more
aggressive 42x6 would lift 0.7 recall to 99.5% but drag in 53% of 0.5-similar pairs, and
that junk is what inflates connected components into unclusterable blobs.

Output is band-major per shard -- ``bandkeys/shard_NNNNN.npy`` of shape (B, n_reps) -- so
stage 3 can mmap one band across all shards as contiguous slices instead of re-reading the
whole 555 GB signature store once per band.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

from common import OUT_DIR, atomic_save, load_manifest, log
from near.nd_common import ND_DIR, N_PERM, band_layout, lsh_prob, sig_path

MIX = np.uint64(0x9E3779B97F4A7C15)


def band_keys(sig, bands):
    """(n, N_PERM) uint32 signatures -> (B, n) uint64 band keys."""
    out = np.empty((len(bands), sig.shape[0]), dtype=np.uint64)
    for bi, (lo, hi) in enumerate(bands):
        h = np.zeros(sig.shape[0], dtype=np.uint64)
        for c in range(lo, hi):
            h ^= sig[:, c].astype(np.uint64) + MIX + (h << np.uint64(6)) + (h >> np.uint64(2))
        h ^= h >> np.uint64(33)
        h *= np.uint64(0xFF51AFD7ED558CCD)
        h ^= h >> np.uint64(33)
        out[bi] = h
    return out


def process_shard(task):
    num, nd_dir, b, r, n_perm, pf_perms = task
    dst = os.path.join(nd_dir, "bandkeys", f"shard_{num:05d}.npy")
    pf_dst = os.path.join(nd_dir, "prefilter", f"shard_{num:05d}.npy")
    need_bands = not os.path.exists(dst)
    need_pf = pf_perms > 0 and not os.path.exists(pf_dst)
    if not need_bands and not need_pf:
        return num, -1
    sig = np.load(sig_path(num, nd_dir))
    if n_perm > sig.shape[1]:
        ext = os.path.join(nd_dir, "sigs2", f"shard_{num:05d}.sig.npy")
        if not os.path.exists(ext):
            raise SystemExit(f"need {n_perm} perms but {ext} is missing; run nd1b first")
        sig = np.concatenate([sig, np.load(ext)], axis=1)
    if sig.shape[1] < n_perm:
        raise SystemExit(f"shard {num}: only {sig.shape[1]} perms available")
    if need_pf:
        atomic_save(pf_dst, np.ascontiguousarray(sig[:, :pf_perms]))
    if need_bands:
        atomic_save(dst, band_keys(sig, band_layout(n_perm, b, r)))
    return num, sig.shape[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--nd-dir", default=ND_DIR)
    ap.add_argument("--bands", type=int, default=36)
    ap.add_argument("--rows", type=int, default=7)
    ap.add_argument("--n-perm", type=int, default=N_PERM)
    ap.add_argument("--prefilter-perms", type=int, default=0,
                    help="also write the first N perms for nd3's prefilter")
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--limit-shards", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(os.path.join(args.nd_dir, "bandkeys"), exist_ok=True)
    if args.prefilter_perms:
        os.makedirs(os.path.join(args.nd_dir, "prefilter"), exist_ok=True)
    man = load_manifest(args.out_dir)
    shards = [s["num"] for s in man["shards"]]
    if args.limit_shards:
        shards = shards[: args.limit_shards]

    b, r = args.bands, args.rows
    log(f"nd2: {b} bands x {r} rows on {args.n_perm} perms; 50% at "
        f"{(1/b)**(1/r):.3f}; P(cand) at s=0.7 {lsh_prob(0.7,b,r):.3f}, "
        f"0.8 {lsh_prob(0.8,b,r):.3f}, 0.9 {lsh_prob(0.9,b,r):.3f}, "
        f"0.5 {lsh_prob(0.5,b,r):.3f}")

    t0 = time.time()
    tasks = [(n, args.nd_dir, b, r, args.n_perm, args.prefilter_perms)
             for n in shards]
    done = cached = total = 0
    with ProcessPoolExecutor(args.workers) as ex:
        futs = [ex.submit(process_shard, t) for t in tasks]
        for fut in as_completed(futs):
            _, n = fut.result()
            done += 1
            if n < 0:
                cached += 1
            else:
                total += n
            if done % 500 == 0 or done == len(tasks):
                log(f"  {done}/{len(tasks)} shards ({cached} cached)")

    stats = {"bands": b, "rows": r, "n_perm": args.n_perm, "docs": total,
             "prefilter_perms": args.prefilter_perms,
             "band_key_bytes": total * b * 8,
             "p_candidate": {str(s): lsh_prob(s, b, r) for s in (0.5, 0.6, 0.7, 0.8, 0.9)}}
    with open(os.path.join(args.nd_dir, "nd2_stats.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
    log(f"nd2 done in {(time.time()-t0)/60:.1f}m: {json.dumps(stats)}")


if __name__ == "__main__":
    main()
