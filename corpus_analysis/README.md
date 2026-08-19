# Corpus analysis: length distribution and duplicate detection

Measures a parquet document corpus end to end: how long its documents are, how much of it is
duplicated, and how much survives deduplication. Built for ClimbMix (`climbmix-400b`, 553M
documents) but corpus-agnostic — any directory of `shard_NNNNN.parquet` files with a `text`
column works.

Three things it answers:

- **Length.** Per-document token counts under any tokenizer, bucketised, with percentiles and
  a figure-ready histogram. Llama-2 by default.
- **Duplication.** Byte-identical groups (SHA-256, every group re-verified by direct byte
  comparison) and near duplicates (MinHash/LSH candidates, every reported pair scored by
  *exact* Jaccard on its shingle sets — no similarity here is a MinHash estimate).
- **What dedup costs.** Documents and tokens removed and retained at each Jaccard threshold,
  under **complete linkage**: near-duplicate relations are not treated as transitive, so every
  pair inside a cluster is verified at or above the threshold.

## Quick start

```bash
pip install -r requirements.txt

# where the corpus lives and where artifacts go (or pass --corpus-dir/--out-dir per step)
export CORPUS_DIR=/path/to/corpus
export CORPUS_OUT_DIR=/path/to/artifacts

python exact/step0_manifest.py --dry-run       # every step takes --dry-run
bash run_all.sh                                # the full pipeline, in order
python figures/make_figures.py                 # PDFs for print, PNGs for the repo
```

Every step is **resumable**: a shard or bucket whose output exists is skipped, so a killed run
picks up where it stopped.

## Steps

| step | what happens |
|---|---|
| `exact/step0_manifest.py` | enumerate shards, read row counts from parquet footers |
| `exact/step1_scan.py` | one parallel pass → per-shard length and SHA-256 sidecars |
| `exact/step2a_length_map.py` | length → doc ids (optional; an independent cross-check that the scan saw every document) |
| `exact/step2b_hash_map.py` | SHA-256 → doc ids, streamed to JSONL |
| `exact/step3_verify.py` | re-read every candidate group and byte-compare it |
| `exact/step4_summary.py` | `duplicate_groups.jsonl`, `summary.json`, reconciliation |
| `near/step0_representatives.py` | exclude redundant exact copies from the MinHash pass |
| `near/step1_signatures.py` | MinHash signatures (`--n-perm`, default 512) |
| `near/step2_band.py` | LSH banding (`--bands`/`--rows`, default 73×7) + prefilter slice |
| `near/step3_components.py` | LSH buckets → candidate components, with the merge prefilter |
| `near/step4_cliques.py` | exact all-pairs Jaccard per component → complete-linkage cliques |
| `near/step5_doc_records.py` | one record per document: exact + near duplicates (parquet) |
| `near/step6_summary.py` | cluster stats, drop lists, single- vs complete-linkage comparison |
| `tokens/step0_tokenize.py` | per-document token counts (`--tokenizer`, default `llama2`) |
| `tokens/step1_length_stats.py` | percentiles, buckets, histogram, length × duplication |
| `analysis/duplicate_stats.py` | duplicate rates per threshold + Jaccard histogram |
| `analysis/duplicate_composition.py` | corpus composition by strongest relationship + per-tier moments |
| `analysis/clique_retention.py` | **documents and tokens retained** per threshold |
| `analysis/dup_vs_length.py` | median document length vs duplicate count |
| `figures/make_figures.py` | all five figures, PDF + PNG |

## Verification

The numbers are checkable without trusting the pipeline:

```bash
python verify/check_cliques.py     # every cluster really is a clique (scans all cluster files)
python verify/sample_records.py    # re-score sampled records with a reference implementation
python verify/bruteforce_counts.py # count duplicate groups by scanning the corpus directly
```

`bruteforce_counts.py` shares nothing with the pipeline — no SHA-256, no MinHash, no manifest.
It globs the parquet files and compares strings. On ClimbMix it reproduced all six sampled
group sizes exactly (`Cats`: 3,424; the Elsevier "Fingerprint" template: 6,377).

`tests/` runs the whole pipeline on synthetic corpora with planted duplicates in about a
minute each — no corpus needed.

## Results on ClimbMix

