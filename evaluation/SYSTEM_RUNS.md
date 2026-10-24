# Retrieval runs for qrel pooling

The selected Piika retrieval runs for **Sol, Gemma and Qwen on all four
datasets** are available to users with watgpu filesystem access:

```text
/u201/l39gu/experiments/cmass-system-runs-public-20261024/
```

The export directory has traversable/readable directories and read-only access
for other users to the files. It is **not an anonymous HTTP download**: a
watgpu account or mounted filesystem access is still needed. No original run
permissions were changed. The files are not committed to GitHub and have not
been uploaded to Hugging Face.

```bash
scp -r watgpu:/u201/l39gu/experiments/cmass-system-runs-public-20261024 \
  /absolute/private/cmass-system-runs
cd /absolute/private/cmass-system-runs
shasum -a 256 -c SHA256SUMS
```

Use your own SSH host configuration for watgpu. The top-level `README.md` and
`manifest.json` describe the fields and pin every payload file's hash.

## What is shared

- `runs/<dataset>/<model>.jsonl`: twelve files; dataset is `bcp`, `trqa`,
  `hotpotqa`, or `dsqa`, and model is `gpt`, `gemma`, or `qwen`. There is one
  row per released question, **1,269 rows total**, including zero-retrieval
  questions and the selected model-unanswered case.
- Each row contains the released `query_id`, sorted unique `surfaced_docids`
  and `opened_docids`, tool-call counts, outcome, model label and selected
  source-artifact SHA-256. The source is the frozen final revised attempt,
  never a union of successful and failed retries.
- `pools/<dataset>.jsonl`: one row per unique `(dataset, query_id, doc_id)`
  from the union of all three models' search-returned **or opened** documents.
  `systems` records which models surfaced/opened it. `assessment_status` is
  `existing_positive` or `unjudged`, relative to frozen phase-2 qrels.
- No question/answer text, model reasoning, search-query text, previews,
  document bodies, credentials, raw logs or machine-local source paths are
  copied. The bundle is a retrieval-ID export, **not full agent trajectories**.

The ID arrays are unranked sets sorted for reproducibility. They are not BM25
rankings, retrieval scores, or TREC run rankings. Repeated calls and within-call
ranks cannot be reconstructed from them. Pooling does not require inventing
those ranks. Use the search-returned view alone for the reported recall and
hop-groundedness metrics; the expansion pool additionally includes opened-only
documents and marks their provenance.

## Pool size

Counts are unique **question-document pairs**, not globally unique documents.
The exact run summaries are in [pooling_results.json](pooling_results.json).

| Dataset | Questions | Candidate pairs | Existing positives | Unjudged candidates |
| --- | ---: | ---: | ---: | ---: |
| BCP | 80 | 21,397 | 1,178 | 20,219 |
| TRQA | 68 | 4,909 | 1,457 | 3,452 |
| HotpotQA | 204 | 13,314 | 2,281 | 11,033 |
| DSQA | 71 | 10,265 | 1,708 | 8,557 |
| **Total** | **423** | **49,885** | **6,624** | **43,261** |

## Use for qrel expansion

1. Join using dataset and the **released question ID**, not a run-local sequence
   number. Obtain the revised questions separately from the pinned CMASS release.
2. Retrieve candidate document text from the same ClimbMix corpus. Preserve the
   contributing systems, original source hashes and frozen input revision.
3. Assess previously unjudged candidates for support of specific retained hops,
   including inclusion/exclusion roles. Neither retrieval nor being outside
   current qrels is a relevance judgment. Hide system identities from assessors
   when practical; retain them in provenance.
4. Add only verified evidence to a **new version** of the qrels/hop mappings.
   Preserve the phase-2 release and its reported scores. Recompute every system's
   metrics consistently against any expanded judgment set.

This is a candidate-pooling release, not a claim that 43,261 new relevant pairs
have been found. It is also a pool of these three selected systems, not an
exhaustive corpus judgment. No new relevance judgments were run for this update.

## Regenerate a private export

[export_system_runs.py](export_system_runs.py) accepts a private frozen selection
manifest with a `sources` list. Each entry supplies `dataset`, `model`, released
`query_id`, artifact `path`, `sha256`, and `outcome` (`completed` or
`model_unanswered`). It checks every source hash, model/tool interface, retrieval
failure count, call counts, full question coverage and duplicate selection.
Use a separately prepared, decoded JSONL qrels file with `dataset`, `query_id`,
`doc_id` and positive `relevance`.

```bash
python3 evaluation/export_system_runs.py \
  --manifest /absolute/private/frozen-selection.json \
  --qrels /absolute/private/frozen-phase2-qrels.jsonl \
  --output /absolute/private/new-retrieval-export
```

The exporter refuses overwrites and destinations inside this public checkout.
Its allowlist copies only retrieval metadata; it does not redo selection or a
full raw-trace audit. Sharing/permission changes are a separate, deliberate step.
