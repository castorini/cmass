# Four-dataset evaluation results

All scores use the latest revised question sets: BCP 80, TRQA 68, HotpotQA 204,
and DSQA 71. Accuracy and recall are percentages. The complete-precision
aggregates and integer counts are in [results.json](results.json).
These results are separate from the earlier BCP57 and BCP830 experiments.

## Answer accuracy

| Dataset | Model | Closed-book | Piika |
| --- | --- | ---: | ---: |
| BCP | GPT-6.1 Sol (high) | 52.50 | 85.00 |
| BCP | Gemma 4 31B IT | 16.25 | 27.50 |
| BCP | Qwen 3.5 9B | 5.00 | 16.25 |
| TRQA | GPT-6.1 Sol (high) | 86.76 | 94.12 |
| TRQA | Gemma 4 31B IT | 50.00 | 66.18 |
| TRQA | Qwen 3.5 9B | 39.71 | 57.35 |
| HotpotQA | GPT-6.1 Sol (high) | 89.71 | 94.61 |
| HotpotQA | Gemma 4 31B IT | 36.76 | 65.69 |
| HotpotQA | Qwen 3.5 9B | 27.45 | 70.10 |
| DSQA | GPT-6.1 Sol (high) | 70.42 / 74.65 | 90.14 / 92.96 |
| DSQA | Gemma 4 31B IT | 23.94 / 36.62 | 38.03 / 53.52 |
| DSQA | Qwen 3.5 9B | 12.68 / 22.54 | 39.44 / 53.52 |

DSQA entries are **strict / relaxed** from one judgment of the same generated
answer. Order is ignored in both. Terminal unanswered outcomes count as
incorrect: 25 Qwen BCP closed-book, one Qwen HotpotQA closed-book, and one Gemma
DSQA Piika. The denominators remain 80, 204 and 71, respectively. The 55
produced Qwen BCP closed-book answers were all judged; the score is 4/80, not
4/55. Context/output caps varied across accepted sources and continuations;
see [SETTINGS.md](SETTINGS.md).

## Retrieval

| Dataset | Model | Recall@all | Average tool calls |
| --- | --- | ---: | ---: |
| BCP | GPT-6.1 Sol (high) | 10.83 | 21.48 |
| BCP | Gemma 4 31B IT | 3.40 | 23.49 |
| BCP | Qwen 3.5 9B | 3.17 | 49.91 |
| TRQA | GPT-6.1 Sol (high) | 9.94 | 8.60 |
| TRQA | Gemma 4 31B IT | 14.05 | 12.41 |
| TRQA | Qwen 3.5 9B | 15.73 | 19.69 |
| HotpotQA | GPT-6.1 Sol (high) | 16.58 | 6.68 |
| HotpotQA | Gemma 4 31B IT | 16.30 | 10.17 |
| HotpotQA | Qwen 3.5 9B | 17.22 | 16.96 |
| DSQA | GPT-6.1 Sol (high) | 14.12 | 17.87 |
| DSQA | Gemma 4 31B IT | 15.46 | 21.49 |
| DSQA | Qwen 3.5 9B | 16.66 | 37.13 |

Recall averages per-question recall of all unique search-returned document IDs
against final phase-2 qrels. Distinct duplicate-backed IDs remain separate.
Tool calls average search plus document-read invocations in selected attempts,
including the selected Gemma DSQA unanswered attempt. Intermediate attempts
are excluded. These are archived source-hash-checked artifact aggregates; this
publication update is not a new independent audit of every raw event stream.

## Qrels-only Sol

Sol receives phase-2 evidence and no tools or web access. This subset contains
questions incorrect in the revised Sol Piika run, not all questions.

| Dataset | Failed subset | Completed | Correct | Accuracy |
| --- | ---: | ---: | ---: | ---: |
| BCP | 12 | 12 | 12 | 100.00 |
| TRQA | 4 | 4 | 4 | 100.00 |
| HotpotQA | 11 | 11 | 10 | 90.91 |
| DSQA | 7 | 7 | 6 | 85.71 |

All 34 selected cases completed. DSQA has 6/7 under both standards. An additional
revised DSQA diagnostic passed but is excluded because the revised Piika answer
was correct. Oversized evidence was processed in full-coverage batches before
synthesis. These scores measure the selected failure subset with supplied
evidence and must not be compared as all-question accuracy.

## Scope

The six primary runs per dataset share question sets, but deployment and budget
histories differ across backbones. Closed-book scores are a memorization proxy;
they do not prove that an answer was literally memorized. Recall and hop coverage
show which evidence was retrieved, not which evidence caused an answer.

The [main README](../README.md#results) additionally reports archived hop coverage.
Recomputing that metric requires withheld per-hop mappings; public question-level
qrels alone are insufficient. See the [metric definitions](../REPRODUCING.md#5-compute-metrics)
and [source provenance](PROVENANCE.md).
