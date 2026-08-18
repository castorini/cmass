#!/usr/bin/env python3
"""Median document length as a function of how many duplicates a document has.

Tests the claim that heavily duplicated documents are not real content. If true, median
length should collapse as the duplicate count rises -- and on ClimbMix it does, but only in
the extreme tail: median length stays between 460 and 620 Llama-2 tokens all the way to 999
duplicates, then falls to 22 for the 71,456 documents with 1,000 or more. Documents with
100-999 duplicates are still mostly substantive (median 464), so "heavily duplicated implies
junk" is true of the tail and not of duplication generally.

Duplicate count here is exact plus near duplicates at the record floor, i.e. how many other
documents the corpus holds that are at least that similar.
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

ROW_SPAN = 100000
CAP = 4000
BUCKETS = [(1, 1), (2, 4), (5, 9), (10, 99), (100, 999), (1000, 10 ** 9)]


def main():
    ap = config.base_parser(__doc__)
    ap.add_argument("--tokenizer", default=config.DEFAULT_TOKENIZER,
                    choices=sorted(config.TOKENIZERS))
    a = ap.parse_args()

    nd = config.near_dir(a.out_dir)
    rec = os.path.join(nd, f"doc_duplicates_t{int(config.MIN_THRESHOLD*100)}")
    if a.dry_run:
        print(f"reads {rec}\nand token counts for {a.tokenizer}")
        return

    man = load_manifest(a.out_dir)
    offs, nums = man["offsets"], man["shard_nums"]
    pos = np.full(int(nums.max()) + 1, -1, dtype=np.int64)
    pos[nums] = np.arange(nums.size)
    tok = config.token_dir(a.out_dir, a.tokenizer)
    lens = np.concatenate([np.load(os.path.join(tok, f"shard_{s['num']:05d}.npy"))
                           for s in man["shards"]])
    log(f"{lens.size:,} {a.tokenizer} token counts")

    hist = {b: np.zeros(CAP + 2, dtype=np.int64) for b in BUCKETS}
    for b in ds.dataset(rec, format="parquet").to_batches(
            columns=["doc_id", "n_exact", "near_duplicates"], batch_size=400_000):
        did = b.column("doc_id")
        sh = pc.cast(pc.utf8_slice_codeunits(did, 6, 11), "int64").to_numpy(
            zero_copy_only=False)
        rw = pc.cast(pc.utf8_slice_codeunits(did, 12, 17), "int64").to_numpy(
            zero_copy_only=False)
        L = lens[offs[pos[sh]] + rw]
        tot = (b.column("n_exact").to_numpy(zero_copy_only=False).astype(np.int64)
               + pc.list_value_length(b.column("near_duplicates")).to_numpy(
                   zero_copy_only=False).astype(np.int64))
        for lo, hi in BUCKETS:
            m = (tot >= lo) & (tot <= hi)
            if m.any():
                hist[(lo, hi)] += np.bincount(np.minimum(L[m], CAP + 1),
                                              minlength=CAP + 2)

    out = {}
    print(f"\n{'duplicates':>12}{'documents':>14}{'median':>10}{'p25':>8}{'p75':>8}")
    for lo, hi in BUCKETS:
        h = hist[(lo, hi)]
        n = int(h.sum())
        if not n:
            continue
        c = np.cumsum(h)
        q = {k: int(np.searchsorted(c, f * n))
             for k, f in (("p25", .25), ("median", .5), ("p75", .75))}
        lab = f"{lo}-{hi}" if hi < 10 ** 9 else f"{lo}+"
        out[lab] = {"documents": n, **q}
        print(f"{lab:>12}{n:>14,}{q['median']:>10,}{q['p25']:>8,}{q['p75']:>8,}")

    path = os.path.join(a.out_dir, f"corpus_dup_vs_length_{a.tokenizer}.json")
    with open(path, "w") as fh:
        json.dump({"tokenizer": config.TOKENIZERS[a.tokenizer][0], "buckets": out}, fh,
                  indent=2)
    log(f"wrote {path}")


if __name__ == "__main__":
    main()
