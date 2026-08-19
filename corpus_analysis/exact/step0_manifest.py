"""Step 0 -- enumerate shards, read row counts from parquet footers, write manifest.json.

Cheap (footer reads only, ~3s for 6543 shards with 48 threads).  Every later stage reads
the manifest instead of re-deriving global offsets, so a doc id always resolves the same
way no matter which stage produced it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
from concurrent.futures import ThreadPoolExecutor

import pyarrow.parquet as pq

from common import CORPUS_DIR, OUT_DIR, discover_shards, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-dir", default=CORPUS_DIR)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--threads", type=int, default=48)
    ap.add_argument("--limit-shards", type=int, default=0, help="0 = all (smoke testing)")
    args = ap.parse_args()

    shards = discover_shards(args.corpus_dir)
    if args.limit_shards:
        shards = shards[: args.limit_shards]
    log(f"discovered {len(shards)} shards under {args.corpus_dir}")

    def probe(item):
        num, path = item
        md = pq.ParquetFile(path).metadata
        return {"num": num, "path": path, "n_rows": md.num_rows,
                "n_row_groups": md.num_row_groups}

    with ThreadPoolExecutor(args.threads) as ex:
        rows = list(ex.map(probe, shards))

    total = sum(r["n_rows"] for r in rows)
    max_rows = max(r["n_rows"] for r in rows)
    if max_rows > 99999:
        raise SystemExit(
            f"shard has {max_rows} rows; the 5-digit row field in the doc id format "
            "would overflow. Widen DOC_ID_LEN/row padding in common.py before running."
        )

    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(os.path.join(args.out_dir, "stage1"), exist_ok=True)

    manifest = {
        "corpus_dir": args.corpus_dir,
        "n_shards": len(rows),
        "total_rows": total,
        "max_rows_per_shard": max_rows,
        "doc_id_format": "shard_{shard:05d}_{row:05d}",
        "length_unit": "characters",
        "hash": "sha256(text.strip().encode('utf-8'))",
        "shards": rows,
    }
    path = os.path.join(args.out_dir, "manifest.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(manifest, fh)
    os.replace(tmp, path)

    log(f"wrote {path}: {len(rows)} shards, {total:,} documents")


if __name__ == "__main__":
    main()
