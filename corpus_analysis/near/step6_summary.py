"""Near-dup step 6 -- summary, drop lists, and the single- vs complete-linkage gap.

Reports at each threshold how much of the corpus is removable, and how far the verified
complete-linkage clusters diverge from the raw single-linkage components LSH produced.
That gap is the direct measurement of transitive chaining: every document inside a
component is connected by *some* chain of similar links, but only the clique members are
pairwise verified, and the difference is what a conventional connected-components pipeline
would have deleted on weaker evidence.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import time

import numpy as np

from common import OUT_DIR, human, log
from near.nd_common import ND_DIR

DEFAULT_THRESHOLDS = (0.7, 0.8, 0.9)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--nd-dir", default=ND_DIR)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--write-drop-lists", action="store_true", default=True)
    ap.add_argument("--thresholds", default=",".join(str(t) for t in DEFAULT_THRESHOLDS))
    ap.add_argument("--nd4-stats", default="nd4_stats.json")
    ap.add_argument("--out-name", default="near_dup_summary.json")
    args = ap.parse_args()
    THRESHOLDS = tuple(float(x) for x in args.thresholds.split(","))

    t0 = time.time()
    nd, P = args.nd_dir, lambda n: os.path.join(args.nd_dir, n)
    nd0 = json.load(open(P("nd0_stats.json")))
    nd1 = json.load(open(P("nd1_stats.json")))
    nd3 = json.load(open(P("nd3_stats.json")))
    nd4 = json.load(open(P(args.nd4_stats)))

    total = nd0["total_docs"]
    reps = nd0["representatives"]
    exact_removed = nd0["excluded_exact_duplicates"]

    per_thr = {}
    for thr in THRESHOLDS:
        path = P(f"near_dup_clusters_t{int(thr*100)}.jsonl")
        n_cl = n_docs = redundant = 0
        sizes = []
        mins = []
        biggest = []
        drop = open(P(f"drop_list_t{int(thr*100)}.txt"), "w") if args.write_drop_lists else None
        with open(path) as fh:
            for line in fh:
                c = json.loads(line)
                k = c["size"]
                n_cl += 1
                n_docs += k
                redundant += k - 1
                sizes.append(k)
                mins.append(c["min_jaccard"])
                if len(biggest) < args.top or k > biggest[-1][0]:
                    biggest.append((k, c["min_jaccard"], c["doc_ids"][:4]))
                    biggest.sort(key=lambda t: -t[0])
                    del biggest[args.top:]
                if drop:                      # keep the lowest doc id, drop the rest
                    for d in sorted(c["doc_ids"])[1:]:
                        drop.write(d + "\n")
        if drop:
            drop.close()
        sizes = np.array(sizes) if sizes else np.zeros(0, dtype=int)
        mins = np.array(mins) if mins else np.zeros(0)
        kept = reps - redundant
        per_thr[str(thr)] = {
            "clusters": n_cl,
            "documents_in_clusters": n_docs,
            "redundant_documents": redundant,
            "redundant_rate_of_corpus": redundant / total if total else 0.0,
            "corpus_after_exact_and_near_dedup": kept,
            "total_removable": total - kept,
            "total_removable_rate": (total - kept) / total if total else 0.0,
            "mean_cluster_size": float(sizes.mean()) if sizes.size else 0.0,
            "max_cluster_size": int(sizes.max()) if sizes.size else 0,
            "min_jaccard_p05": float(np.percentile(mins, 5)) if mins.size else None,
            "largest_clusters": [{"size": k, "min_jaccard": j, "doc_ids_sample": d}
                                 for k, j, d in biggest],
        }

    sl_docs = nd4["documents_in_single_linkage_components"]
    ref = str(THRESHOLDS[min(1, len(THRESHOLDS)-1)])
    cl_docs = per_thr[ref]["documents_in_clusters"]
    summary = {
        "corpus": total,
        "exact_duplicate_removal": {"removed": exact_removed, "remaining": reps},
        "signatures": {"documents": nd1["signatures"],
                       "too_short_for_shingling": nd1["too_short_for_shingling"],
                       "n_perm": nd1["n_perm"], "shingle_k": nd1["shingle_k"],
                       "bytes": nd1["signature_bytes"]},
        "lsh": {"bands": nd3["bands"], "components_ge2": nd3["components_ge2"],
                "documents_in_components": nd3["documents_in_components"],
                "largest_component": nd3["largest_components"][0]
                if nd3["largest_components"] else 0,
                "oversized_buckets_skipped": nd3["oversized_buckets_skipped"]},
        "oversized_components_unverified": nd4["oversized_components"],
        "by_threshold": per_thr,
        "chaining_gap_at_" + ref: {
            "documents_in_single_linkage_components": sl_docs,
            "documents_in_verified_cliques": cl_docs,
            "documents_a_connected_components_pipeline_would_have_merged_unverified":
                sl_docs - cl_docs,
        },
    }
    with open(P(args.out_name), "w") as fh:
        json.dump(summary, fh, indent=2)

    print(f"\n{'='*74}\nClimbMix-400B near-duplicate summary (word {nd1['shingle_k']}-grams, "
          f"{nd1['n_perm']} perms)\n{'='*74}")
    print(f"  corpus                      {total:>15,}")
    print(f"  after exact dedup           {reps:>15,}   (-{exact_removed:,})")
    print(f"  LSH components (>=2)        {nd3['components_ge2']:>15,}"
          f"   {nd3['documents_in_components']:,} docs, largest "
          f"{nd3['largest_components'][0] if nd3['largest_components'] else 0:,}")
    if nd4["oversized_components"]:
        print(f"  oversized, NOT verified     {nd4['oversized_components']:>15,}"
              f"   (see oversized_components.jsonl)")
    print(f"{'-'*74}")
    print(f"  {'thr':>4} {'clusters':>13} {'redundant docs':>16} {'% corpus':>9} "
          f"{'corpus after':>15}")
    for thr in THRESHOLDS:
        s = per_thr[str(thr)]
        print(f"  {thr:>4} {s['clusters']:>13,} {s['redundant_documents']:>16,} "
              f"{100*s['redundant_rate_of_corpus']:>8.2f}% "
              f"{s['corpus_after_exact_and_near_dedup']:>15,}")
    g = summary["chaining_gap_at_" + ref]
    print(f"{'-'*74}")
    print(f"  chaining gap at {ref}: {g['documents_in_single_linkage_components']:,} docs sit in "
          f"single-linkage components,\n    but only {g['documents_in_verified_cliques']:,} "
          f"are in pairwise-verified cliques -- a connected-components\n    pipeline would "
          f"have deleted "
          f"{g['documents_a_connected_components_pipeline_would_have_merged_unverified']:,} "
          f"documents on unverified evidence.")
    print(f"{'='*74}\n")
    log(f"nd5 done in {(time.time()-t0)/60:.1f}m -> {P(args.out_name)}")


if __name__ == "__main__":
    main()
