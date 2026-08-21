# Reproducing the Agent Evaluation

<!-- markdownlint-disable MD013 MD033 -->

This guide describes how to evaluate any search agent on
BrowseComp-Plus<sub>CM</sub> without using Piika. It defines the search, run, and
evaluation artifacts needed to reproduce the paper's metrics while remaining
compatible with Piika's observable output structure.

A complete submission has this layout:

```text
artifacts/<run-id>/
  run_config.json
  runs/
    <query_id>.json
  evals/
    per-query/
      <query_id>_eval.json
    evaluation_summary.json
    detailed_judge_results.csv
```

`run_config.json` is harness-specific, but should capture every setting listed
in the validation checklist. Search exchanges live chronologically in each
run's `result` array; a harness may additionally stream the same entries to raw
JSONL while a query is running.

The compatibility reference is `castorini/piika` package version 0.3.0 at
[commit `6475fef`](https://github.com/castorini/piika/tree/6475fefe6749448c4c30cadd071f6c75c3538fa4).
The evaluation condition and prompts come from
[the CMASS paper](https://arxiv.org/abs/2608.20317), especially Section 6 and
Appendix B.

## Evaluation condition

Hold the following choices fixed when comparing against the paper:

| Component | Setting |
| --- | --- |
| Queries | All 57 released BrowseComp-Plus<sub>CM</sub> questions |
| Qrels | Released question-level qrels, without filtering or sampling |
| Corpus | ClimbMix |
| Index | `climbmix-400b` |
| Retriever | BM25 through the Pyserini REST API |
| Agent tools | `search` and `read_document` only |
| Search preview | Whitespace-normalized, at most 500 characters per hit |
| Supplied evidence | None |
| Answer judging | Gold-answer semantic-equivalence judge |
| Headline recall | Macro recall over documents shown to the agent in search results |
| Tool calls | Mean of `search + read_document` calls over all 57 questions |

The agent must not receive the gold answer, qrels, hop decomposition, or
supporting documents. It may use only text returned by the two retrieval tools.
Store gold answers and qrels in a separate evaluation process.

For an exact comparison between the original BrowseComp-Plus corpus and
ClimbMix, use the same 57 query IDs, agent prompt, model configuration, timeout,
and judge. Only the corpus, index, and corresponding qrels should change.

### Run manifest

Write the experiment settings before starting the run. This combines the role
of Piika's `benchmark_manifest_snapshot.json` and `run_setup.json` without tying
the experiment to a particular harness.

```json
{
  "schema_version": "cmass-run-config-v1",
  "run_id": "cmass-bcp-cm-example",
  "benchmark_id": "cmass-bcp-cm",
  "query_set_id": "test",
  "query_count": 57,
  "dataset": {
    "repo": "castorini/cmass",
    "revision": "13a0ff7ce702f797e2db5dfe7be38933e08088e2",
    "queries_path": "bcp/queries.jsonl",
    "qrels_path": "bcp/qrels.jsonl"
  },
  "agent": {
    "harness": "name and version or commit",
    "model": "provider/model-id",
    "reasoning_effort": "exact setting",
    "temperature": null,
    "prompt_sha256": "sha256 of exact rendered prompt template"
  },
  "retrieval": {
    "backend": "pyserini-rest",
    "base_url": "http://api.castorini.uwaterloo.ca",
    "index": "climbmix-400b",
    "tool_interface": "pyserini-rest-2tool",
    "search_preview_chars": 500,
    "default_hits": 5,
    "document_read_mode": "paginated-lines",
    "document_default_lines": 200
  },
  "execution": {
    "timeout_seconds": 300,
    "concurrency": 1,
    "retry_incomplete_only": true,
    "completed_attempt_selection": "first-completed"
  },
  "judge": {
    "mode": "gold-answer",
    "model": "provider/judge-model-id",
    "reasoning_effort": "low",
    "prompt_sha256": "sha256 of exact judge prompt template"
  },
  "started_at": "YYYY-MM-DDTHH:MM:SSZ"
}
```

Use `null` only when a setting genuinely does not apply. Replace descriptive
placeholders with exact values before publishing the artifact.

## Released inputs

The examples below pin the Hugging Face release to revision
`13a0ff7ce702f797e2db5dfe7be38933e08088e2`. Pinning the revision prevents a
later dataset update from silently changing an experiment.

```python
from datasets import load_dataset

from scripts.deobfuscate import decode_row

REVISION = "13a0ff7ce702f797e2db5dfe7be38933e08088e2"
ROOT = f"hf://datasets/castorini/cmass@{REVISION}/bcp"

queries = [
    decode_row(row)
    for row in load_dataset(
        "json", data_files=f"{ROOT}/queries.jsonl", split="train"
    )
]
qrels = [
    decode_row(row)
    for row in load_dataset(
        "json", data_files=f"{ROOT}/qrels.jsonl", split="train"
    )
]

assert len(queries) == 57
assert len({row["id"] for row in queries}) == 57
assert len(qrels) == 6695
assert {row["relevance"] for row in qrels} == {1}
assert {row["query_id"] for row in qrels} == {row["id"] for row in queries}
```

Run `hf auth login` first if the release requires authentication. After
deobfuscation, query rows have this shape:

```json
{
  "id": "25",
  "question": "...",
  "answer": "...",
  "canary": "..."
}
```

Qrel rows have this shape:

```json
{
  "query_id": "<query_id>",
  "iteration": "0",
  "doc_id": "shard_00071_31411",
  "relevance": 1,
  "canary": "..."
}
```

Treat query IDs and document IDs as strings. Match document IDs exactly as
released; do not zero-pad, shorten, or split them. The qrels contain 6,695
unique query-document pairs. Every question has between 7 and 476 relevant
documents.

The released qrels are question-level unions over all supporting hops. They are
not per-hop qrels, and the private hop assignments must not be reconstructed or
used as agent input.

## Search stage

### REST interface

The current service location and authentication guidance are maintained in the
[Pyserini REST API skill](https://github.com/TREC-RAG/trec-rag-skills/tree/main/skills/pyserini-rest-api).
At the time of writing, the base URL is
`http://api.castorini.uwaterloo.ca`. Record the URL and evaluation date because
the service location may change.

Use these two endpoints:

```text
GET /v1/climbmix-400b/search?query=<query>&hits=<k>&max_doc_length=500
GET /v1/climbmix-400b/doc/<docid>
```

Authentication uses `Authorization: Bearer <token>`. Keep the token in a secure
environment variable or credential file. Never put it in prompts, run
artifacts, command histories, or a repository.

A search response has this wire format:

```json
{
  "api": "v1",
  "index": "climbmix-400b",
  "query": {"text": "example query"},
  "candidates": [
    {
      "docid": "shard_00459_61697",
      "score": 12.483799934387207,
      "rank": 1,
      "doc": "Document preview text..."
    }
  ]
}
```

A document response has this wire format:

```json
{
  "api": "v1",
  "index": "climbmix-400b",
  "docid": "shard_00459_61697",
  "doc": "Full document text..."
}
```

With the default `parse` behavior, `doc` may instead be an object. Normalize an
object by taking the first string field among `text`, `contents`, `content`, and
`body`; retain a string `title` separately when present. For search previews,
collapse whitespace and truncate the normalized text to 500 characters,
appending `...` when truncated. The document endpoint returns the full
normalized document; the agent tool may expose it in deterministic line-based
pages as described below.

Use ordinary text queries. Do not depend on Lucene field syntax, Boolean
operators, or quotation marks having special semantics.

For shared API use, allow at most one request in flight per worker, keep the
worker pool modest, and use bounded exponential backoff for `429`, transient
`5xx`, and timeout responses. An HTTP retry inside one tool invocation is still
one agent tool call.

### Agent tool schemas

Expose the following two tools to the agent. `reason` is logged for audit but is
not sent to the retriever.

```json
{
  "name": "search",
  "arguments": {
    "reason": "Short explanation of the current evidence gap",
    "query": "concise lexical query",
    "hits": 5
  }
}
```

```json
{
  "name": "read_document",
  "arguments": {
    "reason": "Why this candidate needs inspection",
    "docid": "shard_00459_61697",
    "offset": 1,
    "limit": 200
  }
}
```

`offset` and `limit` are optional. For the paginated form used in the reference
traces, they default to line 1 and 200 lines. A full-document implementation may
omit both, but must disclose that interface difference.

The model-visible search output used by the reference two-tool interface is:

```text
Returned 2 hits from the Pyserini REST ranking.
Plain query: "example query"
Requested hits: 2

1. docid=shard_00459_61697 score=12.4838
   Title: Optional title
   Excerpt: Document preview text...

2. docid=shard_00071_31411 score=11.9201
   Excerpt: Another document preview...

Use read_document(docid) to inspect a specific document before answering.
```

The model-visible paginated document output is:

```text
[docid=shard_00459_61697 lines 1-80 of 315]

Document text for lines 1 through 80...

[Document truncated. Continue with read_document({"docid":"shard_00459_61697","offset":81,"limit":80}).]
```

If the full-document form is used, render `[docid=<docid> full document]`, an
optional `[title=<title>]` line, and the complete text.

Another rendering is acceptable, but disclose it: tool rendering can affect
agent behavior even when the underlying ranking is identical.

### Search trace records

Record every tool invocation chronologically. The `output` field must contain
the exact text shown to the model. `details` is optional but strongly
recommended because it makes document accounting independent of parsing the
rendered text.

```json
{
  "type": "tool_call",
  "tool_name": "search",
  "arguments": {
    "reason": "Find the document connecting two clues",
    "query": "example query",
    "hits": 2
  },
  "output": "Returned 2 hits from the Pyserini REST ranking.\n...",
  "details": {
    "toolInterface": "pyserini-rest-2tool",
    "rawQuery": "example query",
    "queryMode": "plain",
    "k": 2,
    "returnedRankStart": 1,
    "returnedRankEnd": 2,
    "retrievedDocids": [
      "shard_00459_61697",
      "shard_00071_31411"
    ],
    "previewedDocids": [
      "shard_00459_61697",
      "shard_00071_31411"
    ],
    "timingMs": {"searchRpcMs": 123.4}
  }
}
```

```json
{
  "type": "tool_call",
  "tool_name": "read_document",
  "arguments": {
    "reason": "Verify the candidate's stated fact",
    "docid": "shard_00459_61697",
    "offset": 1,
    "limit": 200
  },
  "output": "[docid=shard_00459_61697 lines 1-42 of 42]\n\nFull document text...",
  "details": {
    "docid": "shard_00459_61697",
    "offset": 1,
    "limit": 200,
    "totalLines": 42,
    "returnedLineStart": 1,
    "returnedLineEnd": 42,
    "truncated": false,
    "timingMs": {"readDocumentRpcMs": 74.2}
  }
}
```

Record failed calls as tool-call entries with their error and HTTP status, but
add no document IDs from a failed response. Count each agent-dispatched call,
including a failed call, once. Do not count internal HTTP retries as additional
agent calls.

## Agent run stage

### Prompt and answer contract

The paper's retrieval-enabled prompt requires the agent to:

- perform at least one search before answering;
- use only facts returned by `search` and `read_document`;
- prefer concise lexical searches;
- inspect promising documents before answering;
- cite supporting document IDs inline when possible; and
- report insufficient evidence rather than fill gaps from memory.

Use the exact final response structure below. Do not add text before or after
these fields.

```text
Answer:
Explanation: <concise evidence-based explanation with [full_docid] citations>
Exact Answer: <succinct answer>
Confidence: <integer or decimal from 0% to 100%>
```

The full reference prompt is in Appendix B of the paper. Record the exact prompt
text or its content hash with every run. Hidden reasoning or chain-of-thought is
not part of the artifact contract; retain only observable tool interactions and
the final assistant text.

### Per-query run artifact

Write one JSON file per query, named `<query_id>.json`. The following is the
canonical PIIKA-compatible shape. Custom metadata fields are allowed, but do not
rename the fields shown here. Values are illustrative rather than an official
trace for a particular released query.

```json
{
  "metadata": {
    "benchmark_id": "cmass-bcp-cm",
    "query_set_id": "test",
    "model": "provider/model-id",
    "output_dir": "runs/example",
    "query": "The benchmark question...",
    "prompt_variant": "plain_minimal",
    "output_mode": "answer",
    "output_modes": ["answer"],
    "tool_interface": "pyserini-rest-2tool",
    "search_backend_kind": "pyserini-rest",
    "index": "climbmix-400b",
    "dataset_revision": "13a0ff7ce702f797e2db5dfe7be38933e08088e2",
    "attempt": 1,
    "supplied_docids": []
  },
  "query_id": "25",
  "tool_call_counts": {
    "search": 1,
    "read_document": 1
  },
  "status": "completed",
  "completion_source": "assistant_text",
  "surfaced_docids": [
    "shard_00459_61697",
    "shard_00071_31411"
  ],
  "previewed_docids": [
    "shard_00459_61697",
    "shard_00071_31411"
  ],
  "agent_docids": ["shard_00459_61697"],
  "opened_docids": ["shard_00459_61697"],
  "cited_docids": ["shard_00459_61697"],
  "stats": {
    "elapsed_seconds": 42.317,
    "assistant_turns": 3,
    "tool_calls_total": 2,
    "seconds_per_assistant_turn": 14.106,
    "seconds_per_tool_call": 21.159,
    "search_calls": 1,
    "read_search_results_calls": 0,
    "read_document_calls": 1,
    "search_rewrites_after_browse": 0,
    "search_rewrites_without_browse": 0,
    "pi_search_failures": 0,
    "timed_out": false
  },
  "result": [
    {
      "type": "tool_call",
      "tool_name": "search",
      "arguments": {
        "reason": "Find the first clue",
        "query": "example query",
        "hits": 2
      },
      "output": "Returned 2 hits from the Pyserini REST ranking.\n..."
    },
    {
      "type": "tool_call",
      "tool_name": "read_document",
      "arguments": {
        "reason": "Verify the candidate",
        "docid": "shard_00459_61697",
        "offset": 1,
        "limit": 200
      },
      "output": "[docid=shard_00459_61697 lines 1-42 of 42]\n\n..."
    },
    {
      "type": "output_text",
      "tool_name": null,
      "arguments": null,
      "output": "Answer:\nExplanation: ... [shard_00459_61697]\nExact Answer: ...\nConfidence: 88%"
    }
  ]
}
```

Field semantics:

| Field | Meaning |
| --- | --- |
| `status` | One of `completed`, `timeout`, or `failed` |
| `completion_source` | `assistant_text` for a completed answer, otherwise `null` |
| `surfaced_docids` | First-encounter deduplicated IDs returned through search |
| `previewed_docids` | First-encounter deduplicated IDs whose previews were actually shown to the model |
| `opened_docids` | First-encounter deduplicated IDs passed to `read_document` |
| `cited_docids` | First-encounter deduplicated full IDs cited in the final response |
| `agent_docids` | First-encounter union of `opened_docids` and `cited_docids` |
| `result` | Chronological observable tool calls followed by final assistant text |
| `tool_call_counts` | Counts derived from `result`, not estimated from logs |
| `stats.tool_calls_total` | Sum of all retrieval-tool counts |

For this two-tool condition, every search hit returned to the harness is shown
to the model, so `surfaced_docids` and `previewed_docids` should be identical.
If a custom backend retrieves hidden candidates, exclude them from
`previewed_docids`; do not count them in the paper's headline recall.

For a timeout or failure, preserve the partial trace, set
`completion_source` to `null`, and set `stats.timed_out` appropriately. Do not
label an artifact `completed` unless its final `result` entry is an
`output_text` item containing the requested answer fields.

Infrastructure failures may be retried under the identical configuration. Keep
every attempt and identify the selected final attempt. Do not rerun completed
but wrong answers, and do not choose among multiple completed attempts by
outcome. In the paper, three initially incomplete GPT-5.6 Sol queries were rerun
under the same setup; all final 57 artifacts completed.

## Evaluation stage

### Per-query metrics

For query `q`, define:

- `G(q)`: the set of released qrel document IDs with `relevance > 0`;
- `P(q)`: the set of `previewed_docids` shown during the whole run;
- `S(q)`: the set of `surfaced_docids`;
- `O(q)`: the set of `opened_docids`; and
- `C(q)`: the set of `cited_docids`.

Compute:

```text
previewed_recall(q) = |P(q) intersect G(q)| / |G(q)|
surfaced_recall(q)  = |S(q) intersect G(q)| / |G(q)|
opened_recall(q)    = |O(q) intersect G(q)| / |G(q)|
cited_recall(q)     = |C(q) intersect G(q)| / |G(q)|
```

The paper's recall is:

```text
Recall (%) = 100 * mean_q(previewed_recall(q))
```

This is macro recall over the full accumulated search interaction, with no rank
cutoff. It is not recall from only the last search and not micro recall over all
qrels. Piika's legacy `Recall (%)` key uses `surfaced_docids`; it agrees with the
paper only when `surfaced_docids == previewed_docids`, as in the two-tool setup.

The remaining paper metrics are:

```text
Accuracy (%) = 100 * judged-correct completed answers / 57

Tool Calls = mean_q(
    tool_call_counts.search + tool_call_counts.read_document
)
```

Compute the mean before rounding and report two decimal places. A final
incomplete artifact contributes zero to all-query accuracy. Also report
completed-only accuracy separately if any final artifact is incomplete.

### Gold-answer judge

Use one fixed judge configuration for all runs being compared. The judge sees
only the question, the agent response, and the benchmark answer. It determines
semantic equivalence and must not solve the question or use external knowledge.
Its raw output must be exactly one JSON object:

```json
{
  "extracted_final_answer": "...",
  "correct_answer": "...",
  "reasoning": "Why the extracted answer does or does not match",
  "correct": true,
  "confidence": 100
}
```

`extracted_final_answer` may be `null`. `correct` is Boolean and `confidence`
is between 0 and 100. Store judge parse failures explicitly; do not silently
repair or discard them.

### Per-query evaluation artifact

Write one file at `per-query/<query_id>_eval.json` with this shape. Its values
continue the illustrative run example above:

```json
{
  "json_path": "runs/example/<query_id>.json",
  "query_id": "<query_id>",
  "question": "The benchmark question...",
  "response": "Answer:\nExplanation: ... [shard_00459_61697]\nExact Answer: ...\nConfidence: 88%",
  "response_confidence": 88,
  "calibration_confidence": 88,
  "correct_answer": "The benchmark answer",
  "judge_mode": "gold-answer",
  "is_completed": true,
  "judge_prompt": "The exact judge prompt...",
  "judge_response": "{\"extracted_final_answer\":\"...\",\"correct_answer\":\"...\",\"reasoning\":\"...\",\"correct\":true,\"confidence\":100}",
  "judge_result": {
    "extracted_final_answer": "...",
    "correct_answer": "The benchmark answer",
    "reasoning": "The answers are semantically equivalent.",
    "correct": true,
    "confidence": 100,
    "parse_error": false
  },
  "tool_call_counts": {
    "search": 1,
    "read_document": 1
  },
  "citations": {
    "cited_docids": ["shard_00459_61697"],
    "metrics": {
      "num_citations": 1,
      "num_relevant": 7,
      "precision": 1.0,
      "recall": 0.14285714285714285
    }
  },
  "retrieval": {
    "surfaced_docids": [
      "shard_00459_61697",
      "shard_00071_31411"
    ],
    "previewed_docids": [
      "shard_00459_61697",
      "shard_00071_31411"
    ],
    "agent_docids": ["shard_00459_61697"],
    "opened_docids": ["shard_00459_61697"],
    "cited_docids": ["shard_00459_61697"],
    "surfaced_recall": 0.14285714285714285,
    "previewed_recall": 0.14285714285714285,
    "agent_recall": 0.14285714285714285,
    "opened_recall": 0.14285714285714285,
    "cited_recall": 0.14285714285714285
  },
  "model_info": {
    "judge_model": "provider/judge-model-id",
    "judge_thinking": "low",
    "pi_bin": "custom-harness",
    "run_model": "provider/model-id"
  }
}
```

For an incomplete run, set `is_completed` to `false`, set judge prompt and
response to `null`, and use:

```json
{
  "extracted_final_answer": null,
  "correct_answer": "The benchmark answer",
  "reasoning": "",
  "correct": null,
  "confidence": null,
  "parse_error": true,
  "error": "Response incomplete or unavailable for judging."
}
```

### Aggregate evaluation artifact

Write `evaluation_summary.json`. The following keys align with Piika's summary
and are sufficient to reconstruct the paper table:

| Key | Type | Definition |
| --- | --- | --- |
| `LLM` | string | Exact run model identifier |
| `Judge Mode` | string | `gold-answer` |
| `Accuracy Label` | string | `Accuracy (gold-answer judge)` |
| `Accuracy Semantics` | string | Human-readable judge definition |
| `Accuracy (%)` | number | All-57 accuracy |
| `Completed-Only Accuracy (%)` | number or null | Accuracy over completed artifacts |
| `Recall (%)` | number | Legacy surfaced macro recall; use only when surfaced equals previewed |
| `Recall Macro (%)` | number | Same legacy surfaced macro recall |
| `Recall Micro (%)` | number | Optional surfaced micro recall |
| `Agent Set Recall Macro (%)` | number | Compatibility alias for surfaced macro recall |
| `Agent Set Recall Micro (%)` | number | Compatibility alias for surfaced micro recall |
| `System Surfaced Recall Macro (%)` | number | Macro recall from `surfaced_docids` |
| `System Surfaced Recall Micro (%)` | number | Micro recall from `surfaced_docids` |
| `Agent Previewed Recall Macro (%)` | number | Paper headline recall |
| `Agent Previewed Recall Micro (%)` | number | Optional previewed micro recall |
| `Agent Recall Macro (%)` | number | Macro recall from `agent_docids` |
| `Agent Recall Micro (%)` | number | Micro recall from `agent_docids` |
| `Agent Opened Recall Macro (%)` | number | Macro recall from `opened_docids` |
| `Agent Opened Recall Micro (%)` | number | Micro recall from `opened_docids` |
| `Answer Cited Recall Macro (%)` | number | Macro recall from `cited_docids` |
| `Answer Cited Recall Micro (%)` | number | Micro recall from `cited_docids` |
| `Coverage Tier Semantics` | string | Definitions of the five document views |
| `Recall Definition` | string | Exact denominator and aggregation rule |
| `avg_tool_stats` | object | Mean count for each retrieval tool |
| `Calibration Error (%)` | number or null | Optional response-confidence calibration error |
| `Calibration Error Computed` | Boolean | Whether enough examples exist for calibration |
| `Calibration Metric` | string | Name of the calibration metric |
| `Calibration Semantics` | string | Human-readable calibration definition |
| `Calibration Confidence Source` | string | Usually `response` |
| `Calibration Confidence Count` | integer | Responses included in calibration |
| `Calibration Defaulted Count` | integer | Missing confidences assigned a compatibility default |
| `Completed Queries` | integer | Number with `status == completed` |
| `Timeout/Incomplete Queries` | integer | Number not completed |
| `Completed Correct` | integer | Completed and judged correct |
| `Completed Wrong` | integer | Completed and judged incorrect |
| `Retriever` | string | Retriever, index, and important settings |
| `Link` | string | Public run-artifact location |
| `Evaluation Date` | string | ISO date |
| `per_query_metrics` | array | One compact metric object per query |
| `judge` | object | Judge model, settings, usage, and parse counts |
| `citation_summary` | object | Optional aggregate citation diagnostics |

Per-query files may also include `judge_usage` with input, output, cache, token,
and cost totals. This is diagnostic metadata and does not affect the three paper
metrics.

Each `per_query_metrics` item should be:

```json
{
  "query_id": "<query_id>",
  "correct": true,
  "system_surfaced_recall": 14.29,
  "agent_previewed_recall": 14.29,
  "agent_recall": 14.29,
  "agent_opened_recall": 14.29,
  "answer_cited_recall": 14.29,
  "agent_set_recall": 14.29,
  "recall": 14.29
}
```

The paper's `Tool Calls` column is not a single Piika summary key. Derive it as:

```text
avg_tool_stats.search + avg_tool_stats.read_document
```

Also write `detailed_judge_results.csv` with this exact header for convenient
cross-harness analysis:

```csv
query_id,predicted_answer,correct_answer,judge_correct,response_confidence,calibration_confidence,judge_confidence,is_completed,parse_error,json_path,num_citations,precision_positives,recall_positives
```

## Reference calculation

This compact Python shows the three headline calculations. `runs` is the list
of final per-query run artifacts, `evals` maps query ID to per-query evaluation
artifact, and `gold` maps query ID to its qrel document-ID set.

```python
def recall(retrieved, relevant):
    return len(set(retrieved) & relevant) / len(relevant)


query_count = 57

accuracy = 100 * sum(
    evals[run["query_id"]]["judge_result"]["correct"] is True
    for run in runs
) / query_count

macro_recall = 100 * sum(
    recall(run["previewed_docids"], gold[run["query_id"]])
    for run in runs
) / query_count

mean_tool_calls = sum(
    run["tool_call_counts"].get("search", 0)
    + run["tool_call_counts"].get("read_document", 0)
    for run in runs
) / query_count
```

## Validation checklist

Before reporting a result, verify all of the following:

- There are exactly 57 unique query IDs, identical to the released query set.
- There are 6,695 unique qrel pairs and every query has at least one qrel.
- Every final per-query artifact has an explicit status.
- Every completed artifact ends with one `output_text` result containing all
  four required answer lines.
- `tool_call_counts` agrees with the chronological `result` entries.
- `stats.tool_calls_total` equals the sum of retrieval-tool counts.
- All document lists are deduplicated in first-encounter order.
- Every document ID is preserved as a full ClimbMix ID matching
  `^shard_[0-9]{5}_[0-9]+$`.
- `surfaced_docids` equals `previewed_docids` for the direct two-tool condition.
- The agent received no supplied documents, answers, qrels, hops, open-web
  access, filesystem search, or other retrieval tools.
- Recall uses all relevant documents for that question and is macro-averaged
  across 57 questions before rounding.
- Tool calls include both `search` and `read_document`.
- Judge parse errors, timeouts, failed calls, and retries are disclosed.
- The exact model identifier, model settings, prompt, timeout, concurrency,
  API location, index name, dataset revision, and evaluation date are recorded.

As a final numerical check, the paper's GPT-5.6 Sol (max) run on
BrowseComp-Plus<sub>CM</sub> has 46 correct answers out of 57:

| Accuracy | Previewed macro recall | Mean retrieval calls |
| ---: | ---: | ---: |
| 80.70 | 21.37 | 98.26 |

Matching accuracy alone is not a retrieval reproduction. Report all three
numbers together, along with the run and per-query evaluation artifacts.
