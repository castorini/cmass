#!/usr/bin/env python3
"""Verify the repartitioned release against the pipeline output, before anything is uploaded.

Repartitioning rewrites 219M rows through a scatter and a sort. That is exactly the kind of
step that can silently drop or duplicate rows, so this checks the result rather than
trusting it:

  1. row counts match;
  2. an order-independent content checksum matches. Each row is hashed over its doc_id, its
     counts, and every (neighbour, jaccard) pair; the hashes are summed mod 2^64, so the sum
     is invariant to row order but sensitive to a dropped row, a duplicated row, a changed
     score, or a lost neighbour;
  3. every output file is sorted by doc_id internally;
  4. file ranges are non-overlapping and cover the corpus in order, so a reader can binary
     search across files;
  5. sampled rows match the source field for field.

A mismatch here means the release is wrong, not that the check is fussy.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import glob
from concurrent.futures import ProcessPoolExecutor

import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import xxhash

import config
from common import log

# results go to stdout, which is block-buffered when redirected to a file; a killed run
# then loses everything it "printed". Line buffering makes partial output survive.
sys.stdout.reconfigure(line_buffering=True)

# stdout is block-buffered when redirected to a file, which hid every
# result until process exit; the slow step then looked like a hang
sys.stdout.reconfigure(line_buffering=True)

MASK = (1 << 64) - 1


def row_digest(path):
    """(rows, order-independent checksum) for one parquet file."""
    n = 0
    total = 0
    for b in pq.ParquetFile(path).iter_batches(
            columns=["doc_id", "exact_duplicates", "near_duplicates",
                     "n_exact", "n_near"], batch_size=1 << 17):
        d = b.to_pylist()
        n += len(d)
        for r in d:
            h = xxhash.xxh64()
            h.update(r["doc_id"].encode())
            h.update(f"|{r['n_exact']}|{r['n_near']}|".encode())
            for e in r["exact_duplicates"]:
                h.update(e.encode())
            for x in r["near_duplicates"]:
                # round the score so a float32 round-trip cannot fail the comparison
                h.update(f"{x['doc_id']}:{x['jaccard']:.6f}".encode())
            total = (total + h.intdigest()) & MASK
    return n, total


def digest_all(files, workers, label):
    n = t = 0
    with ProcessPoolExecutor(workers) as ex:
        for i, (rn, rt) in enumerate(ex.map(row_digest, files), 1):
            n += rn
            t = (t + rt) & MASK
            if i % 100 == 0 or i == len(files):
                log(f"  {label}: {i}/{len(files)} files, {n:,} rows")
    return n, t


def main():
    ap = config.base_parser(__doc__, workers=24)
    ap.add_argument("--release-dir", default=None)
    ap.add_argument("--sample", type=int, default=200,
                    help="rows to compare field-for-field (default: %(default)s)")
    a = ap.parse_args()

    src_dir = os.path.join(config.near_dir(a.out_dir),
                           f"doc_duplicates_t{int(config.MIN_THRESHOLD*100)}")
    rel_dir = a.release_dir or os.path.join(a.out_dir, "hf_release", "corpus_duplicates")
    src = sorted(glob.glob(os.path.join(src_dir, "*.parquet")))
    rel = sorted(glob.glob(os.path.join(rel_dir, "*.parquet")))
    if a.dry_run:
        print(f"source : {len(src)} files  {src_dir}")
        print(f"release: {len(rel)} files  {rel_dir}")
        return 0
    if not rel:
        raise SystemExit(f"no release parquet under {rel_dir}")

    ok = True
    log(f"digesting {len(src)} source files")
    n_src, h_src = digest_all(src, a.workers, "source")
    log(f"digesting {len(rel)} release files")
    n_rel, h_rel = digest_all(rel, a.workers, "release")

    print(f"\nrows     source {n_src:,}   release {n_rel:,}   "
          f"{'MATCH' if n_src == n_rel else 'MISMATCH'}")
    print(f"checksum source {h_src:#018x}   release {h_rel:#018x}   "
          f"{'MATCH' if h_src == h_rel else 'MISMATCH'}")
    ok &= n_src == n_rel and h_src == h_rel

    # ---- ordering and coverage
    print("\nper-file ranges:")
    prev_hi = None
    gaps = 0
    for p in rel:
        f = pq.ParquetFile(p)
        ids = f.read(columns=["doc_id"]).column("doc_id").to_pylist()
        srt = ids == sorted(ids)
        lo, hi = ids[0], ids[-1]
        flag = "" if srt else "  NOT SORTED"
        if prev_hi is not None and lo <= prev_hi:
            flag += "  OVERLAPS PREVIOUS"
            gaps += 1
        prev_hi = hi
        ok &= srt
        print(f"  {os.path.basename(p):<34} {len(ids):>10,} rows  {lo} .. {hi}{flag}")
    ok &= gaps == 0

    # ---- field-for-field on a sample
    import random
    random.seed(11)
    log(f"\ncomparing {a.sample} sampled rows field for field")
    want = {}
    for p in random.sample(rel, min(8, len(rel))):
        t = pq.ParquetFile(p).read().to_pylist()
        for r in random.sample(t, min(a.sample // 8, len(t))):
            want[r["doc_id"]] = r
    # a filtered dataset read touches only the doc_id column to locate the
    # sample; converting every source row through to_pylist to find 200 ids
    # was O(219M rows) of Python and dominated the whole verification
    got = ds.dataset(src_dir, format="parquet").to_table(
        filter=pc.field("doc_id").isin(list(want)))
    found = {r["doc_id"]: r for r in got.to_pylist()}
    missing = set(want) - set(found)
    diff = [k for k in found if found[k] != want[k]]
    print(f"  sampled {len(want)}   located in source {len(found)}   "
          f"missing {len(missing)}   differing {len(diff)}")
    for k in diff[:3]:
        print(f"    DIFFERS: {k}")
    ok &= not missing and not diff

    print("\nRELEASE VERIFIED" if ok else "\nVERIFICATION FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
