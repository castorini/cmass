"""Step 1 -- one parallel pass over the corpus producing per-shard binary sidecars.

For every document: strip leading/trailing whitespace, record the character length and the
sha256 of the stripped UTF-8 bytes.  Results go to fixed-width numpy arrays rather than any
Python dict -- at 553M documents a dict of digests would cost ~170GB of live objects.

  shard_NNNNN.len.npy   uint32[n_rows]      character length after strip
  shard_NNNNN.sha.npy   uint8[n_rows, 32]   raw sha256 digest

~36 bytes/doc, ~20GB total.  Both files present == shard finished, so reruns resume.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import hashlib
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pyarrow.parquet as pq

from common import CORPUS_DIR, OUT_DIR, atomic_save, discover_shards, log, sidecar_paths

_EMPTY_SHA = hashlib.sha256(b"").digest()


def scan_shard(task):
    num, path, out_dir = task
    len_path, sha_path = sidecar_paths(out_dir, num)
    if os.path.exists(len_path) and os.path.exists(sha_path):
        return num, -1, 0.0, 0  # already done

    t0 = time.time()
    texts = pq.read_table(path, columns=["text"]).column("text").to_pylist()
    n = len(texts)

    lengths = np.empty(n, dtype=np.uint32)
    digests = np.empty((n, 32), dtype=np.uint8)
    sha256 = hashlib.sha256
    frombuffer = np.frombuffer
    n_null = 0

    for i, s in enumerate(texts):
        if s is None:  # not observed in sampling, but never crash the run over one
            n_null += 1
            lengths[i] = 0
            digests[i] = frombuffer(_EMPTY_SHA, dtype=np.uint8)
            continue
        t = s.strip()
        lengths[i] = len(t)
        digests[i] = frombuffer(sha256(t.encode("utf-8")).digest(), dtype=np.uint8)

    atomic_save(len_path, lengths)
    atomic_save(sha_path, digests)
    return num, n, time.time() - t0, n_null


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-dir", default=CORPUS_DIR)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--limit-shards", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(os.path.join(args.out_dir, "stage1"), exist_ok=True)
    shards = discover_shards(args.corpus_dir)
    if args.limit_shards:
        shards = shards[: args.limit_shards]

    tasks = [(num, path, args.out_dir) for num, path in shards]
    log(f"stage1: {len(tasks)} shards, {args.workers} workers")

    t0 = time.time()
    done = skipped = 0
    rows = nulls = 0
    with ProcessPoolExecutor(args.workers) as ex:
        futs = [ex.submit(scan_shard, t) for t in tasks]
        for fut in as_completed(futs):
            num, n, dt, n_null = fut.result()
            done += 1
            if n < 0:
                skipped += 1
            else:
                rows += n
                nulls += n_null
            if done % 100 == 0 or done == len(tasks):
                el = time.time() - t0
                rate = done / el if el else 0
                eta = (len(tasks) - done) / rate if rate else 0
                log(f"  {done}/{len(tasks)} shards ({skipped} cached) "
                    f"{rate:.1f} shard/s  eta {eta/60:.1f}m")

    log(f"stage1 done in {(time.time()-t0)/60:.1f}m: "
        f"{rows:,} rows scanned, {skipped} shards already cached, {nulls} null texts")


if __name__ == "__main__":
    main()
