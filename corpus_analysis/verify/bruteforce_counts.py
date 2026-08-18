#!/usr/bin/env python3
"""Independent brute-force verification of reported duplicate group sizes.

Shares nothing with the pipeline: no SHA-256, no MinHash, no manifest, no shard offsets, no
exact index. It globs the parquet files directly, reads the text column, and counts string
equality. If the pipeline's group sizes are real, these counts must match exactly.

Targets are taken from the largest groups in summary.json, so this generalises to any
corpus rather than testing hardcoded strings. Counts are reported for both raw and stripped
text, since the pipeline groups on text.strip().

On ClimbMix this reproduced all six sampled group sizes exactly and counted 553,240,576
documents by walking every row -- matching the manifest, so no shard was skipped or read
twice.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import glob
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed

import pyarrow.parquet as pq

import config

_TARGETS = None


def _init(targets):
    global _TARGETS
    _TARGETS = set(targets)


def scan(path):
    raw, stripped = Counter(), Counter()
    n = 0
    for v in pq.read_table(path, columns=["text"]).column("text").to_pylist():
        n += 1
        if v is None:
            continue
        if v in _TARGETS:
            raw[v] += 1
        s = v.strip()
        if s in _TARGETS:
            stripped[s] += 1
    return n, raw, stripped


def main():
    ap = config.base_parser(__doc__, workers=48)
    ap.add_argument("--top", type=int, default=6,
                    help="how many of the largest groups to verify (default: %(default)s)")
    a = ap.parse_args()

    summary = os.path.join(a.out_dir, "summary.json")
    if not os.path.exists(summary):
        raise SystemExit(f"no {summary}; run exact/step4_summary.py first")
    groups = json.load(open(summary))["largest_groups"][:a.top]

    # fetch one representative per group straight from the corpus
    targets = {}
    for g in groups:
        d = g["doc_ids_sample"][0]
        col = pq.read_table(os.path.join(a.corpus_dir, f"shard_{int(d[6:11]):05d}.parquet"),
                            columns=["text"]).column("text")
        targets[col[int(d[12:17])].as_py()] = g["count"]

    files = sorted(glob.glob(os.path.join(a.corpus_dir, "*.parquet")))
    if a.dry_run:
        print(f"would scan {len(files):,} parquet files for {len(targets)} target strings")
        for t, c in targets.items():
            print(f"   expect {c:>9,}  {t[:60]!r}")
        return 0

    print(f"scanning {len(files):,} parquet files with {a.workers} workers")
    total = 0
    raw, stripped = Counter(), Counter()
    done = 0
    with ProcessPoolExecutor(a.workers, initializer=_init,
                             initargs=(list(targets),)) as ex:
        futs = [ex.submit(scan, f) for f in files]
        for fu in as_completed(futs):
            n, r, s = fu.result()
            total += n
            raw.update(r)
            stripped.update(s)
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(files)} shards, {total:,} docs", flush=True)

    print(f"\ntotal documents counted by brute force: {total:,}")
    print(f"\n{'expected':>10}{'raw':>10}{'stripped':>10}  text")
    ok = True
    for t, exp in targets.items():
        s = stripped[t]
        mark = "OK" if s == exp else "MISMATCH"
        disp = (t[:50] + "...") if len(t) > 53 else t
        print(f"{exp:>10,}{raw[t]:>10,}{s:>10,}  {mark:<9}{disp!r}")
        ok &= s == exp
    print("\nALL COUNTS MATCH" if ok else "\nDISCREPANCY FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
