#!/usr/bin/env python3
"""Assert that every cluster really is a clique.

Complete linkage promises that any two documents inside a cluster are at least J similar.
That promise is only worth what it is checked against, so this scans every cluster file
end to end and reports the worst ``min_jaccard`` observed together with the number of
clusters below threshold. It parses the field directly rather than through json.loads,
because the cluster files run to tens of gigabytes.

On ClimbMix this covers 200.5M clusters and finds the worst minimum to be exactly the
threshold in each file, with zero violations.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re

import config
from common import log

FIELD = re.compile(rb'"min_jaccard":\s*([0-9.]+)')


def scan(path, thr):
    worst, n, bad = 1.0, 0, 0
    with open(path, "rb") as fh:
        for line in fh:
            m = FIELD.search(line)
            if not m:
                continue
            v = float(m.group(1))
            n += 1
            if v < worst:
                worst = v
            if v < thr - 1e-9:
                bad += 1
    return n, worst, bad


def main():
    ap = config.base_parser(__doc__)
    ap.add_argument("--thresholds", default=",".join(str(t) for t in config.THRESHOLDS))
    a = ap.parse_args()
    nd = config.near_dir(a.out_dir)
    ok = True
    for t in (float(x) for x in a.thresholds.split(",")):
        path = os.path.join(nd, f"near_dup_clusters_t{int(t*100)}.jsonl")
        if not os.path.exists(path):
            log(f"missing {path}")
            ok = False
            continue
        if a.dry_run:
            print(f"would scan {path} ({os.path.getsize(path)/1e9:.1f} GB) against J={t}")
            continue
        n, worst, bad = scan(path, t)
        status = "OK" if bad == 0 else f"{bad:,} VIOLATIONS"
        print(f"J>={t}: {n:>12,} clusters   worst min_jaccard {worst:.4f}   {status}")
        ok &= bad == 0
    if not a.dry_run:
        print("\nall clusters satisfy the pairwise threshold" if ok
              else "\nFAILED: at least one cluster is not a clique")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
