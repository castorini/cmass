"""Step 3 -- confirm every duplicate group by re-reading and byte-comparing real content.

The corpus is shuffled, so the members of a duplicate group sit in arbitrary far-apart
shards.  Fetching group-by-group would re-read the corpus once per group.  Instead this is
a bucket sort-merge costing exactly one corpus pass:

  Pass A (scatter, parallel over shards)  read only the rows stage 2b flagged, and append
      (sha256, doc_id, char_len, content) to bucket ``sha256[:2] >> 7``.  All members of a
      group therefore land in the same bucket index.
  Pass B (gather, parallel over buckets)  load one bucket (a few hundred MB), group by
      digest, and byte-compare every member against the group representative -- plus
      recompute the sha256 from the content, so the check is genuinely end-to-end and not
      just a restatement of stage 1.

Comparing UTF-8 bytes is equivalent to comparing the decoded strings (UTF-8 encoding is
injective over valid str), so no decode is needed.

Groups that fail to confirm go to verification_failures.jsonl -- flagged, never silently
dropped, since a mismatch means a pipeline bug (or an implausible sha256 collision).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import glob
import hashlib
import json
import shutil
import struct
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from common import OUT_DIR, DocIdFormatter, human, load_manifest, log, sidecar_paths

NBUCKETS = 512
BUCKET_SHIFT = 7           # (dig[0] << 8 | dig[1]) >> 7  ->  0..511
HDR = 32 + 17 + 4 + 4      # sha256, doc_id, char_len, byte_len


# ------------------------------------------------------------------ pass A: scatter

def scatter_batch(task):
    batch_id, items, out_dir = task
    bdir = os.path.join(out_dir, "buckets", f"p{batch_id:04d}")
    done_marker = os.path.join(bdir, "_DONE")
    if os.path.exists(done_marker):
        return batch_id, -1, 0
    if os.path.isdir(bdir):
        shutil.rmtree(bdir)  # partial batch from a killed run; redo it cleanly
    os.makedirs(bdir)

    man = load_manifest(out_dir)
    fmt = DocIdFormatter(man)
    offsets = man["offsets"]

    handles = [open(os.path.join(bdir, f"b{i:04d}.bin"), "wb", buffering=1 << 20)
               for i in range(NBUCKETS)]
    n_recs = n_bytes = 0
    try:
        for shard_idx, num, path in items:
            rows_path = os.path.join(out_dir, "dup_rows", f"shard_{num:05d}.npy")
            if not os.path.exists(rows_path):
                continue
            rows = np.load(rows_path).astype(np.int64)
            digests = np.load(sidecar_paths(out_dir, num)[1])[rows]
            clens = np.load(sidecar_paths(out_dir, num)[0])[rows]
            gidx = offsets[shard_idx] + rows
            doc_ids = fmt.fixed_width(gidx)

            col = pq.read_table(path, columns=["text"]).column("text")
            texts = col.take(pa.array(rows)).to_pylist()

            buckets = ((digests[:, 0].astype(np.int32) << 8)
                       | digests[:, 1].astype(np.int32)) >> BUCKET_SHIFT
            dig_bytes = digests.tobytes()
            bufs = {}
            for i, s in enumerate(texts):
                content = (s or "").strip().encode("utf-8")
                rec = (dig_bytes[i * 32:(i + 1) * 32] + doc_ids[i]
                       + struct.pack("<II", int(clens[i]), len(content)) + content)
                bufs.setdefault(int(buckets[i]), bytearray()).extend(rec)
                n_recs += 1
                n_bytes += len(rec)
            for b, buf in bufs.items():
                handles[b].write(buf)
    finally:
        for h in handles:
            h.close()
    with open(done_marker, "w") as fh:
        fh.write("ok\n")
    return batch_id, n_recs, n_bytes


# ------------------------------------------------------------------ pass B: gather

def gather_bucket(task):
    bucket, out_dir = task
    parts = sorted(glob.glob(os.path.join(out_dir, "buckets", "p*", f"b{bucket:04d}.bin")))
    groups = {}
    for p in parts:
        with open(p, "rb") as fh:
            buf = fh.read()
        pos, end = 0, len(buf)
        while pos < end:
            dig = buf[pos:pos + 32]
            doc_id = buf[pos + 32:pos + 49]
            clen, blen = struct.unpack_from("<II", buf, pos + 49)
            content = buf[pos + HDR:pos + HDR + blen]
            pos += HDR + blen
            groups.setdefault(dig, []).append((doc_id, clen, content))

    ok_path = os.path.join(out_dir, "verify_parts", f"ok_{bucket:04d}.jsonl")
    bad_path = os.path.join(out_dir, "verify_parts", f"bad_{bucket:04d}.jsonl")
    n_ok = n_bad = n_docs = 0
    with open(ok_path, "wb", buffering=1 << 22) as ok, \
         open(bad_path, "wb", buffering=1 << 20) as bad:
        for dig, members in groups.items():
            members.sort(key=lambda m: m[0])           # representative = lowest doc id
            rep_id, rep_clen, rep_content = members[0]
            hexdig = dig.hex().encode()
            ids = [m[0].decode() for m in members]
            n_docs += len(members)

            mismatched = [m[0].decode() for m in members[1:] if m[2] != rep_content]
            rehash_ok = hashlib.sha256(rep_content).digest() == dig
            if mismatched or not rehash_ok:
                n_bad += 1
                bad.write(json.dumps({
                    "sha256": hexdig.decode(),
                    "count": len(members),
                    "doc_ids": ids,
                    "representative": rep_id.decode(),
                    "content_mismatch": mismatched,
                    "sha256_recompute_ok": rehash_ok,
                }).encode() + b"\n")
            else:
                n_ok += 1
                ok.write(b'{"sha256":"%s","length":%d,"count":%d,"doc_ids":["%s"]}\n'
                         % (hexdig, rep_clen, len(members),
                            b'","'.join(m[0] for m in members)))
    return bucket, n_ok, n_bad, n_docs


# ------------------------------------------------------------------ driver

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
    ap.add_argument("--workers", type=int, default=48, help="pass A workers")
    ap.add_argument("--gather-workers", type=int, default=16,
                    help="pass B workers; each holds one bucket in RAM")
    ap.add_argument("--keep-buckets", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    man = load_manifest(args.out_dir)
    shards = [(i, s["num"], s["path"]) for i, s in enumerate(man["shards"])]

    # ---- pass A
    nbatch = args.workers
    batches = [(b, shards[b::nbatch], args.out_dir) for b in range(nbatch)]
    os.makedirs(os.path.join(args.out_dir, "buckets"), exist_ok=True)
    log(f"stage3 pass A: scattering flagged rows into {NBUCKETS} buckets "
        f"across {nbatch} batches")
    tot_recs = tot_bytes = 0
    with ProcessPoolExecutor(args.workers) as ex:
        futs = [ex.submit(scatter_batch, b) for b in batches]
        for k, fut in enumerate(as_completed(futs), 1):
            bid, nrec, nbytes = fut.result()
            if nrec >= 0:
                tot_recs += nrec
                tot_bytes += nbytes
            log(f"  batch {k}/{nbatch} done"
                + (" (cached)" if nrec < 0 else f" {nrec:,} recs"))
    log(f"  pass A: {tot_recs:,} records, {human(tot_bytes)} "
        f"in {(time.time()-t0)/60:.1f}m")

    # ---- pass B
    os.makedirs(os.path.join(args.out_dir, "verify_parts"), exist_ok=True)
    log(f"stage3 pass B: verifying {NBUCKETS} buckets "
        f"with {args.gather_workers} workers")
    n_ok = n_bad = n_docs = 0
    with ProcessPoolExecutor(args.gather_workers) as ex:
        futs = [ex.submit(gather_bucket, (b, args.out_dir)) for b in range(NBUCKETS)]
        for k, fut in enumerate(as_completed(futs), 1):
            _, ok, bad, docs = fut.result()
            n_ok += ok
            n_bad += bad
            n_docs += docs
            if k % 64 == 0 or k == NBUCKETS:
                log(f"  {k}/{NBUCKETS} buckets  confirmed={n_ok:,} failed={n_bad:,}")

    vp = os.path.join(args.out_dir, "verify_parts")
    concat([os.path.join(vp, f"ok_{b:04d}.jsonl") for b in range(NBUCKETS)],
           os.path.join(args.out_dir, "duplicate_groups.jsonl"))
    concat([os.path.join(vp, f"bad_{b:04d}.jsonl") for b in range(NBUCKETS)],
           os.path.join(args.out_dir, "verification_failures.jsonl"))
    shutil.rmtree(vp)
    if not args.keep_buckets:
        shutil.rmtree(os.path.join(args.out_dir, "buckets"))

    stats = {"confirmed_groups": n_ok, "failed_groups": n_bad, "documents_compared": n_docs}
    with open(os.path.join(args.out_dir, "verify_stats.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
    log(f"stage3 done in {(time.time()-t0)/60:.1f}m: {json.dumps(stats)}")
    if n_bad:
        log(f"WARNING: {n_bad:,} groups failed content verification -- "
            "see verification_failures.jsonl")


if __name__ == "__main__":
    main()
