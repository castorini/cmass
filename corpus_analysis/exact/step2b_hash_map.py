"""Step 2b -- sha256 -> doc ids, streamed to JSONL (one object per line).

    {"sha256":"9f86d0...","count":1,"doc_ids":["shard_00188_76292"]}

Sorting 553M 32-byte digests by byte comparison would be painfully slow, so this sorts by
the first 8 bytes reinterpreted as a big-endian uint64 (numpy uses radix sort for integer
dtypes with kind="stable", so that pass is O(n)).  Equal-prefix runs of length 1 are
already fully resolved.  Only the runs of length >1 -- the duplicate candidates, plus any
accidental 64-bit prefix collision (expected count across 553M documents is n^2/2^65 ~=
0.008, so almost certainly none, but correctness must not rest on that) -- are re-sorted
exactly with a 4-key lexsort over the full digest.  The result is exact global digest
order for a fraction of the cost of sorting everything by full digest.

Confirmed on the real corpus: 542,305,160 prefix runs and 542,305,160 distinct digests,
i.e. zero accidental prefix collisions, as the estimate predicts.

Also writes ``dup_rows/shard_NNNNN.npy``: the row indices belonging to a group of size >=2,
which is the targeted work list stage 3 consumes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import shutil
import time

import numpy as np

from common import (OUT_DIR, DocIdFormatter, hex_digests, human, load_manifest, log,
                    sidecar_paths)

GROUP_CHUNK = 1_000_000  # groups formatted per write batch


def expand_ranges(starts, lens):
    """Vectorised equivalent of concatenating arange(s, s+l) for each (s, l)."""
    total = int(lens.sum())
    if total == 0:
        return np.empty(0, dtype=np.int64)
    out = np.ones(total, dtype=np.int64)
    out[0] = starts[0]
    if starts.shape[0] > 1:
        jump = np.cumsum(lens)[:-1]
        out[jump] = starts[1:] - (starts[:-1] + lens[:-1]) + 1
    return np.cumsum(out)


def load_all_digests(out_dir, manifest):
    total = int(manifest["offsets"][-1])
    digests = np.empty((total, 32), dtype=np.uint8)
    for i, s in enumerate(manifest["shards"]):
        lo, hi = int(manifest["offsets"][i]), int(manifest["offsets"][i + 1])
        arr = np.load(sidecar_paths(out_dir, s["num"])[1])
        if arr.shape[0] != hi - lo:
            raise SystemExit(
                f"shard {s['num']}: sidecar has {arr.shape[0]} rows, manifest says {hi-lo}. "
                "Delete the stale sidecar and rerun stage 1."
            )
        digests[lo:hi] = arr
    return digests


def be_u64(dig, lo):
    """bytes [lo, lo+8) of each digest as a big-endian uint64."""
    return np.ascontiguousarray(dig[:, lo : lo + 8]).view(">u8").ravel()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    t0 = time.time()
    man = load_manifest(args.out_dir)
    fmt = DocIdFormatter(man)
    offsets = man["offsets"]

    log("stage2b: loading digests")
    digests = load_all_digests(args.out_dir, man)
    n = digests.shape[0]
    log(f"  {n:,} digests ({human(digests.nbytes)})")

    log("stage2b: radix sort on 8-byte prefix")
    key = be_u64(digests, 0)
    order = np.argsort(key, kind="stable")
    sorted_key = key[order]
    del key

    chg = np.flatnonzero(sorted_key[1:] != sorted_key[:-1]) + 1
    run_start = np.concatenate([[0], chg])
    run_end = np.concatenate([chg, [n]])
    run_len = run_end - run_start
    del chg, run_end
    multi = np.flatnonzero(run_len > 1)
    log(f"  {run_start.shape[0]:,} prefix runs, {multi.shape[0]:,} with >1 member")

    # exact resolution, restricted to the ambiguous runs
    new_group = np.zeros(n, dtype=bool)
    new_group[0] = True
    new_group[1:] = sorted_key[1:] != sorted_key[:-1]
    del sorted_key

    if multi.shape[0]:
        log("stage2b: exact 4-key lexsort within ambiguous runs")
        cand_pos = expand_ranges(run_start[multi], run_len[multi])
        sub = order[cand_pos]
        sub_dig = digests[sub]
        perm = np.lexsort((be_u64(sub_dig, 24), be_u64(sub_dig, 16),
                           be_u64(sub_dig, 8), be_u64(sub_dig, 0)))
        sub = sub[perm]
        sub_dig = sub_dig[perm]
        order[cand_pos] = sub
        del sub, perm

        neq = np.any(sub_dig[1:] != sub_dig[:-1], axis=1)
        contig = cand_pos[1:] == cand_pos[:-1] + 1
        new_group[cand_pos[1:][neq & contig]] = True
        del sub_dig, neq, contig, cand_pos
    del run_start, run_len, multi

    group_start = np.flatnonzero(new_group)
    del new_group
    n_groups = group_start.shape[0]
    group_size = np.diff(np.concatenate([group_start, [n]]))
    log(f"  {n_groups:,} distinct sha256 values")

    # ---------------------------------------------------------------- JSONL
    path = os.path.join(args.out_dir, "hash_to_doc_ids.jsonl")
    tmp = path + ".tmp"
    log(f"stage2b: writing {path}")
    with open(tmp, "wb", buffering=1 << 24) as fh:
        for gs in range(0, n_groups, GROUP_CHUNK):
            ge = min(gs + GROUP_CHUNK, n_groups)
            lo = int(group_start[gs])
            hi = int(group_start[ge]) if ge < n_groups else n
            seg = order[lo:hi]
            ids = fmt.fixed_width(seg)
            hx = hex_digests(digests[order[group_start[gs:ge]]])
            sizes = group_size[gs:ge]
            rel = group_start[gs:ge] - lo
            parts = []
            for j in range(ge - gs):
                s, cnt = int(rel[j]), int(sizes[j])
                if cnt == 1:
                    parts.append(b'{"sha256":"%s","count":1,"doc_ids":["%s"]}\n'
                                 % (hx[j], ids[s]))
                else:
                    joined = b'","'.join(ids[s : s + cnt].tolist())
                    parts.append(b'{"sha256":"%s","count":%d,"doc_ids":["%s"]}\n'
                                 % (hx[j], cnt, joined))
            fh.write(b"".join(parts))
            if (gs // GROUP_CHUNK) % 50 == 0:
                log(f"    {ge:,}/{n_groups:,} groups")
    os.replace(tmp, path)
    del digests
    log(f"  wrote {human(os.path.getsize(path))}")

    # ---------------------------------------------------- duplicate work list
    dupg = np.flatnonzero(group_size > 1)
    n_dup_groups = dupg.shape[0]
    dup_pos = expand_ranges(group_start[dupg], group_size[dupg])
    dup_gidx = np.sort(order[dup_pos])
    n_dup_docs = dup_gidx.shape[0]
    log(f"stage2b: {n_dup_groups:,} groups with >=2 members, {n_dup_docs:,} documents")

    dup_dir = os.path.join(args.out_dir, "dup_rows")
    if os.path.isdir(dup_dir):
        shutil.rmtree(dup_dir)
    os.makedirs(dup_dir)
    shard_idx = np.searchsorted(offsets, dup_gidx, side="right") - 1
    bounds = np.searchsorted(shard_idx, np.arange(len(man["shards"]) + 1))
    n_shards_touched = 0
    for i, s in enumerate(man["shards"]):
        lo, hi = int(bounds[i]), int(bounds[i + 1])
        if hi <= lo:
            continue
        rows = (dup_gidx[lo:hi] - offsets[i]).astype(np.uint32)
        np.save(os.path.join(dup_dir, f"shard_{s['num']:05d}.npy"), rows)
        n_shards_touched += 1

    stats = {
        "total_docs": int(n),
        "distinct_sha256": int(n_groups),
        "duplicate_groups": int(n_dup_groups),
        "duplicate_docs": int(n_dup_docs),
        "redundant_docs": int(n_dup_docs - n_dup_groups),
        "duplicate_doc_rate": float(n_dup_docs) / n if n else 0.0,
        "largest_group": int(group_size.max()),
        "shards_with_duplicates": n_shards_touched,
    }
    with open(os.path.join(args.out_dir, "dup_stats.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
    log(f"stage2b done in {(time.time()-t0)/60:.1f}m: {json.dumps(stats)}")


if __name__ == "__main__":
    main()
