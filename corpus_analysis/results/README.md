# Results — the ClimbMix run

Snapshot of what this pipeline produced for `climbmix-400b` (553,240,576 documents). The
code never reads these; it reads from `--out-dir`. They are here so every number in the
paper can be checked, and the figures redrawn, without a 24-hour run.

To redraw the figures from this snapshot:

```bash
python figures/make_figures.py --out-dir results --fig-dir figures/out
```

| file | what it holds | needed for |
|---|---|---|
| `corpus_dup_chart_data.json` | corpus by strongest duplicate relationship | figure: affected pie |
| `corpus_redundancy_complete.json` | removable documents per threshold (cliques) | figure: removable pie |
| `corpus_dup_moments.json` | duplicate-count percentiles per tier | figure: duplicates-per-document |
| `corpus_length_stats_llama2.json` | percentiles, buckets, histogram, length × duplication | figures: both length panels |
| `corpus_duplicate_stats.json` | duplicate rates at J ≥ 0.7/0.8/0.9, Jaccard histogram | paper text |
| `corpus_dedup_token_savings.json` | documents and tokens removed/retained per threshold | paper text |
| `corpus_dup_vs_length_llama2.json` | median length by duplicate count | paper text |
| `exact_duplicates_summary.json` | exact-duplicate totals and the largest groups | appendix table; read by `verify/bruteforce_counts.py` |
| `pipeline_stats.json` | parameters and per-step counts for the run | provenance |
| `corpus_length_stats_qwen3.json` | the same length analysis under Qwen3 | backs the tokenizer comparison |

## The tokenizer comparison

The same 553,240,576 documents measure **410.6B** tokens under Llama-2, **356.9B** under
GPT-2, and **350.0B** under Qwen3-Embedding — a 17% spread on identical text, from vocabulary
size alone (32k / 50k / 151k). Llama-2 is the default here: it is what the ClimbMix authors
state was used in their paper, and the only one that reproduces the corpus's nominal 400B.
The Qwen3 file is kept so that claim is checkable; the GPT-2 figure came from a 36,000
document sample rather than a full pass.
