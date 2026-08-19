"""Test the per-document duplicate records, especially cross-group propagation.

Plants an exact-duplicate PAIR that is also a near-duplicate of other documents. Only one
member of that pair (the representative) went through the MinHash pass, so the records are
correct only if:

  * the redundant copy inherits the representative's near-duplicate list, and
  * documents near the representative list BOTH members of its exact group.

Also checks exact and near lists are disjoint, and that reported Jaccards match a reference
implementation that shares no code with the pipeline.

    python test_nd7.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import itertools
import re
import shutil
import subprocess
import tempfile

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N_SHARDS, ROWS, W = 4, 150, 600
NON = re.compile(r"[^a-z0-9]+")


def ref_shingles(t, k=5):
    w = NON.sub(" ", t.lower()).split()
    return {tuple(w[i:i + k]) for i in range(len(w) - k + 1)}


def ref_jac(a, b):
    return len(a & b) / len(a | b) if (a | b) else 0.0


def base(tag, n=W):
    return [f"{tag}w{i}" for i in range(n)]


def repl(words, start, length, tag):
    m = list(words)
    for i in range(start, min(start + length, len(m))):
        m[i] = f"{tag}x{i}"
    return m


def build(corpus):
    cells = [[" ".join(base(f"u{s}_{r}")) for r in range(ROWS)] for s in range(N_SHARDS)]
    fam = base("F")
    docs = {
        "F0": fam,
        "F1": repl(fam, 20, 8, "a"),
        "E0": repl(fam, 400, 10, "b"),     # near-dup of F0/F1 ...
        "E1": repl(fam, 400, 10, "b"),     # ... and byte-identical to E0
    }
    slots = {"F0": (0, 10), "F1": (1, 20), "E0": (0, 30), "E1": (2, 40)}
    planted = {}
    for name, w in docs.items():
        s, r = slots[name]
        cells[s][r] = " ".join(w)
        planted[name] = f"shard_{s:05d}_{r:05d}"
    for s in range(N_SHARDS):
        pq.write_table(pa.table({"text": cells[s]}),
                       os.path.join(corpus, f"shard_{s:05d}.parquet"), row_group_size=64)
    return planted, {k: " ".join(v) for k, v in docs.items()}


def run(script, out, extra=()):
    subprocess.run([sys.executable, os.path.join(HERE, script), "--out-dir", out, *extra],
                   check=True, cwd=HERE)


def main():
    tmp = tempfile.mkdtemp(prefix="climbmix_nd7_")
    corpus, out = os.path.join(tmp, "corpus"), os.path.join(tmp, "analysis")
    nd = os.path.join(out, "near_dup")
    os.makedirs(corpus)
    os.makedirs(out)
    try:
        planted, texts = build(corpus)
        truth = {}
        for a, b in itertools.combinations(sorted(texts), 2):
            truth[(a, b)] = truth[(b, a)] = ref_jac(ref_shingles(texts[a]),
                                                    ref_shingles(texts[b]))
        print("reference pairwise Jaccard:")
        for (a, b), j in sorted(truth.items()):
            if a < b:
                print(f"   {a}-{b}: {j:.4f}")

        for s, e in [("exact/step0_manifest.py", ["--corpus-dir", corpus]),
                     ("exact/step1_scan.py", ["--corpus-dir", corpus, "--workers", "2"]),
                     ("exact/step2a_length_map.py", []), ("exact/step2b_hash_map.py", []),
                     ("exact/step3_verify.py", ["--workers", "2", "--gather-workers", "2"])]:
            run(s, out, e)
        run("near/step0_representatives.py", out, ["--nd-dir", nd])
        run("near/step1_signatures.py", out, ["--nd-dir", nd, "--corpus-dir", corpus, "--workers", "2"])
        run("near/step2_band.py", out, ["--nd-dir", nd, "--workers", "2",
                                 "--prefilter-perms", "64"])
        run("near/step3_components.py", out, ["--nd-dir", nd,
                                       "--prefilter-threshold", "0.4"])
        run("near/step4_cliques.py", out, ["--nd-dir", nd, "--workers", "2", "--gather-workers", "2",
                                   "--keep-buckets", "--thresholds", "0.7"])
        run("near/step5_doc_records.py", out, ["--nd-dir", nd, "--workers", "2", "--threshold", "0.7"])

        rev = {v: k for k, v in planted.items()}
        tbl = ds.dataset(os.path.join(nd, "doc_duplicates_t70"), format="parquet").to_table()
        recs = {}
        for r in tbl.to_pylist():
            if r["doc_id"] in rev:
                recs[rev[r["doc_id"]]] = r
        print(f"\nrecords found for planted docs: {sorted(recs)}")
        for n in sorted(recs):
            r = recs[n]
            ex = sorted(rev.get(x, x) for x in r["exact_duplicates"])
            nr = sorted((rev.get(x["doc_id"], x["doc_id"]), round(x["jaccard"], 4))
                        for x in r["near_duplicates"])
            print(f"  {n}: exact={ex}  near={nr}")

        fail = []
        for n in ("F0", "F1", "E0", "E1"):
            if n not in recs:
                fail.append(f"{n} has no record at all")
        if fail:
            print("\nFAILURES:"); [print("  " + f) for f in fail]; raise SystemExit(1)

        def names(r, field):
            if field == "exact":
                return {rev.get(x, x) for x in r["exact_duplicates"]}
            return {rev.get(x["doc_id"], x["doc_id"]) for x in r["near_duplicates"]}

        # exact-duplicate symmetry
        if names(recs["E0"], "exact") != {"E1"}:
            fail.append(f"E0 exact should be {{E1}}, got {names(recs['E0'],'exact')}")
        if names(recs["E1"], "exact") != {"E0"}:
            fail.append(f"E1 exact should be {{E0}}, got {names(recs['E1'],'exact')}")
        if names(recs["F0"], "exact") or names(recs["F1"], "exact"):
            fail.append("F0/F1 should have no exact duplicates")

        # PROPAGATION: the redundant copy must inherit the representative's near list
        if names(recs["E0"], "near") != names(recs["E1"], "near"):
            fail.append(f"E0 and E1 near lists differ: {names(recs['E0'],'near')} vs "
                        f"{names(recs['E1'],'near')} -- propagation to the redundant copy failed")

        # EXPANSION: documents near the representative must list both group members
        for n in ("F0", "F1"):
            got = names(recs[n], "near")
            want = {x for x in ("F0", "F1", "E0", "E1") if x != n and truth[(n, x)] >= 0.7}
            if got != want:
                fail.append(f"{n} near should be {want}, got {got}")

        # disjointness + Jaccard accuracy
        for n, r in recs.items():
            both = names(r, "exact") & names(r, "near")
            if both:
                fail.append(f"{n}: {both} appears in BOTH exact and near lists")
            if r["n_exact"] != len(r["exact_duplicates"]) or r["n_near"] != len(r["near_duplicates"]):
                fail.append(f"{n}: n_exact/n_near disagree with list lengths")
            for x in r["near_duplicates"]:
                nm = rev.get(x["doc_id"], x["doc_id"])
                exp = truth.get((n, nm))
                if exp is not None and abs(exp - x["jaccard"]) > 0.002:
                    fail.append(f"{n}-{nm}: reported J={x['jaccard']:.4f} vs reference {exp:.4f}")

        if fail:
            print("\nFAILURES:")
            for f in fail:
                print("  " + f)
            raise SystemExit(1)
        print("\nPASS: exact/near lists correct and disjoint, redundant copy inherited the "
              "representative's near duplicates, neighbours expanded over exact groups, "
              "Jaccards match the reference implementation")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
