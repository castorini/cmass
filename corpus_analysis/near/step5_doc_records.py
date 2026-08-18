"""Near-dup step 5 -- one record per document: exact duplicates + near duplicates.

    {"doc_id": "shard_00188_76292",
     "exact_duplicates": ["shard_01204_00931"],
     "near_duplicates": [{"doc_id": "shard_03771_10233", "jaccard": 0.9012},
                         {"doc_id": "shard_05002_71188", "jaccard": 0.7431}],
     "n_exact": 1, "n_near": 2}

Only documents with at least one duplicate get a record; absence therefore means "no
duplicates", which keeps the file ~2.5x smaller than emitting 553M mostly-empty rows.

Two propagation rules make the records correct rather than merely convenient. The
near-duplicate pass ran only over exact-duplicate *representatives*, so:

  * a redundant exact copy inherits its representative's near-duplicate list -- it is
    byte-identical to it, so it has exactly the same near duplicates. Without this, looking
    up a judged document that happens to be a redundant copy would wrongly report no near
    duplicates at all;
  * each near-duplicate neighbour is expanded to its whole exact-duplicate group, at the
    same Jaccard -- if A is 0.84 similar to B and B has two byte-identical copies, A is
    0.84 similar to all three.

Exact and near lists are disjoint: a document's own exact-duplicate group never appears
among its near duplicates.

Threshold floor is 0.7, where LSH candidate recall is 95.5%. Lower thresholds were
deliberately excluded: recall falls to 64% at 0.6 and 24.6% at 0.5, so those ranges would be
substantially incomplete while appearing complete.

Reads the buckets nd4 retained (--keep-buckets); no re-shingling needed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import collections
import glob
import json
import struct
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from common import OUT_DIR, human, load_manifest, log
from near.nd_common import ND_DIR, pairwise_jaccard, rep_path

NBUCKETS = 512
HDR = 8 + 4 + 17
ROW_SPAN = 100000          # doc key = shard * ROW_SPAN + row  (max row 86015)

SCHEMA = pa.schema([
    ("doc_id", pa.string()),
    ("exact_duplicates", pa.list_(pa.string())),
    ("near_duplicates", pa.list_(pa.struct([("doc_id", pa.string()),
                                            ("jaccard", pa.float32())]))),
    ("n_exact", pa.int32()),
    ("n_near", pa.int32()),
])


def key_of(doc_id):
    return int(doc_id[6:11]) * ROW_SPAN + int(doc_id[12:17])


def id_of(key):
    return f"shard_{key // ROW_SPAN:05d}_{key % ROW_SPAN:05d}"


# ---------------------------------------------------------------- phase A: exact index

def build_exact_index(out_dir, nd_dir):
    """Flatten duplicate_groups.jsonl into CSR arrays keyed by doc key, and record whether
    each group's representative landed in a multi-member LSH component."""
    keys, gids, members, offs = [], [], [], [0]
    with open(os.path.join(out_dir, "duplicate_groups.jsonl")) as fh:
        for gi, line in enumerate(fh):
            ids = json.loads(line)["doc_ids"]
            ks = sorted(key_of(d) for d in ids)
            members.extend(ks)
            offs.append(len(members))
            keys.extend(ks)
            gids.extend([gi] * len(ks))
    keys = np.array(keys, dtype=np.int64)
    gids = np.array(gids, dtype=np.int32)
    order = np.argsort(keys, kind="stable")
    keys, gids = keys[order], gids[order]
    members = np.array(members, dtype=np.int64)
    offs = np.array(offs, dtype=np.int64)
    log(f"nd7A: {len(offs)-1:,} exact groups, {len(keys):,} member documents")

    # representative = member 0 of each group (lowest doc id)
    reps = members[offs[:-1]]

    # is that representative inside a multi-member component?
    man = load_manifest(out_dir)
    sh_offs = np.load(os.path.join(nd_dir, "shard_offsets.npy"))
    comp_id = np.load(os.path.join(nd_dir, "component_id.npy"), mmap_mode="r")
    comp_size = np.load(os.path.join(nd_dir, "component_size.npy"))
    in_comp = np.zeros(reps.shape[0], dtype=bool)
    by_shard = collections.defaultdict(list)
    for i, k in enumerate(reps):
        by_shard[int(k) // ROW_SPAN].append(i)
    num_to_idx = {s["num"]: i for i, s in enumerate(man["shards"])}
    for shard, idxs in by_shard.items():
        si = num_to_idx.get(shard)
        if si is None:
            continue
        rows = np.load(rep_path(shard, nd_dir))
        want = np.array([int(reps[i]) % ROW_SPAN for i in idxs], dtype=np.uint32)
        pos = np.searchsorted(rows, want)
        ok = (pos < rows.shape[0]) & (rows[np.minimum(pos, rows.shape[0]-1)] == want)
        g = int(sh_offs[si]) + pos
        cid = np.asarray(comp_id[g[ok]] if ok.any() else [])
        if cid.size:
            big = comp_size[cid] > 1
            sel = np.array(idxs)[ok]
            in_comp[sel[big]] = True
    log(f"nd7A: {int(in_comp.sum()):,} of {len(reps):,} representatives are in a "
        f"multi-member component")
    path = os.path.join(nd_dir, "exact_index.npz")
    np.savez(path, keys=keys, gids=gids, members=members, offs=offs, in_comp=in_comp)
    return path


class ExactIndex:
    def __init__(self, path):
        z = np.load(path)
        self.keys, self.gids = z["keys"], z["gids"]
        self.members, self.offs, self.in_comp = z["members"], z["offs"], z["in_comp"]

    def group_of(self, key):
        """All doc keys byte-identical to `key`, including itself; [] if it has no group."""
        p = np.searchsorted(self.keys, key)
        if p >= self.keys.shape[0] or self.keys[p] != key:
            return None
        g = int(self.gids[p])
        return self.members[self.offs[g]:self.offs[g + 1]]


# ---------------------------------------------------------------- phase B: components

def records_for_bucket(task):
    bucket, nd_dir, thr, max_comp, out_dir, idx_path = task
    dst = os.path.join(out_dir, f"part_{bucket:04d}.parquet")
    if os.path.exists(dst):
        return bucket, -1, 0, 0
    ix = ExactIndex(idx_path)

    comps = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(nd_dir, "cbuckets", "p*", f"b{bucket:04d}.bin"))):
        with open(p, "rb") as fh:
            buf = fh.read()
        pos, end = 0, len(buf)
        while pos < end:
            cid, ns = struct.unpack_from("<qI", buf, pos)
            doc_id = buf[pos + 12:pos + 29]
            sh = np.frombuffer(buf, dtype=np.uint64, count=ns, offset=pos + HDR)
            pos += HDR + ns * 8
            comps[cid].append((doc_id, sh))

    writer = pq.ParquetWriter(dst + ".tmp", SCHEMA, compression="zstd")
    D, E, N, CE, CN = [], [], [], [], []
    n_rows = n_skipped = n_edges = 0

    def flush():
        if not D:
            return
        writer.write_table(pa.table({"doc_id": D, "exact_duplicates": E,
                                     "near_duplicates": N, "n_exact": CE, "n_near": CN},
                                    schema=SCHEMA))
        D.clear(); E.clear(); N.clear(); CE.clear(); CN.clear()

    for cid, members in comps.items():
        k = len(members)
        if k < 2:
            continue
        if k > max_comp:
            n_skipped += 1
            continue
        members.sort(key=lambda m: m[0])
        rep_keys = [key_of(m[0].decode()) for m in members]
        groups = [ix.group_of(kk) for kk in rep_keys]
        flat = np.concatenate([m[1] for m in members])
        offs = np.cumsum([0] + [m[1].size for m in members]).astype(np.int64)
        sim = np.zeros((k, k), dtype=np.float32)
        pairwise_jaccard(flat, offs, sim)

        for i in range(k):
            nb = np.flatnonzero(sim[i] >= thr)
            nb = nb[nb != i]
            if nb.size == 0:
                continue
            # expand each neighbour over its exact-duplicate group, same Jaccard
            near = []
            for j in nb:
                s = float(sim[i, j])
                gj = groups[j]
                if gj is None:
                    near.append((rep_keys[j], s))
                else:
                    near.extend((int(x), s) for x in gj)
            near.sort(key=lambda t: (-t[1], t[0]))
            gi = groups[i]
            my_group = [int(x) for x in gi] if gi is not None else [rep_keys[i]]
            mine = set(my_group)
            near_ids = [{"doc_id": id_of(kk), "jaccard": s}
                        for kk, s in near if kk not in mine]
            # every member of this exact group shares these near duplicates
            for m in my_group:
                ex = [id_of(x) for x in my_group if x != m]
                D.append(id_of(m)); E.append(ex); N.append(near_ids)
                CE.append(len(ex)); CN.append(len(near_ids))
                n_rows += 1
                # count what is actually emitted: neighbours are expanded over their exact
                # groups above, and that expanded list is then inherited by every member of
                # this one. Counting nb.size here instead undercounts both expansions.
                n_edges += len(near_ids)
            if len(D) >= 200_000:
                flush()
    flush()
    writer.close()
    os.replace(dst + ".tmp", dst)
    return bucket, n_rows, n_skipped, n_edges


