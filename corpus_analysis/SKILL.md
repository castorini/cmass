---
name: corpus-analysis
description: Measure a parquet document corpus - token-length distribution, exact and near-duplicate detection, and how much survives deduplication. Use when asked to analyse corpus statistics, find duplicates, count tokens, compute what dedup would remove, or produce corpus figures for a paper. Triggers include "corpus stats", "duplicate detection", "near duplicates", "MinHash", "dedup", "how many tokens", "document length distribution", "ClimbMix analysis".
---

# Corpus analysis

Runs over `corpus_analysis/` in this repository. Everything is CLI-driven; every step takes
`--corpus-dir`, `--out-dir`, `--dry-run`, and is resumable.

## Pick the entry point by what is being asked

| ask | run |
|---|---|
| "how long are the documents" | `tokens/step0_tokenize.py` then `tokens/step1_length_stats.py` |
| "how many duplicates" | `analysis/duplicate_stats.py` (needs the near pipeline) |
| "what would dedup remove/retain" | `analysis/clique_retention.py` |
| "give me the figures" | `figures/make_figures.py` |
| "which documents duplicate this one" | query `near_dup/doc_duplicates_t70/` |

If the artifacts already exist, the analysis and figure steps are minutes. If they do not,
the pipeline is ~23 hours and ~2.5 TB — say so before starting, and use `--dry-run` to show
the cost first.

## Dependency order

```
exact/step0 → step1 → step2b → step3 → step4        (step2a optional cross-check)
                 ↓
near/step0 → step1 → step2 → step3 → step4 → step5 → step6
                 ↓
tokens/step0 → step1
                 ↓
analysis/*  →  figures/make_figures.py
```

`analysis/duplicate_composition.py`, `analysis/clique_retention.py` and
`analysis/dup_vs_length.py` need both the near pipeline and `tokens/step0`.

## Two views of duplication — do not conflate them

- **Affected** (`analysis/duplicate_composition.py`): documents that *have* a duplicate.
  39.60% on ClimbMix. Correct for review scope — if a judged document has a twin, both need
  looking at, and neither is privileged as "the original".
- **Removable** (`analysis/clique_retention.py`): documents you could *delete*, keeping one
  per cluster. 22.71%. Correct for characterising redundancy.

The 17-point gap is the cluster representatives. Reporting "affected" as a redundancy figure
overstates it badly.

## Things that will bite

**`np.save` appends `.npy`.** Writing to `dst + ".tmp"` produces `dst.tmp.npy`, and the
atomic rename then looks for a file that was never written. Temp names must already end in
`.npy`. This silently produced 6,543 mis-named files in one run — all the data was there, the
job just "failed" at the end.

**The `tokenizers` library spawns a rayon pool per process.** N workers × 96 threads thrashes
the machine — measured load average 1400+ on 96 cores, throughput far *below* one thread per
worker. `tokens/step0_tokenize.py` sets `RAYON_NUM_THREADS=1` before importing; keep that.

**Old `tokenizers` cannot load modern tokenizer.json.** Anything below 0.14 fails with "data
did not match any variant of untagged enum ModelWrapper". Environments pinning
`transformers<4.34` cap it there; use a separate virtualenv. Nothing here imports transformers.

**Documents under K words have no shingles.** With `SHINGLE_K=5`, a four-word document
produces an empty shingle set, gets no MinHash signature, and is invisible to near-duplicate
detection — it can only ever be caught as an exact duplicate. This is why a minimum-length
filter adds almost nothing on top of dedup, and the increment is identical at every threshold.

**Token counts are tokenizer-dependent by ~17%.** ClimbMix measures 410.6B under Llama-2,
356.9B under GPT-2, 350.0B under Qwen3 — the same text. Always name the tokenizer beside a
token count. Llama-2 is the default because it reproduces the corpus's nominal 400B.

**Greedy clique partition is order-dependent.** A document joins whichever cluster forms
first, not the one it is most similar to. The pairwise guarantee always holds, but removal
rates are lower bounds. State that if the number goes in a paper.

## Verifying before reporting

Do not report duplicate counts without a check that does not share code with the pipeline:

```bash
python verify/check_cliques.py      # scans every cluster; asserts min_jaccard >= threshold
python verify/bruteforce_counts.py  # counts groups by scanning the corpus directly
python verify/sample_records.py     # re-scores records with a reference Jaccard
```

`tests/` runs the whole thing on synthetic corpora in about a minute per suite and needs no
corpus.

## Artifact schemas

`near_dup/doc_duplicates_t70/` — parquet, one row per document with at least one duplicate.
Absence means no duplicates.

```
doc_id            string                                   "shard_00188_76292"
exact_duplicates  list<string>                             byte-identical documents
near_duplicates   list<struct<doc_id, jaccard: float32>>    exact Jaccard >= 0.7, descending
n_exact, n_near   int32
```

Both directions are present, so filtering on `doc_id` alone gives every neighbour. `jaccard`
is exact, not estimated, so any threshold ≥ 0.7 is a filter rather than a re-run. A value of
1.0 means identical *after normalisation* (lowercased, non-alphanumerics collapsed) — those
documents are not byte-identical, or they would be in `exact_duplicates`, and the two lists
are always disjoint.

`near_dup/near_dup_clusters_t{70,80,90}.jsonl` — complete-linkage clusters:

```json
{"component": 512, "size": 2, "doc_ids": ["shard_00000_00564", "shard_04893_50354"],
 "min_jaccard": 0.7209, "mean_jaccard": 0.7209}
```

`min_jaccard` is the verification handle: it is ≥ the threshold on every line.

`duplicate_groups.jsonl` — exact groups, one JSON object per line, `doc_ids` byte-identical.

## Extending to another corpus

Point `--corpus-dir` at any directory of `shard_NNNNN.parquet` files with a `text` column.
Doc ids are derived as `shard_{shard:05d}_{row:05d}`, so shards must be numbered and rows
under 100,000; both are checked by `exact/step0_manifest.py`.
