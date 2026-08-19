#!/usr/bin/env python3
"""Repartition the per-document duplicate records for release on HuggingFace.

The pipeline writes 514 parquet parts keyed by internal component id, which scatters
doc_ids: a single part holds documents from every corpus shard, unsorted. That is fine for
the pipeline, which reads everything, and bad for the release, whose main use is "what
duplicates this document?" -- answering it would mean scanning all 4.4 GB because no file
or row group has a useful doc_id range.

This rewrites the same rows sorted by doc_id into a smaller number of larger files. doc_id
is fixed width, so lexicographic order is (shard, row) order: each output file then covers
a contiguous slice of the corpus, row-group statistics become meaningful, and a lookup
touches one file and often one row group.

Three passes, all memory-bounded:

  0  count rows per corpus shard, and choose file boundaries with roughly equal rows
     (shards differ in how duplicated they are, so equal shard counts would not be equal
     row counts)
  1  scatter every input row to its target bucket, one temp file per (worker, bucket) so
     workers never write to the same file
  2  read each bucket, sort by doc_id, write the final part

Resumable: a bucket whose final part exists is skipped.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import glob
import json
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

import config
from common import log

SPLIT = "corpus"


def shard_of(col):
    """doc_id -> corpus shard number as numpy, vectorised (ids are fixed width).

    Accepts either an Array (from a RecordBatch) or a ChunkedArray (from a Table); only the
    former takes to_numpy's zero_copy_only kwarg, so chunked input is combined first.
    """
    out = pc.cast(pc.utf8_slice_codeunits(col, 6, 11), "int32")
    if isinstance(out, pa.ChunkedArray):
        out = out.combine_chunks()
    return out.to_numpy(zero_copy_only=False)


def count_shards(path):
    counts = np.zeros(1 << 16, dtype=np.int64)
    for b in pq.ParquetFile(path).iter_batches(columns=["doc_id"], batch_size=1 << 20):
        s = shard_of(b.column("doc_id"))
        counts += np.bincount(s, minlength=counts.size)[:counts.size]
    return counts


def scatter(task):
    wid, paths, bounds, tmp, cols = task
    d = os.path.join(tmp, f"w{wid:03d}")
    os.makedirs(d, exist_ok=True)
    writers = {}
    n = 0
    try:
        for p in paths:
            for b in pq.ParquetFile(p).iter_batches(batch_size=1 << 18):
                s = shard_of(b.column("doc_id"))
                t = pa.Table.from_batches([b])
                bucket = np.searchsorted(bounds, s, side="right") - 1
                n += t.num_rows
                for k in np.unique(bucket):
                    sub = t.filter(pa.array(bucket == k))
                    if k not in writers:
                        writers[k] = pq.ParquetWriter(
                            os.path.join(d, f"b{k:04d}.parquet"), t.schema,
                            compression="zstd")
                    writers[k].write_table(sub)
    finally:
        for w in writers.values():
            w.close()
    return wid, n


def build_bucket(task):
    bucket, tmp, out_dir, nbuckets, rgsize = task
    dst = os.path.join(out_dir, f"{SPLIT}-{bucket:05d}-of-{nbuckets:05d}.parquet")
    if os.path.exists(dst):
        return bucket, -1, None, None
    parts = sorted(glob.glob(os.path.join(tmp, "w*", f"b{bucket:04d}.parquet")))
    if not parts:
        return bucket, 0, None, None
    t = pa.concat_tables([pq.read_table(p) for p in parts])
    t = t.take(pc.sort_indices(t, sort_keys=[("doc_id", "ascending")]))
    tmp_dst = dst + ".tmp"
    pq.write_table(t, tmp_dst, compression="zstd", row_group_size=rgsize,
                   write_statistics=True)
    os.replace(tmp_dst, dst)
    ids = t.column("doc_id")
    return bucket, t.num_rows, ids[0].as_py(), ids[-1].as_py()


def main():
    ap = config.base_parser(__doc__, workers=24)
    ap.add_argument("--release-dir", default=None,
                    help="output directory (default: <out-dir>/hf_release/corpus_duplicates)")
    ap.add_argument("--buckets", type=int, default=32,
                    help="number of output files (default: %(default)s)")
    ap.add_argument("--row-group-size", type=int, default=100_000,
                    help="rows per parquet row group; smaller means finer predicate "
                         "pushdown on doc_id (default: %(default)s)")
    ap.add_argument("--keep-temp", action="store_true")
    a = ap.parse_args()

    src = os.path.join(config.near_dir(a.out_dir),
                       f"doc_duplicates_t{int(config.MIN_THRESHOLD*100)}")
    files = sorted(glob.glob(os.path.join(src, "*.parquet")))
    if not files:
        raise SystemExit(f"no parquet parts under {src}")
    out_dir = a.release_dir or os.path.join(a.out_dir, "hf_release", "corpus_duplicates")
    tmp = os.path.join(a.out_dir, "hf_release", "_scatter")
    total_in = sum(os.path.getsize(f) for f in files)

    if a.dry_run:
        print(f"source   : {src}\n           {len(files):,} files, {total_in/1e9:.1f} GB")
        print(f"output   : {out_dir}\n           {a.buckets} files, "
              f"{SPLIT}-00000-of-{a.buckets:05d}.parquet ...")
        print(f"scratch  : {tmp} (~{total_in/1e9:.1f} GB, removed unless --keep-temp)")
        return

    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()

    # ---- pass 0: rows per corpus shard -> boundaries with roughly equal rows
    log(f"pass 0: counting rows per shard across {len(files):,} files")
    counts = np.zeros(1 << 16, dtype=np.int64)
    with ProcessPoolExecutor(a.workers) as ex:
        for c in ex.map(count_shards, files):
            counts += c
    total_rows = int(counts.sum())
    cum = np.cumsum(counts)
    targets = (np.arange(1, a.buckets) * total_rows) / a.buckets
    bounds = np.concatenate([[0], np.searchsorted(cum, targets) + 1]).astype(np.int32)
    bounds = np.unique(bounds)
    log(f"pass 0: {total_rows:,} rows, {len(bounds)} buckets by shard boundary")

    # ---- pass 1: scatter
    log(f"pass 1: scattering into {tmp}")
    os.makedirs(tmp, exist_ok=True)
    chunks = [files[i::a.workers] for i in range(a.workers)]
    tasks = [(i, c, bounds, tmp, None) for i, c in enumerate(chunks) if c]
    seen = 0
    with ProcessPoolExecutor(a.workers) as ex:
        for wid, n in ex.map(scatter, tasks):
            seen += n
    log(f"pass 1: {seen:,} rows scattered ({time.time()-t0:.0f}s)")
    if seen != total_rows:
        raise SystemExit(f"scatter lost rows: {seen:,} != {total_rows:,}")

    # ---- pass 2: sort each bucket and write the final part
    log("pass 2: sorting buckets and writing release parts")
    nb = len(bounds)
    ranges = {}
    written = 0
    with ProcessPoolExecutor(min(a.workers, 8)) as ex:
        futs = [ex.submit(build_bucket, (k, tmp, out_dir, nb, a.row_group_size))
                for k in range(nb)]
        for f in as_completed(futs):
            k, n, lo, hi = f.result()
            if n > 0:
                written += n
                ranges[k] = {"rows": n, "first_doc_id": lo, "last_doc_id": hi}
            elif n == -1:
                log(f"  bucket {k} already present, skipped")

    manifest = {"split": SPLIT, "files": nb, "rows": total_rows,
                "row_group_size": a.row_group_size,
                "source": src, "sorted_by": "doc_id",
                "parts": {f"{SPLIT}-{k:05d}-of-{nb:05d}.parquet": ranges[k]
                          for k in sorted(ranges)}}
    with open(os.path.join(out_dir, "_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)

    out_bytes = sum(os.path.getsize(p)
                    for p in glob.glob(os.path.join(out_dir, "*.parquet")))
    log(f"wrote {written:,} rows into {nb} files, {out_bytes/1e9:.2f} GB "
        f"(source {total_in/1e9:.2f} GB) in {(time.time()-t0)/60:.1f}m")
    if not a.keep_temp:
        shutil.rmtree(tmp, ignore_errors=True)
        log("removed scatter scratch")


if __name__ == "__main__":
    main()
