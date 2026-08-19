"""Near-dup step 4 -- exact verification and complete-linkage clustering.

LSH components are candidates built by single-linkage, so they guarantee only a chain of
shared band keys. This stage turns them into clusters where *every pair* is verified:

  Pass A (scatter)  re-read the documents in multi-member components, recompute their
      shingle sets, and append (component_id, doc_id, shingles) to a bucket keyed by
      component id, so a whole component always lands in one bucket.
  Pass B (gather)   per component, compute the EXACT all-pairs Jaccard matrix -- not just
      the LSH candidate edges, since complete-linkage needs adjacency for arbitrary pairs
      and building cliques on the candidate graph alone would split clusters wherever LSH
      missed an edge -- then greedily partition into cliques at each threshold.

MinHash signatures are used only to generate candidates; nothing here trusts an estimate.

Components above --max-component cannot be verified pairwise within a bounded budget
(cost is quadratic). They are written to oversized_components.jsonl and excluded from the
cluster output rather than clustered on weaker evidence.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import collections
import glob
import json
import shutil
import struct
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from common import OUT_DIR, human, load_manifest, log
from near.nd_common import ND_DIR, greedy_clique_partition, pairwise_jaccard, rep_path, shingles

NBUCKETS = 512
DEFAULT_THRESHOLDS = (0.7, 0.8, 0.9)


def scatter_batch(task):
    batch_id, items, nd_dir, corpus = task
    bdir = os.path.join(nd_dir, "cbuckets", f"p{batch_id:04d}")
    if os.path.exists(os.path.join(bdir, "_DONE")):
        return batch_id, -1, 0
    if os.path.isdir(bdir):
        shutil.rmtree(bdir)
    os.makedirs(bdir)

    comp_id = np.load(os.path.join(nd_dir, "component_id.npy"), mmap_mode="r")
    comp_size = np.load(os.path.join(nd_dir, "component_size.npy"), mmap_mode="r")
    offs = np.load(os.path.join(nd_dir, "shard_offsets.npy"))
    handles = [open(os.path.join(bdir, f"b{i:04d}.bin"), "wb", buffering=1 << 20)
               for i in range(NBUCKETS)]
    n_rec = n_bytes = 0
    try:
        for idx, num, path in items:
            reps = np.load(rep_path(num, nd_dir))
            g0, g1 = int(offs[idx]), int(offs[idx + 1])
            cids = np.asarray(comp_id[g0:g1])
            want = np.flatnonzero(np.asarray(comp_size)[cids] > 1)
            if want.size == 0:
                continue
            rows = reps[want].astype(np.int64)
            col = pq.read_table(path, columns=["text"]).column("text")
            texts = col.take(pa.array(rows)).to_pylist()
            bufs = collections.defaultdict(bytearray)
            for t, row, cid in zip(texts, rows, cids[want]):
                sh = shingles(t or "")
                if sh.size == 0:
                    continue
                doc_id = f"shard_{num:05d}_{int(row):05d}".encode()
                rec = (struct.pack("<qI", int(cid), sh.size) + doc_id + sh.tobytes())
                bufs[int(cid) % NBUCKETS] += rec
                n_rec += 1
                n_bytes += len(rec)
            for b, buf in bufs.items():
                handles[b].write(buf)
    finally:
        for h in handles:
            h.close()
    open(os.path.join(bdir, "_DONE"), "w").write("ok\n")
    return batch_id, n_rec, n_bytes


HDR = 8 + 4 + 17


def gather_bucket(task):
    bucket, nd_dir, max_comp, thresholds = task
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

    out = {t: [] for t in thresholds}
    over = []
    n_clusters = {t: 0 for t in thresholds}
    n_docs = {t: 0 for t in thresholds}
    single_link_docs = 0
    for cid, members in comps.items():
        k = len(members)
        if k < 2:
            continue
        single_link_docs += k
        if k > max_comp:
            over.append({"component": int(cid), "size": k,
                         "doc_ids": [m[0].decode() for m in members[:50]]})
            continue
        members.sort(key=lambda m: m[0])
        flat = np.concatenate([m[1] for m in members])
        offs = np.cumsum([0] + [m[1].size for m in members]).astype(np.int64)
        sim = np.zeros((k, k), dtype=np.float32)
        pairwise_jaccard(flat, offs, sim)
        for thr in thresholds:
            adj = sim >= thr
            order = np.argsort(-adj.sum(1), kind="stable").astype(np.int64)
            lab = greedy_clique_partition(sim, np.float32(thr), order)
            for c in np.unique(lab):
                idx = np.flatnonzero(lab == c)
                if idx.size < 2:
                    continue
                sub = sim[np.ix_(idx, idx)]
                iu = np.triu_indices(idx.size, 1)
                out[thr].append({
                    "component": int(cid), "size": int(idx.size),
                    "doc_ids": [members[i][0].decode() for i in idx],
                    "min_jaccard": round(float(sub[iu].min()), 4),
                    "mean_jaccard": round(float(sub[iu].mean()), 4),
                })
                n_clusters[thr] += 1
                n_docs[thr] += int(idx.size)

    d = os.path.join(nd_dir, "cluster_parts")
    for thr in thresholds:
        with open(os.path.join(d, f"t{int(thr*100)}_{bucket:04d}.jsonl"), "wb") as fh:
            for r in out[thr]:
                fh.write(json.dumps(r).encode() + b"\n")
    with open(os.path.join(d, f"over_{bucket:04d}.jsonl"), "wb") as fh:
        for r in over:
            fh.write(json.dumps(r).encode() + b"\n")
    return bucket, n_clusters, n_docs, len(over), single_link_docs


def concat(parts, dest):
    tmp = dest + ".tmp"
    with open(tmp, "wb") as out:
        for p in parts:
            with open(p, "rb") as fh:
                shutil.copyfileobj(fh, out, 1 << 22)
    os.replace(tmp, dest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--nd-dir", default=ND_DIR)
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--gather-workers", type=int, default=16)
    ap.add_argument("--max-component", type=int, default=10000)
    ap.add_argument("--keep-buckets", action="store_true")
    ap.add_argument("--scatter-only", action="store_true",
                    help="run pass A only; leaves buckets for nd6/nd7 and skips\n                          the clique partition entirely")
    ap.add_argument("--thresholds", default=",".join(str(t) for t in DEFAULT_THRESHOLDS),
                    help="comma-separated Jaccard thresholds")
    args = ap.parse_args()
    thresholds = tuple(float(x) for x in args.thresholds.split(","))

    t0 = time.time()
    man = load_manifest(args.out_dir)
    shards = [(i, s["num"], s["path"]) for i, s in enumerate(man["shards"])]
    os.makedirs(os.path.join(args.nd_dir, "cbuckets"), exist_ok=True)
    os.makedirs(os.path.join(args.nd_dir, "cluster_parts"), exist_ok=True)

    nb = args.workers
    log(f"nd4 pass A: re-shingling documents in multi-member components -> {NBUCKETS} buckets")
    recs = nbytes = 0
    with ProcessPoolExecutor(args.workers) as ex:
        futs = [ex.submit(scatter_batch, (b, shards[b::nb], args.nd_dir, man["corpus_dir"]))
                for b in range(nb)]
        for k, f in enumerate(as_completed(futs), 1):
            _, n, nby = f.result()
            if n >= 0:
                recs += n
                nbytes += nby
            if k % 8 == 0 or k == nb:
                log(f"  batch {k}/{nb}")
    log(f"  pass A: {recs:,} documents, {human(nbytes)} in {(time.time()-t0)/60:.1f}m")

    if args.scatter_only:
        log(f"nd4: scatter-only, buckets retained in {args.nd_dir}/cbuckets")
        with open(os.path.join(args.nd_dir, "nd4_scatter_stats.json"), "w") as fh:
            json.dump({"documents_scattered": recs, "bytes": nbytes}, fh, indent=2)
        return
    log(f"nd4 pass B: exact all-pairs Jaccard + clique partition "
        f"(max component {args.max_component:,})")
    tot_c = {t: 0 for t in thresholds}
    tot_d = {t: 0 for t in thresholds}
    n_over = sl_docs = 0
    with ProcessPoolExecutor(args.gather_workers) as ex:
        futs = [ex.submit(gather_bucket, (b, args.nd_dir, args.max_component, thresholds))
                for b in range(NBUCKETS)]
        for k, f in enumerate(as_completed(futs), 1):
            _, nc, nd_, no, sl = f.result()
            for t in thresholds:
                tot_c[t] += nc[t]
                tot_d[t] += nd_[t]
            n_over += no
            sl_docs += sl
            if k % 64 == 0 or k == NBUCKETS:
                log(f"  {k}/{NBUCKETS} buckets  t{thresholds[0]}: {tot_c[thresholds[0]]:,} clusters")

    cp = os.path.join(args.nd_dir, "cluster_parts")
    for thr in thresholds:
        concat([os.path.join(cp, f"t{int(thr*100)}_{b:04d}.jsonl") for b in range(NBUCKETS)],
               os.path.join(args.nd_dir, f"near_dup_clusters_t{int(thr*100)}.jsonl"))
    concat([os.path.join(cp, f"over_{b:04d}.jsonl") for b in range(NBUCKETS)],
           os.path.join(args.nd_dir, "oversized_components.jsonl"))
    shutil.rmtree(cp)
    if not args.keep_buckets:
        shutil.rmtree(os.path.join(args.nd_dir, "cbuckets"))

    stats = {"documents_scattered": recs,
             "documents_in_single_linkage_components": sl_docs,
             "oversized_components": n_over, "max_component": args.max_component,
             "thresholds": list(thresholds),
             "clusters": {str(t): tot_c[t] for t in thresholds},
             "documents_in_clusters": {str(t): tot_d[t] for t in thresholds}}
    tag = "" if tuple(thresholds) == DEFAULT_THRESHOLDS else "_" + "_".join(
        str(int(t*100)) for t in thresholds)
    with open(os.path.join(args.nd_dir, f"nd4_stats{tag}.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
    log(f"nd4 done in {(time.time()-t0)/60:.1f}m: {json.dumps(stats)}")


if __name__ == "__main__":
    main()
