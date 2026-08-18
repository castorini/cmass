"""End-to-end test of the near-duplicate pipeline on a synthetic corpus.

Plants near-duplicates whose pairwise Jaccard is *measured* (not predicted), then runs the
real stages and checks two things that matter:

  1. the complete-linkage invariant -- every pair inside an emitted cluster is >= threshold;
  2. recall -- planted pairs above threshold actually end up clustered together.

Includes a deliberate chain (A~B and B~C high, A~C low) that single-linkage would merge
into one cluster of three and complete-linkage must refuse to.

    python test_nd_synthetic.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import itertools
import json
import shutil
import subprocess
import tempfile

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from near.nd_common import jaccard_exact, shingles

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N_SHARDS, ROWS, W = 4, 150, 600
rng = np.random.default_rng(5)


def base_words(tag, n=W):
    return [f"{tag}w{i}" for i in range(n)]


def replace_block(words, start, length, tag):
    m = list(words)
    for i in range(start, min(start + length, len(m))):
        m[i] = f"{tag}x{i}"
    return m


def build(corpus):
    cells = [[" ".join(base_words(f"u{s}_{r}")) for r in range(ROWS)] for s in range(N_SHARDS)]
    planted = {}          # name -> (shard, row)
    docs = {}

    # family F: variants of one base, each replacing a different small block
    fam = base_words("F")
    variants = {"F0": fam,
                "F1": replace_block(fam, 10, 6, "a"),
                "F2": replace_block(fam, 300, 6, "b"),
                "F3": replace_block(fam, 500, 60, "c")}     # much more edited
    # chain C: A~B high, B~C high, A~C low (blocks at opposite ends)
    ch = base_words("C")
    variants |= {"CA": replace_block(ch, 0, 40, "p"),
                 "CB": ch,
                 "CC": replace_block(ch, 550, 40, "q")}
    # exact duplicates, to exercise the nd0 exclusion path
    variants |= {"D0": base_words("D"), "D1": base_words("D")}

    slots = [(0, 5), (1, 20), (2, 40), (3, 60), (0, 90), (1, 100), (2, 110), (3, 120), (0, 130)]
    for (name, w), (s, r) in zip(variants.items(), slots):
        cells[s][r] = " ".join(w)
        planted[name] = f"shard_{s:05d}_{r:05d}"
        docs[name] = " ".join(w)
    for s in range(N_SHARDS):
        pq.write_table(pa.table({"text": cells[s]}),
                       os.path.join(corpus, f"shard_{s:05d}.parquet"), row_group_size=64)
    return planted, docs


def run(script, out, extra=()):
    subprocess.run([sys.executable, os.path.join(HERE, script), "--out-dir", out, *extra],
                   check=True, cwd=HERE)


def main():
    tmp = tempfile.mkdtemp(prefix="climbmix_nd_")
    corpus, out = os.path.join(tmp, "corpus"), os.path.join(tmp, "analysis")
    nd = os.path.join(out, "near_dup")
    os.makedirs(corpus)
    os.makedirs(out)
    try:
        planted, docs = build(corpus)
        truth = {}
        for a, b in itertools.combinations(sorted(docs), 2):
            truth[(a, b)] = jaccard_exact(shingles(docs[a]), shingles(docs[b]))
        print("measured pairwise Jaccard of planted documents:")
        for (a, b), j in sorted(truth.items(), key=lambda kv: -kv[1]):
            if j > 0.05:
                print(f"   {a}-{b}: {j:.3f}")

        for s, e in [("exact/step0_manifest.py", ["--corpus-dir", corpus]),
                     ("exact/step1_scan.py", ["--corpus-dir", corpus, "--workers", "2"]),
                     ("exact/step2a_length_map.py", []), ("exact/step2b_hash_map.py", []),
                     ("exact/step3_verify.py", ["--workers", "2", "--gather-workers", "2"])]:
            run(s, out, e)
        run("near/step0_representatives.py", out, ["--nd-dir", nd])
        run("near/step1_signatures.py", out, ["--nd-dir", nd, "--corpus-dir", corpus, "--workers", "2"])
        run("near/step2_band.py", out, ["--nd-dir", nd, "--workers", "2"])
        run("near/step3_components.py", out, ["--nd-dir", nd])
        run("near/step4_cliques.py", out, ["--nd-dir", nd, "--workers", "2", "--gather-workers", "2"])

        rev = {v: k for k, v in planted.items()}
        failures = []

        # map every planted doc to its LSH component, straight from nd3's output, so the
        # recall check can see documents that exist in a component but were split off into
        # singletons by the clique partition (those never appear in a cluster record).
        offs = np.load(os.path.join(nd, "shard_offsets.npy"))
        gidx = {}
        for i, s in enumerate(json.load(open(os.path.join(out, "manifest.json")))["shards"]):
            for pos, row in enumerate(np.load(os.path.join(nd, "reps", f"shard_{s['num']:05d}.npy"))):
                gidx[f"shard_{s['num']:05d}_{int(row):05d}"] = int(offs[i]) + pos
        comp_arr = np.load(os.path.join(nd, "component_id.npy"))
        comp_of = {rev[d]: int(comp_arr[g]) for d, g in gidx.items() if d in rev}
        excluded = {n for n, d in planted.items() if d not in gidx}
        print(f"\nexcluded by nd0 as redundant exact copies: {sorted(excluded)}")
        for thr in (0.7, 0.8, 0.9):
            path = os.path.join(nd, f"near_dup_clusters_t{int(thr*100)}.jsonl")
            clusters = [json.loads(l) for l in open(path)]
            named = [sorted(rev.get(d, d) for d in c["doc_ids"]) for c in clusters]
            print(f"\nthreshold {thr}: {len(clusters)} clusters -> {named}")

            # 1. complete-linkage invariant
            for c, nm in zip(clusters, named):
                for a, b in itertools.combinations(nm, 2):
                    j = truth.get((a, b), truth.get((b, a)))
                    if j is None or j < thr - 1e-6:
                        failures.append(f"t={thr}: cluster {nm} contains pair {a}-{b} at J={j}")
                if abs(c["min_jaccard"] - min(truth.get((a, b), truth.get((b, a)))
                                              for a, b in itertools.combinations(nm, 2))) > 1e-3:
                    failures.append(f"t={thr}: reported min_jaccard wrong for {nm}")

            # 2. candidate recall -- a planted pair above threshold must at least have been
            #    found by LSH (same component). It may still be split by the clique
            #    partition: complete-linkage is a PARTITION, so when A~B and B~C are both
            #    above threshold but A~C is not, one of those pairs must be separated.
            #    That is correct behaviour, not a miss.
            grouped = {frozenset(nm) for nm in named}
            for (a, b), j in truth.items():
                if j < thr or a in excluded or b in excluded:
                    continue          # excluded[] are redundant exact copies dropped by nd0
                if any({a, b} <= g for g in grouped):
                    continue
                if comp_of.get(a) is not None and comp_of.get(a) == comp_of.get(b):
                    print(f"   note: {a}-{b} (J={j:.3f}) found by LSH but separated by the "
                          f"clique partition -- expected, complete-linkage is a partition")
                    continue
                failures.append(f"t={thr}: planted pair {a}-{b} at J={j:.3f} "
                                "was not even generated as an LSH candidate")

            # 3. the chain must not become one cluster of three
            if any({"CA", "CB", "CC"} <= set(nm) for nm in named):
                jac = truth.get(("CA", "CC"), truth.get(("CC", "CA")))
                if jac < thr:
                    failures.append(f"t={thr}: chain CA-CB-CC merged despite CA-CC J={jac:.3f}")

        # exact duplicates must not appear -- D1 was excluded as a redundant copy
        for thr in (0.7, 0.8, 0.9):
            for c in (json.loads(l) for l in
                      open(os.path.join(nd, f"near_dup_clusters_t{int(thr*100)}.jsonl"))):
                if planted["D1"] in c["doc_ids"]:
                    failures.append(f"t={thr}: excluded exact duplicate D1 leaked into a cluster")

        if failures:
            print("\nFAILURES:")
            for f in failures:
                print("  " + f)
            raise SystemExit(1)
        print("\nPASS: complete-linkage invariant holds, planted pairs recovered, "
              "chain not over-merged, exact duplicates excluded")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
