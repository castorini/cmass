#!/usr/bin/env python3
"""Step 1 -- token-length distribution and its cross-tab against duplication.

Produces, for one tokenizer:
  * percentiles and a bucketised length table over every document;
  * a fixed-bin histogram over a round window, for the length figure. The window is
    chosen just past p90 so the bar width is a round number a caption can state; the
    reference presentation (Figure 4a of arXiv:2508.06600) clips at p90 for the same
    reason -- the tail runs six orders of magnitude and would otherwise flatten the
    distribution into its first bin;
  * duplicate rate per length bucket, which separates two different phenomena: exact
    duplication concentrates in the shortest documents (boilerplate), while near
    duplication peaks among mid-length ones.

doc_id is fixed-width ``shard_NNNNN_RRRRR``, so shard and row are sliced out with
vectorised pyarrow string ops rather than hundreds of millions of Python int() calls.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

import numpy as np
import pyarrow.compute as pc
import pyarrow.dataset as ds

import config
from common import load_manifest, log

EDGES = [0, 32, 64, 128, 256, 512, 768, 1024, 1536, 2048, 4096, 8192, 16384, 1 << 30]
ROW_SPAN = 100000


def label(lo, hi):
    return f"{lo}-{hi}" if hi < (1 << 30) else f"{lo}+"


def load_lengths(out_dir, tokenizer, man):
    d = config.token_dir(out_dir, tokenizer)
    if not os.path.isdir(d):
        raise SystemExit(f"no token counts at {d}; run tokens/step0_tokenize.py "
                         f"--tokenizer {tokenizer} first")
    return np.concatenate([np.load(os.path.join(d, f"shard_{s['num']:05d}.npy"))
                           for s in man["shards"]])


def main():
    ap = config.base_parser(__doc__)
    ap.add_argument("--tokenizer", default=config.DEFAULT_TOKENIZER,
                    choices=sorted(config.TOKENIZERS))
    ap.add_argument("--bin-width", type=int, default=10,
                    help="histogram bin width in tokens (default: %(default)s)")
    ap.add_argument("--hist-max", type=int, default=0,
                    help="upper edge of the histogram window; 0 rounds p90 up to a "
                         "multiple of 100*bin-width")
    a = ap.parse_args()

    man = load_manifest(a.out_dir)
    nums = man["shard_nums"]
    offs = man["offsets"]
    pos = np.full(int(nums.max()) + 1, -1, dtype=np.int64)
    pos[nums] = np.arange(nums.size)

    lens = load_lengths(a.out_dir, a.tokenizer, man).astype(np.int64)
    N = lens.size
    log(f"{N:,} token counts ({a.tokenizer}), {lens.nbytes/1e9:.1f}GB")

    pct = {f"p{p}": float(np.percentile(lens, p))
           for p in (1, 5, 10, 25, 50, 75, 90, 95, 99, 99.9)}
    overall = {"documents": int(N), "total_tokens": int(lens.sum()),
               "mean": float(lens.mean()), "median": float(np.median(lens)),
               "min": int(lens.min()), "max": int(lens.max()), "percentiles": pct}
    print(json.dumps(overall, indent=2), flush=True)

    buckets = []
    for lo, hi in zip(EDGES, EDGES[1:]):
        m = (lens >= lo) & (lens < hi)
        n = int(m.sum())
        buckets.append({"bucket": label(lo, hi), "lo": lo,
                        "hi": None if hi > 1 << 29 else hi, "documents": n,
                        "share": round(n / N, 6),
                        "tokens": int(lens[m].sum()) if n else 0})
    for b in buckets:
        print(f"  {b['bucket']:>12} {b['documents']:>12,}  {b['share']*100:6.2f}%")

    # fixed-width bins over a round window just past p90
    W = a.bin_width
    step = 100 * W
    hi = a.hist_max or int(np.ceil(pct["p90"] / step) * step)
    nb = hi // W
    sub = lens[lens < hi]
    counts = np.bincount(np.minimum(sub // W, nb - 1), minlength=nb).astype(np.int64)
    log(f"histogram: {nb} bins x {W} tokens over 0..{hi}; "
        f"{int(counts.sum()):,} docs shown ({counts.sum()/N*100:.2f}%)")

    # ---- duplication status per document, from the near-duplicate records
    xt = []
    rec_dir = os.path.join(config.near_dir(a.out_dir),
                           f"doc_duplicates_t{int(config.MIN_THRESHOLD*100)}")
    if os.path.isdir(rec_dir):
        status = np.zeros(N, dtype=np.uint8)   # bit0 exact, bits1-3 J>=.7/.8/.9
        seen = 0
        for b in ds.dataset(rec_dir, format="parquet").to_batches(
                columns=["doc_id", "n_exact", "near_duplicates"], batch_size=500_000):
            did = b.column("doc_id")
            sh = pc.cast(pc.utf8_slice_codeunits(did, 6, 11), "int64").to_numpy(
                zero_copy_only=False)
            rw = pc.cast(pc.utf8_slice_codeunits(did, 12, 17), "int64").to_numpy(
                zero_copy_only=False)
            gi = offs[pos[sh]] + rw
            st = np.where(b.column("n_exact").to_numpy(zero_copy_only=False) > 0,
                          1, 0).astype(np.uint8)
            nd = b.column("near_duplicates")
            ln = pc.list_value_length(nd).to_numpy(zero_copy_only=False)
            mx = np.zeros(len(nd), dtype=np.float32)
            nz = ln > 0
            if nz.any():
                # lists are stored sorted by descending jaccard, so element 0 is the max
                mx[nz] = pc.list_element(nd.take(np.flatnonzero(nz)), 0).field(
                    "jaccard").to_numpy(zero_copy_only=False)
            for k, t in enumerate(config.THRESHOLDS, start=1):
                st |= (mx >= t).astype(np.uint8) << k
            status[gi] = st
            seen += len(did)
        log(f"mapped {seen:,} duplicate records onto token lengths")

        for lo, hi_ in zip(EDGES, EDGES[1:]):
            m = (lens >= lo) & (lens < hi_)
            n = int(m.sum())
            if not n:
                continue
            s = status[m]
            row = {"bucket": label(lo, hi_), "documents": n,
                   "exact": int((s & 1).astype(bool).sum())}
            for k, t in enumerate(config.THRESHOLDS, start=1):
                row[f"near_{t}"] = int((s & (1 << k)).astype(bool).sum())
            xt.append(row)
        hdr = "".join(f"{'>='+str(t):>9}" for t in config.THRESHOLDS)
        print(f"\n  {'bucket':>12} {'docs':>12}  {'exact%':>7}{hdr}")
        for r in xt:
            n = r["documents"]
            cells = "".join(f"{r[f'near_{t}']/n*100:8.2f}%" for t in config.THRESHOLDS)
            print(f"  {r['bucket']:>12} {n:>12,}  {r['exact']/n*100:6.2f}%{cells}")
    else:
        log(f"no duplicate records at {rec_dir}; skipping the length x duplication table")

    out = {"tokenizer": config.TOKENIZERS[a.tokenizer][0], "key": a.tokenizer,
           "add_special_tokens": False, "overall": overall, "buckets": buckets,
           "histogram_fixed_bins": {
               "bin_width": W, "hi": hi, "bins": nb, "counts": counts.tolist(),
               "edges": (np.arange(nb + 1) * W).astype(float).tolist(),
               "documents_shown": int(counts.sum()),
               "share_shown": round(float(counts.sum()) / N, 5)},
           "by_length_and_duplication": xt}
    path = config.length_stats_path(a.out_dir, a.tokenizer)
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2)
    log(f"wrote {path}")


if __name__ == "__main__":
    main()
