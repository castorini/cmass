"""Step 2a -- length -> doc ids, streamed to a single JSON object.

    {"3": ["shard_02311_40118", ...], "4": [...], ...}

Keys are character lengths (ascending, as JSON strings); values are doc ids in corpus
order.  Built by radix-argsorting the 553M uint32 lengths and walking equal-length runs,
so nothing bigger than the sort index is ever held in memory and no giant Python dict is
constructed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import time

import numpy as np

from common import OUT_DIR, DocIdFormatter, human, load_manifest, log, sidecar_paths

ID_CHUNK = 4_000_000  # cap the size of any single formatted-bytes buffer


def load_all_lengths(out_dir, manifest):
    total = int(manifest["offsets"][-1])
    lengths = np.empty(total, dtype=np.uint32)
    for i, s in enumerate(manifest["shards"]):
        lo, hi = int(manifest["offsets"][i]), int(manifest["offsets"][i + 1])
        arr = np.load(sidecar_paths(out_dir, s["num"])[0])
        if arr.shape[0] != hi - lo:
            raise SystemExit(
                f"shard {s['num']}: sidecar has {arr.shape[0]} rows, manifest says {hi-lo}. "
                "Delete the stale sidecar and rerun stage 1."
            )
        lengths[lo:hi] = arr
    return lengths


def write_ids(fh, fmt, gidx):
    for i in range(0, gidx.shape[0], ID_CHUNK):
        if i:
            fh.write(b",")
        fh.write(fmt.json_array(gidx[i : i + ID_CHUNK]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    t0 = time.time()
    man = load_manifest(args.out_dir)
    fmt = DocIdFormatter(man)

    log("stage2a: loading lengths")
    lengths = load_all_lengths(args.out_dir, man)
    n = lengths.shape[0]
    log(f"  {n:,} lengths ({human(lengths.nbytes)})")

    log("stage2a: sorting")
    order = np.argsort(lengths, kind="stable")  # radix sort for integer dtypes
    sorted_len = lengths[order]
    del lengths

    change = np.flatnonzero(sorted_len[1:] != sorted_len[:-1]) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [n]])
    uniq = sorted_len[starts]
    del sorted_len, change
    log(f"  {uniq.shape[0]:,} distinct lengths "
        f"(min {uniq[0]}, max {uniq[-1]}, largest group {int((ends-starts).max()):,})")

    path = os.path.join(args.out_dir, "length_to_doc_ids.json")
    tmp = path + ".tmp"
    log(f"stage2a: writing {path}")
    with open(tmp, "wb", buffering=1 << 24) as fh:
        fh.write(b"{\n")
        for k in range(uniq.shape[0]):
            fh.write(b',\n"%d": [' % uniq[k] if k else b'"%d": [' % uniq[k])
            write_ids(fh, fmt, order[starts[k] : ends[k]])
            fh.write(b"]")
        fh.write(b"\n}\n")
    os.replace(tmp, path)

    size = os.path.getsize(path)
    log(f"stage2a done in {(time.time()-t0)/60:.1f}m -> {human(size)}")


if __name__ == "__main__":
    main()
