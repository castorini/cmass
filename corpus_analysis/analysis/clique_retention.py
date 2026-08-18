#!/usr/bin/env python3
"""How many documents and tokens survive deduplication, per Jaccard threshold.

Clustering is COMPLETE LINKAGE. Every pair inside a cluster is verified at or above the
threshold -- near-duplicate relations are NOT treated as transitive, so a document is never
removed on the strength of a chain through an intermediate it was never compared to. Each
cluster's reported ``min_jaccard`` is asserted against the threshold here rather than
assumed; a violation aborts the run instead of producing a number.

That distinction is worth real percentage points. On ClimbMix at J >= 0.7, connected
components remove 133,566,253 documents while cliques remove 125,647,591 -- the 7.9M
difference is chaining.

Removal proceeds in two stages so nothing is double counted:

  1. exact duplicates -- keep the lowest doc_id of each sha256 group, remove the rest.
     Threshold-independent;
  2. near duplicates  -- among the representatives forming a clique, keep the lowest
     doc_id and remove the others, whose own exact copies stage 1 already removed.

Optionally a minimum-length floor is applied afterwards, reported as the ADDITIONAL
documents it removes beyond dedup. On ClimbMix that increment is tiny (77,048 documents
under 5 Llama-2 tokens), and the reason is structural rather than incidental: a document
with fewer than K words produces an empty shingle set, so it never receives a MinHash
signature and can only ever be caught as an exact duplicate.

Writes two artifacts -- the redundancy breakdown used by the removable-documents figure,
and the token savings table.

Note on optimality: cliques are built greedily (see near/nd_common.greedy_clique_partition),
seeding by degree. Minimum clique partition is NP-hard, so the greedy cover can leave more
clusters than necessary; more clusters means fewer merges, which makes every removal figure
here a LOWER bound.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time

import numpy as np

import config
from common import load_manifest, log

ROW_SPAN = 100000


def main():
    ap = config.base_parser(__doc__)
    ap.add_argument("--tokenizer", default=config.DEFAULT_TOKENIZER,
                    choices=sorted(config.TOKENIZERS))
    ap.add_argument("--thresholds", default=",".join(str(t) for t in config.THRESHOLDS),
                    help="comma-separated Jaccard thresholds (default: %(default)s)")
    ap.add_argument("--min-tokens", type=int, default=5,
                    help="short-document floor applied after dedup; 0 disables "
                         "(default: %(default)s)")
    a = ap.parse_args()
    thresholds = tuple(float(x) for x in a.thresholds.split(","))
    nd = config.near_dir(a.out_dir)

    man = load_manifest(a.out_dir)
    offs, nums = man["offsets"], man["shard_nums"]
    pos = np.full(int(nums.max()) + 1, -1, dtype=np.int64)
    pos[nums] = np.arange(nums.size)
    N = int(offs[-1])

    if a.dry_run:
        print(f"documents  : {N:,}")
        print(f"thresholds : {thresholds}")
        print(f"tokenizer  : {a.tokenizer}")
        for t in thresholds:
            p = os.path.join(nd, f"near_dup_clusters_t{int(t*100)}.jsonl")
            sz = os.path.getsize(p) / 1e9 if os.path.exists(p) else 0
            print(f"  reads {p}  ({sz:.1f} GB)")
        return

    tok_dir = config.token_dir(a.out_dir, a.tokenizer)
    lens = np.concatenate([np.load(os.path.join(tok_dir, f"shard_{s['num']:05d}.npy"))
                           for s in man["shards"]]).astype(np.int64)
    total_tokens = int(lens.sum())
    log(f"{N:,} documents, {total_tokens:,} {a.tokenizer} tokens "
        f"({total_tokens/1e9:.1f}B)")

    def gidx(keys):
        return offs[pos[keys // ROW_SPAN]] + (keys % ROW_SPAN)

    def key(doc_id):
        return int(doc_id[6:11]) * ROW_SPAN + int(doc_id[12:17])

    # ---- stage 1: exact duplicates (threshold-independent)
    z = np.load(os.path.join(nd, "exact_index.npz"))
    members, gofs = z["members"], z["offs"]
    drop = np.ones(members.size, dtype=bool)
    drop[gofs[:-1]] = False                       # keep the lowest doc_id of each group
    base = np.zeros(N, dtype=bool)
    base[gidx(members[drop])] = True
    ex_docs, ex_tokens = int(base.sum()), int(lens[base].sum())
    n_groups = int(gofs.size - 1)
    log(f"exact: {ex_docs:,} documents, {ex_tokens:,} tokens "
        f"({ex_tokens/total_tokens*100:.2f}%) in {n_groups:,} groups")

    red = {"corpus_documents": N, "linkage": "complete (clique; all pairs >= J)",
           "exact": {"groups": n_groups, "redundant_copies": ex_docs}}
    sav = {"corpus_documents": N, "corpus_tokens": total_tokens,
           "tokenizer": config.TOKENIZERS[a.tokenizer][0], "tokenizer_key": a.tokenizer,
           "linkage": "complete (clique; all pairs >= J)",
           "short_floor_tokens": a.min_tokens,
           "exact": {"documents_removed": ex_docs, "tokens_removed": ex_tokens},
           "thresholds": {}}

    for t in thresholds:
        t0 = time.time()
        removed = base.copy()
        path = os.path.join(nd, f"near_dup_clusters_t{int(t*100)}.jsonl")
        n_cl = n_merged = 0
        worst = 1.0
        buf = []
        with open(path) as fh:
            for line in fh:
                r = json.loads(line)
                ids = r["doc_ids"]
                if len(ids) < 2:
                    continue
                worst = min(worst, r["min_jaccard"])
                n_cl += 1
                n_merged += len(ids) - 1
                ks = sorted(key(d) for d in ids)
                buf.extend(ks[1:])                # keep the lowest, drop the rest
                if len(buf) >= 2_000_000:
                    removed[gidx(np.array(buf, dtype=np.int64))] = True
                    buf.clear()
        if buf:
            removed[gidx(np.array(buf, dtype=np.int64))] = True
        if worst < t - 1e-9:
            raise SystemExit(f"clique below threshold at J={t}: min_jaccard {worst}")

        d_rm, t_rm = int(removed.sum()), int(lens[removed].sum())
        s_docs = s_tokens = 0
        if a.min_tokens:
            short = (lens < a.min_tokens) & ~removed
            s_docs, s_tokens = int(short.sum()), int(lens[short].sum())
        both_d, both_t = d_rm + s_docs, t_rm + s_tokens

        red[f"J>={t}"] = {
            "cliques": n_cl, "representatives_merged": n_merged,
            "min_jaccard_observed": round(worst, 4),
            "redundant_copies": d_rm, "redundant_share": round(d_rm / N, 5),
            "retained": N - d_rm}
        sav["thresholds"][str(t)] = {
            "cliques": n_cl, "min_jaccard_observed": round(worst, 4),
            "dedup": {"documents_removed": d_rm, "tokens_removed": t_rm,
                      "document_share": round(d_rm / N, 5),
                      "token_share": round(t_rm / total_tokens, 5)},
            "short_filter_additional": {"documents_removed": s_docs,
                                        "tokens_removed": s_tokens},
            "combined": {"documents_removed": both_d, "tokens_removed": both_t,
                         "document_share": round(both_d / N, 5),
                         "token_share": round(both_t / total_tokens, 5),
                         "documents_retained": N - both_d,
                         "tokens_retained": total_tokens - both_t}}

        log(f"J>={t}: {n_cl:,} cliques, min pairwise Jaccard {worst:.4f} "
            f"({time.time()-t0:.0f}s)")
        print(f"   dedup         {d_rm:>13,} docs {d_rm/N*100:6.2f}%   "
              f"{t_rm:>15,} tok {t_rm/total_tokens*100:6.2f}%")
        if a.min_tokens:
            print(f"   +<{a.min_tokens} tokens   {s_docs:>13,} docs {s_docs/N*100:6.2f}%   "
                  f"{s_tokens:>15,} tok {s_tokens/total_tokens*100:6.3f}%")
        print(f"   RETAINED      {N-both_d:>13,} docs {(N-both_d)/N*100:6.2f}%   "
              f"{total_tokens-both_t:>15,} tok "
              f"{(total_tokens-both_t)/total_tokens*100:6.2f}%")

    for path, obj in ((os.path.join(a.out_dir, "corpus_redundancy_complete.json"), red),
                      (os.path.join(a.out_dir, "corpus_dedup_token_savings.json"), sav)):
        with open(path, "w") as fh:
            json.dump(obj, fh, indent=2)
        log(f"wrote {path}")


if __name__ == "__main__":
    main()