# ---------------------------------------------------------------- phase C: exact-only

def exact_only_records(nd_dir, out_dir, idx_path):
    """Every exact-duplicate document phase B did not already emit.

    Deliberately defined as "not already present" rather than "~in_comp": phase B skips a
    representative whose candidates all failed to verify, so ~in_comp is NOT the complement
    of what phase B wrote, and using it silently dropped 240,359 whole groups (600,145
    documents) whose exact duplicates were established by sha256 and never depended on
    near-duplicate verification. Scanning what was actually written costs a few minutes and
    cannot drift out of step with phase B's skip conditions.
    """
    ix = ExactIndex(idx_path)
    present = []
    for p in sorted(glob.glob(os.path.join(out_dir, "part_*.parquet"))):
        for b in pq.ParquetFile(p).iter_batches(columns=["doc_id"], batch_size=1_000_000):
            did = b.column("doc_id")
            sh = pc.cast(pc.utf8_slice_codeunits(did, 6, 11), "int64").to_numpy(
                zero_copy_only=False)
            rw = pc.cast(pc.utf8_slice_codeunits(did, 12, 17), "int64").to_numpy(
                zero_copy_only=False)
            present.append(sh * ROW_SPAN + rw)
    present = np.sort(np.concatenate(present)) if present else np.zeros(0, dtype=np.int64)
    pos = np.searchsorted(present, ix.members)
    have = (pos < present.size) & (present[np.minimum(pos, present.size - 1)] == ix.members)
    gid = np.repeat(np.arange(ix.offs.size - 1), np.diff(ix.offs))
    missing_per_group = np.bincount(gid[~have], minlength=ix.offs.size - 1)
    todo = np.flatnonzero(missing_per_group == np.diff(ix.offs))
    log(f"nd7C: {present.size:,} documents already written, "
        f"{todo.size:,} exact groups still to emit")

    dst = os.path.join(out_dir, "part_exact_only.parquet")
    writer = pq.ParquetWriter(dst + ".tmp", SCHEMA, compression="zstd")
    D, E, N, CE, CN = [], [], [], [], []
    n = 0
    for g in todo:
        grp = [int(x) for x in ix.members[ix.offs[g]:ix.offs[g + 1]]]
        for m in grp:
            ex = [id_of(x) for x in grp if x != m]
            D.append(id_of(m)); E.append(ex); N.append([])
            CE.append(len(ex)); CN.append(0)
            n += 1
        if len(D) >= 200_000:
            writer.write_table(pa.table({"doc_id": D, "exact_duplicates": E,
                                        "near_duplicates": N, "n_exact": CE,
                                        "n_near": CN}, schema=SCHEMA))
            D.clear(); E.clear(); N.clear(); CE.clear(); CN.clear()
    if D:
        writer.write_table(pa.table({"doc_id": D, "exact_duplicates": E,
                                    "near_duplicates": N, "n_exact": CE, "n_near": CN},
                                   schema=SCHEMA))
    writer.close()
    os.replace(dst + ".tmp", dst)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--nd-dir", default=ND_DIR)
    ap.add_argument("--threshold", type=float, default=0.7)
    ap.add_argument("--max-component", type=int, default=40000)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--rebuild-index", action="store_true")
    ap.add_argument("--also-jsonl", action="store_true")
    args = ap.parse_args()

    tag = int(args.threshold * 100)
    out_dir = os.path.join(args.nd_dir, f"doc_duplicates_t{tag}")
    os.makedirs(out_dir, exist_ok=True)
    if not glob.glob(os.path.join(args.nd_dir, "cbuckets", "p*", "b0000.bin")):
        raise SystemExit("no cbuckets -- rerun nd4_verify.py with --keep-buckets")

    t0 = time.time()
    idx_path = os.path.join(args.nd_dir, "exact_index.npz")
    if args.rebuild_index or not os.path.exists(idx_path):
        idx_path = build_exact_index(args.out_dir, args.nd_dir)

    log(f"nd7B: per-document records, near-dup threshold {args.threshold}, "
        f"{args.workers} workers -> {out_dir}")
    rows = skipped = edges = cached = 0
    with ProcessPoolExecutor(args.workers) as ex:
        futs = [ex.submit(records_for_bucket,
                          (b, args.nd_dir, args.threshold, args.max_component,
                           out_dir, idx_path)) for b in range(NBUCKETS)]
        for k, f in enumerate(as_completed(futs), 1):
            _, n, ns, ne = f.result()
            if n < 0:
                cached += 1
            else:
                rows += n
                skipped += ns
                edges += ne
            if k % 64 == 0 or k == NBUCKETS:
                log(f"  {k}/{NBUCKETS} buckets  {rows:,} records")

    log("nd7C: records for exact-duplicate groups with no near duplicates")
    n_exact_only = exact_only_records(args.nd_dir, out_dir, idx_path)

    # read the ACTUAL band layout rather than assuming one -- this figure is the file's
    # completeness claim, so it must match what nd2 really ran
    _nd2 = json.load(open(os.path.join(args.nd_dir, "nd2_stats.json")))
    _b, _r = _nd2["bands"], _nd2["rows"]
    size = sum(os.path.getsize(p) for p in glob.glob(os.path.join(out_dir, "*.parquet")))
    stats = {"threshold": args.threshold,
             "records_with_near_duplicates": rows,
             "records_exact_only": n_exact_only,
             "records_total": rows + n_exact_only,
             "directed_near_edges": edges,
             "oversized_components_skipped": skipped,
             "bands": _b, "rows": _r,
             "candidate_recall_at_threshold": 1 - (1 - args.threshold ** _r) ** _b,
             "bytes": size}
    with open(os.path.join(args.nd_dir, f"nd7_stats_t{tag}.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
    log(f"nd7 done in {(time.time()-t0)/60:.1f}m: {json.dumps(stats)} ({human(size)})")


if __name__ == "__main__":
    main()
