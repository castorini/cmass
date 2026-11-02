# Retrieval hop groundedness

For each question, a retained hop receives **1** if at least one unique
search-returned document ID belongs to that hop's evidence set in the released
`v2026.10.25` qrels, and **0** otherwise. Average these binary values within the
question, then average the question scores within each dataset/model combination:

`HG(q) = sum_h 1[R_q intersects D_qh] / number_of_retained_hops(q)`

`HG(dataset, model) = 100 * mean_q HG(q)`

For example, three covered hops out of five give 60% for that question. This is
**not** total covered hops divided by total hops across the dataset: questions
with more hops must not receive greater weight.

## Scope and interpretation

- `R_q` is the union of unique IDs shown in all search results in the selected
  final attempt, the same view used for recall@all. Repeated retrieval counts
  once. Opened-only documents do not change this metric.
- `D_qh` comes from the hop/document associations behind those qrels, not a fresh
  judgment of the agent's answer or reasoning. All retained inclusion and
  exclusion hops count, including required, confirmatory and derivation
  annotations. Existing full/partial/audit evidence links are used as recorded.
- One document may cover multiple hops, including hops of both roles. A
  document's single inclusion/exclusion category for document recall does not
  replace its many-to-many hop associations.
- Hops with empty evidence sets count as uncovered, not as missing denominators.
  There are none in these inputs. A missing question or hop inventory
  is an input error; do not drop it silently.
- All selected questions remain, including a model-unanswered outcome. When
  reporting role-specific coverage, average only over questions with at least
  one hop of that role and report that eligible count.

This measures **retrieved evidence availability**, not causal use of evidence.
A document hit does not establish that its supporting passage was visible in
the search preview, that the model opened/read it, or that it performed a
derivation. It cannot by itself distinguish retrieval use from memory.

## Results

Percentages are question-macro averages. The complete-precision values,
role-specific scores, eligible counts and source hashes are in
[hop_groundedness_results.json](hop_groundedness_results.json).

| Dataset | Questions | Retained hops | Sol | Gemma | Qwen |
| --- | ---: | ---: | ---: | ---: | ---: |
| BCP | 80 | 582 | 75.50 | 31.42 | 31.38 |
| TRQA | 68 | 644 | 79.66 | 82.69 | 85.76 |
| HotpotQA | 204 | 803 | 90.20 | 78.40 | 83.92 |
| DSQA | 71 | 679 | 75.74 | 68.07 | 71.97 |

Sol is GPT-6.1 Sol high; Gemma is Gemma 4 31B IT; Qwen is Qwen 3.5 9B.
These results score all **1,269 hash-matched selected artifacts** against the
**2,708 retained-hop mappings** of `v2026.10.25`, and are not directly comparable
with scores on `v2026.10.20`. On the `v2026.10.20` hop files, the same evaluator
reproduces all twelve summaries published for that release.
The source question strings were matched against the pinned released revisions.
This is a fresh artifact/metric check, not a fresh semantic adjudication of the
hop evidence or a full raw-event-stream audit.

## Recompute

[hop_groundedness.py](hop_groundedness.py) takes two private JSONL inputs:

- Runs: `dataset`, `model`, released `query_id`, and `surfaced_docids`.
- Hops: `dataset`, released `query_id`, `hop_id`, `role` (`inclusion` or
  `exclusion`), and `doc_ids` (a list, possibly empty).

The run files in the [private bundle, available from the authors on request](SYSTEM_RUNS.md), have the required fields;
extra fields are ignored. Concatenate the desired dataset/model files into a
private input file. The full hop catalog must include every retained hop,
including ones with no supporting documents. IDs are strings, scoped by dataset.

```bash
python3 evaluation/hop_groundedness.py \
  --runs /absolute/private/selected-runs.jsonl \
  --hops /absolute/private/hop-documents.jsonl \
  > /absolute/private/hop-aggregates.json
```

The command emits aggregate statistics only. It rejects duplicate questions,
duplicate hop IDs, unknown roles and incomplete question coverage. Tests cover
macro averaging, shared evidence, repeated retrieval, empty evidence sets,
role-specific denominators and malformed inputs.

**Public question-level qrels do not contain the hop-to-document mapping.**
That mapping remains a private evaluation input; it cannot be inferred from
membership in the question's qrels. Hop clues, answers and evidence text are
not included in the private system-run bundle or this GitHub repository.