`results/` holds the JSON these steps produced for `climbmix-400b`, and `figures/out/*.png`
the figures drawn from them. Together they let someone check every number without a 24-hour run.

| | |
|---|---|
| documents | 553,240,576 |
| tokens (Llama-2 / GPT-2 / Qwen3) | 410.6B / 356.9B / 350.0B |
| median length | 614 tokens (Llama-2) |
| exact duplicates | 16,845,444 documents in 5,910,028 groups |
| any duplicate at J ≥ 0.7 | 219,066,180 documents (39.60%) |
| removable at J ≥ 0.7 (cliques) | 125,647,591 documents (22.71%), 71.3B tokens (17.37%) |

## Released data

The per-document duplicate records are published as the `corpus_duplicates` config of
[castorini/cmass](https://huggingface.co/datasets/castorini/cmass) — 219,066,180 rows,
32 parquet files, 9.1 GB, sorted by `doc_id`.

```python
from datasets import load_dataset
ds = load_dataset("castorini/cmass", "corpus_duplicates")["corpus"]
```

`release/` holds the tooling that produced it: `repartition_for_hf.py` (sorts and
repartitions), `verify_release.py` (row counts, order-independent content checksum,
sortedness and range coverage — run this before any upload), and `DUPLICATES.md` (the reference
the dataset card links to). See
[`release/README.md`](release/) for why the released layout differs from the pipeline's, and
what that costs.

## Cost

Measured on 96 cores for ClimbMix's 553M documents / 600 GB of parquet.

| step | runtime | peak disk |
|---|---|---|
| exact steps 1–4 | ~1.5 h | 72 GB |
| near step1 signatures (512 perms) | ~3 h¹ | 1.0 TB |
| near step2 banding | 1.1 h | 456 GB |
| near step3 components | 4.9 h | 139 GB RAM |
| near step4 scatter + cliques | 1.9 h + 2.2 h | 790 GB |
| near step5 doc records | 1.2 h | 4.4 GB |
| tokens step0 (Llama-2) | 6.2 h | 2.2 GB |
| analysis + figures | < 1 h | — |
| **total** | **~23 h** | **~2.5 TB peak** |

¹ extrapolated: the ClimbMix run computed 256 permutations (2.7 h) then extended to 512
(2.2 h); a single 512-permutation pass shares the shingling and should land near 3 h.

Artifacts stay outside the repository. `near/step5_doc_records.py` alone writes 4.4 GB of
parquet, and the intermediate signature and bucket stores are hundreds of gigabytes each.

## Design notes

**Why 512 permutations at 73×7.** Candidate recall is `1-(1-J^r)^b`: 99.81% at J=0.7,
effectively 1.0 above 0.8. Re-banding a *fixed* permutation count buys recall by shortening
bands, which makes them less selective, so low-similarity junk rises faster than recall does.
Adding permutations moves to a sharper curve instead — 42×6 on 256 perms reaches only 99.48%
recall for roughly twice the junk at J=0.3.

**Why nothing is reported below J = 0.7.** Candidate recall falls to 43.6% at J=0.5. A
threshold there would be less than half complete while looking complete.

**Why complete linkage.** Single-linkage components chain: A and C land together because both
resemble B, though they were never compared. On ClimbMix that chained 155,009 unrelated
documents into one component whose sampled pairwise Jaccard was 0.000, and it inflates removal
by 1.0–1.5 points at every threshold. `near/step4_cliques.py` computes the exact all-pairs
matrix per component and partitions it into cliques instead.

**Removal figures are lower bounds.** Cliques are built greedily, seeded by degree; membership
goes to whichever cluster forms first, not the most similar one. Minimum clique partition is
NP-hard, so the greedy cover can leave more clusters than necessary — and more clusters means
fewer merges.

**One component is excluded.** Components above `--max-component` (default 40,000) cannot be
verified pairwise within a bounded budget, since cost is quadratic. They go to
`oversized_components.jsonl` rather than being clustered on weaker evidence. On ClimbMix that
is a single component of 91,408 documents whose sampled pairwise Jaccard is 0.000 — chained
boilerplate, so little real duplication is lost.

**Deviation from the repo's convention.** Other pipelines here put path constants at the top of
each script. These steps take `--corpus-dir`/`--out-dir` instead, because a run takes a day and
writes terabytes: pointing it at the wrong corpus is an expensive mistake to make silently.
