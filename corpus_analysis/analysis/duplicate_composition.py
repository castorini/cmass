#!/usr/bin/env python3
"""Corpus composition by duplicate status, and the duplicate-count distribution per tier.

One pass over the per-document records produces both artifacts the duplication figures
need, where the two scripts this replaces made the same scan twice.

Tiers are mutually exclusive: each document is assigned to its STRONGEST relationship --
a byte-identical copy outranks any near duplicate, and above that the highest Jaccard
wins. That is what lets the tiers partition the corpus and be drawn as a pie; cumulative
brackets (J>=0.9, J>=0.8, J>=0.7) overlap and would triple-count.

Note this is the "affected" view: every member of a duplicate group counts, including the
one you would keep. For the "removable" view see analysis/clique_retention.py -- on
ClimbMix the two differ by 15.5 points (39.60% affected against 24.14% removable), because
every cluster retains a representative.

Counts are reported as a distribution as well as a mean, because the means are small while
the tails run to thousands. The mean number of exact duplicates is 14.82 against a median
of 1: a handful of boilerplate strings recurring thousands of times drags it up, and a
mean alone would badly misdescribe a typical document.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collections
import json

import numpy as np
import pyarrow.compute as pc
import pyarrow.dataset as ds

import config
from common import load_manifest, log

CAP = 20000                      # count histogram cap; the tail is kept separately
BUCKETS = [(1, 1), (2, 2), (3, 4), (5, 9), (10, 24), (25, 99), (100, 10 ** 9)]


def blabel(lo, hi):
    return f"{lo}-{hi}" if hi < 10 ** 9 else f"{lo}+"


def main():
    ap = config.base_parser(__doc__)
    a = ap.parse_args()
    nd = config.near_dir(a.out_dir)
    rec = os.path.join(nd, f"doc_duplicates_t{int(config.MIN_THRESHOLD*100)}")
    if not os.path.isdir(rec):
        raise SystemExit(f"no per-document records at {rec}; run near/step5_doc_records.py")

    N = int(load_manifest(a.out_dir)["offsets"][-1])
    tiers = ["exact"] + [f"near_{t}" for t in sorted(config.THRESHOLDS, reverse=True)]
    if a.dry_run:
        print(f"reads {rec}\ncorpus {N:,} documents\ntiers {tiers}")
        return

    docs = collections.Counter()
    s1 = collections.Counter()
    s2 = collections.Counter()
    hist = {t: np.zeros(CAP + 2, dtype=np.int64) for t in tiers}
    nb = {t: collections.Counter() for t in tiers}
    recs = 0

    for b in ds.dataset(rec, format="parquet").to_batches(
            columns=["n_exact", "near_duplicates"], batch_size=500_000):
        ne = b.column("n_exact").to_numpy(zero_copy_only=False).astype(np.int64)
        nd_col = b.column("near_duplicates")
        ln = pc.list_value_length(nd_col).to_numpy(zero_copy_only=False).astype(np.int64)
        recs += len(ne)
        mx = np.zeros(len(ne), dtype=np.float32)
        nz = ln > 0
        if nz.any():
            # lists are stored sorted by descending jaccard, so element 0 is the max
            mx[nz] = pc.list_element(nd_col.take(np.flatnonzero(nz)), 0).field(
                "jaccard").to_numpy(zero_copy_only=False)

        is_exact = ne > 0
        assigned = is_exact.copy()
        sel = [("exact", is_exact, ne)]
        for t in sorted(config.THRESHOLDS, reverse=True):
            m = ~assigned & (mx >= t)
            assigned |= m
            sel.append((f"near_{t}", m, ln))
        for name, mask, cnt in sel:
            if not mask.any():
                continue
            c = cnt[mask]
            docs[name] += int(c.size)
            s1[name] += float(c.sum())
            s2[name] += float((c.astype(np.float64) ** 2).sum())
            hist[name] += np.bincount(np.minimum(c, CAP + 1), minlength=CAP + 2)
            for lo, hi in BUCKETS:
                nb[name][(lo, hi)] += int(((c >= lo) & (c <= hi)).sum())

    dup_total = sum(docs.values())
    pie = [{"label": "no duplicates", "documents": N - dup_total,
            "share": round((N - dup_total) / N, 5)}]
    for t in tiers:
        pie.append({"label": t, "documents": docs[t], "share": round(docs[t] / N, 5)})
    assert sum(p["documents"] for p in pie) == N, "tiers must partition the corpus"

    moments = {}
    for t in tiers:
        n = docs[t]
        if not n:
            continue
        mean = s1[t] / n
        sd = max(s2[t] / n - mean * mean, 0.0) ** 0.5
        cum = np.cumsum(hist[t])
        q = {k: int(np.searchsorted(cum, f * n)) for k, f in
             (("p1", .01), ("p10", .10), ("p25", .25), ("median", .5),
              ("p75", .75), ("p90", .90), ("p99", .99))}
        moments[t] = {"documents": n, "mean": round(mean, 4), "sd": round(sd, 4),
                      "max": int(np.max(np.nonzero(hist[t]))), **q,
                      "distribution": {blabel(lo, hi): nb[t][(lo, hi)]
                                       for lo, hi in BUCKETS}}

    print(f"{'tier':<14}{'documents':>14}{'share':>9}{'mean':>9}{'median':>8}"
          f"{'p90':>7}{'max':>9}")
    print(f"{'no duplicates':<14}{N-dup_total:>14,}{(N-dup_total)/N*100:8.2f}%"
          f"{'-':>9}{'-':>8}{'-':>7}{'-':>9}")
    for t in tiers:
        if t not in moments:
            continue
        m = moments[t]
        print(f"{t:<14}{m['documents']:>14,}{m['documents']/N*100:8.2f}%"
              f"{m['mean']:>9.2f}{m['median']:>8}{m['p90']:>7}{m['max']:>9,}")

    for path, obj in (
            (os.path.join(a.out_dir, "corpus_dup_chart_data.json"),
             {"corpus_documents": N, "records_in_file": recs, "pie": pie}),
            (os.path.join(a.out_dir, "corpus_dup_moments.json"), moments)):
        with open(path, "w") as fh:
            json.dump(obj, fh, indent=2)
        log(f"wrote {path}")


if __name__ == "__main__":
    main()
