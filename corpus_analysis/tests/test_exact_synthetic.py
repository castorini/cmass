"""End-to-end test on a synthetic corpus with planted duplicates.

Builds a small parquet corpus whose duplicate structure is known exactly, runs the real
pipeline over it, and asserts the recovered groups match the planted ones.  Includes
whitespace-only variants, which must group together because every stage strips first, and
a same-shard duplicate pair, which the shuffled-corpus assumption must not miss.

    python test_synthetic.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import shutil
import subprocess
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N_SHARDS, ROWS = 5, 100

# (text, [(shard, row), ...]) -- planted groups; whitespace variants must still group
PLANTS = [
    ("alpha document about climbing",
     [(0, 3), (1, 17), (3, 60), (4, 99)],
     ["", "  ", "\n\t", "   \n  "]),                 # per-copy whitespace padding
    ("beta document", [(0, 50), (2, 50)], ["", "\n"]),
    ("gamma same shard twice", [(1, 5), (1, 6), (1, 7)], ["", " ", "\t\n"]),
]


def build_corpus(d):
    cells = [[f"unique document number {s*ROWS+r} lorem ipsum" for r in range(ROWS)]
             for s in range(N_SHARDS)]
    expected = {}
    for text, locs, pads in PLANTS:
        ids = []
        for (s, r), pad in zip(locs, pads):
            cells[s][r] = pad + text + pad
            ids.append(f"shard_{s:05d}_{r:05d}")
        expected[text] = sorted(ids)
    for s in range(N_SHARDS):
        pq.write_table(pa.table({"text": cells[s]}),
                       os.path.join(d, f"shard_{s:05d}.parquet"), row_group_size=32)
    return expected


def run(stage, out, extra=()):
    subprocess.run([sys.executable, os.path.join(HERE, stage), "--out-dir", out, *extra],
                   check=True, cwd=HERE)


def main():
    tmp = tempfile.mkdtemp(prefix="climbmix_test_")
    corpus, out = os.path.join(tmp, "corpus"), os.path.join(tmp, "analysis")
    os.makedirs(corpus)
    os.makedirs(out)
    try:
        expected = build_corpus(corpus)
        run("exact/step0_manifest.py", out, ["--corpus-dir", corpus])
        run("exact/step1_scan.py", out, ["--corpus-dir", corpus, "--workers", "2"])
        run("exact/step2a_length_map.py", out)
        run("exact/step2b_hash_map.py", out)
        run("exact/step3_verify.py", out, ["--workers", "2", "--gather-workers", "2"])
        run("exact/step4_summary.py", out)

        total = N_SHARDS * ROWS
        groups = [json.loads(l) for l in open(os.path.join(out, "duplicate_groups.jsonl"))]
        fails = open(os.path.join(out, "verification_failures.jsonl")).read().strip()
        summary = json.load(open(os.path.join(out, "summary.json")))

        got = sorted(sorted(g["doc_ids"]) for g in groups)
        want = sorted(sorted(v) for v in expected.values())
        assert got == want, f"\n  planted: {want}\n  found:   {got}"
        assert not fails, f"unexpected verification failures:\n{fails}"
        assert summary["total_documents"] == total
        assert all(summary["checks"].values()), summary["checks"]

        for g in groups:
            text = next(t for t, ids in expected.items()
                        if sorted(ids) == sorted(g["doc_ids"]))
            assert g["length"] == len(text), (g["length"], len(text), text)
            assert g["count"] == len(expected[text])

        # every document must appear exactly once in each map
        n_len_ids = open(os.path.join(out, "length_to_doc_ids.json")).read().count('"shard_')
        n_hash_ids = sum(len(json.loads(l)["doc_ids"])
                         for l in open(os.path.join(out, "hash_to_doc_ids.jsonl")))
        assert n_len_ids == total, n_len_ids
        assert n_hash_ids == total, n_hash_ids

        print(f"\nPASS: {len(groups)} planted groups recovered exactly, "
              f"{total} documents accounted for in both maps, 0 verification failures\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
