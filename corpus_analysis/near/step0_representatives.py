"""Near-dup step 0 -- mark the 10,935,416 redundant exact copies so the MinHash pass skips them.

``duplicate_groups.jsonl`` lists each exact-duplicate group with members sorted by doc id,
so ``doc_ids[0]`` is the representative and ``doc_ids[1:]`` are redundant. Writes the
*excluded* rows per shard (10.9M values total) rather than the representative rows
(542.3M), since the complement is cheap to take and 50x smaller to store.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import collections
import json
import time

import numpy as np

from common import OUT_DIR, load_manifest, log
from near.nd_common import ND_DIR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--nd-dir", default=ND_DIR)
    args = ap.parse_args()

    t0 = time.time()
    man = load_manifest(args.out_dir)
    excl = collections.defaultdict(list)
    n_groups = n_excl = 0
    with open(os.path.join(args.out_dir, "duplicate_groups.jsonl"), "rb") as fh:
        for line in fh:
            ids = json.loads(line)["doc_ids"]
            n_groups += 1
            for d in ids[1:]:                      # ids[0] is the kept representative
                excl[int(d[6:11])].append(int(d[12:17]))
                n_excl += 1
    log(f"nd0: {n_groups:,} exact-dup groups -> {n_excl:,} redundant copies to exclude")

    d = os.path.join(args.nd_dir, "excluded")
    os.makedirs(d, exist_ok=True)
    for shard, rows in excl.items():
        np.save(os.path.join(d, f"shard_{shard:05d}.npy"),
                np.sort(np.array(rows, dtype=np.uint32)))

    total = int(man["offsets"][-1])
    meta = {
        "total_docs": total,
        "excluded_exact_duplicates": n_excl,
        "representatives": total - n_excl,
        "shards_with_exclusions": len(excl),
    }
    with open(os.path.join(args.nd_dir, "nd0_stats.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    log(f"nd0 done in {(time.time()-t0)/60:.1f}m: {json.dumps(meta)}")


if __name__ == "__main__":
    main()
