# Reproducing the four-dataset Piika evaluation

The current evaluation covers **BCP (80), Total Recall QA (68), HotpotQA (204),
and DeepSearchQA (71)**: 423 revised questions, with three models and two
conditions per dataset. Each model answers closed-book and with Piika retrieval
over ClimbMix. The [results](evaluation/RESULTS.md) include all 24 combinations,
DSQA strict/relaxed accuracy, recall@all, average tool calls, and the separate
Sol qrels-only diagnostic. The [settings](evaluation/SETTINGS.md) identify the
models, serving configuration, context/output limits and stopping rules.

The original GPT-5.6 results on 57 BCP questions are a different experiment;
their [historical guide](evaluation/legacy_bcp57.md) is retained separately.

## Exact prompts

These runtime templates were checked against the archived October 2026 sources.
Placeholders replace benchmark content.

| Stage | BCP, TRQA, HotpotQA | DSQA |
| --- | --- | --- |
| Closed-book system prompt | [closed_book.txt](evaluation/prompts/closed_book.txt) | [closed_book_dsqa.txt](evaluation/prompts/closed_book_dsqa.txt) |
| Piika user prompt | [piika.txt](evaluation/prompts/piika.txt) | [piika_dsqa.txt](evaluation/prompts/piika_dsqa.txt) |
| Answer judge | [gold_answer_judge.txt](evaluation/prompts/gold_answer_judge.txt) | [dsqa_judge.txt](evaluation/prompts/dsqa_judge.txt) |

**The DSQA generation/evaluation instruction** is the
[complete-set instruction](evaluation/prompts/dsqa_complete_set.txt), included
in both generation conditions. Piika keeps its `Answer`, `Explanation`,
`Exact Answer`, and `Confidence` format, with the complete JSON answer array in
`Exact Answer`. Closed-book generation outputs only the array, or exactly
`I don't know.` There are no separate strict and relaxed generation runs.

**DSQA judging uses one call and two verdicts.** Strict requires the complete
gold set and no incorrect extra member. Relaxed requires a nonempty subset of
the gold set and no incorrect extra member. Any wrong extra answer, an empty
answer, or abstention fails both. Order is ignored for every question, even if
the question asks for an order. The judge matches canonical members and accepted
aliases; [protocol.py](evaluation/protocol.py) checks that its verdicts agree
with those mappings. This is our DSQA protocol, not the upstream DeepSearchQA
precision/recall/F1 evaluation.

BCP, TRQA and HotpotQA use Piika's gold-answer semantic-equivalence template,
**not an original BrowseComp-Plus judge prompt**. Its inputs are `question`,
`response` and `correctAnswer`; the archived template has no explicit
single/set/aggregate answer-type parameter. Its JSON-array wording means
acceptable alternatives, not DSQA's complete-set rule. Do not use it for DSQA.
The wrapper system instruction is:
`You are an evaluation judge. Follow the supplied gold-answer grading prompt.`
All four datasets use **GPT-6.1 Sol (`gpt-6.1-sol`), high reasoning**, for judging
both conditions. Judges have no tools or external retrieval.

The archived four-dataset Piika prompt does **not** contain the extra
`STRICT EVIDENCE POLICY` block in the historical BCP57 guide. It instructs the
agent to use the provided tools and cite documents. Those instructions alone
cannot establish whether a model used retrieved evidence or parametric knowledge.

## 1. Freeze the released inputs

Use the reviewed HF release at
[`09b13bd392f499ab9af21618bcde3284a69cbcbb`](https://huggingface.co/datasets/castorini/cmass/tree/09b13bd392f499ab9af21618bcde3284a69cbcbb)
(`v2026.10.20`), including phase-2 qrels and DSQA canonical answer sets. This
incorporates the final revisions. BCP has 80 unique questions; duplicate
annotation records are not extra evaluation questions.

Decoded inputs and outputs belong outside this public repo:

```bash
export CMASS_WORK=/absolute/private/cmass-evaluation
for dataset in bcp trqa hotpotqa dsqa; do
  python3 evaluation/prepare_inputs.py --dataset "$dataset" \
    --output "$CMASS_WORK/$dataset"
done
```

The helper validates counts, IDs and qrels, writes question-only
`generation/questions.tsv`, and places `ground_truth.jsonl` and `qrels.txt` in
a separate `evaluation/` directory. It writes a Piika manifest and SHA-256
receipt and refuses to overwrite an existing directory. IDs are strings scoped
by dataset; do not join datasets on bare IDs.

Gold, qrels, hop annotations and supplied documents are **not generation
inputs** in either primary condition. Piika may receive qrels as evaluator
metadata but must not insert them into model context. Inclusion and exclusion
qrels are both positive relevant evidence.

## 2. Configure the harness and models

Experiments used Piika 0.3.0 with experiment-local source snapshots; their prompt
hashes are in [PROVENANCE.md](evaluation/PROVENANCE.md). A public CLI reference
with manifest support is
[Piika `c0447d78`](https://github.com/castorini/piika/tree/c0447d78a9f1a63fd86460a29a24cfb4bba14817).
It is a reproduction starting point, not a byte-identical archive of every
experiment-local runtime.

Use a dedicated Piika checkout per dataset, or configure them sequentially.
From the CMASS root, after setting `PIIKA_CHECKOUT` and `dataset`:

```bash
python3 evaluation/protocol.py --dataset "$dataset" configure-piika \
  --checkout "$PIIKA_CHECKOUT"
cd "$PIIKA_CHECKOUT"
npm ci
npm run install:benchmark-manifest -- \
  --manifest "$CMASS_WORK/$dataset/benchmark.json" \
  --root "$CMASS_WORK/manifests"
export PIIKA_BENCHMARKS_DIR="$CMASS_WORK/manifests"
```

The configuration helper accepts only known source hashes and installs the
archived standard or DSQA prompt. Use the model revisions, parsers and sampling
values in [SETTINGS.md](evaluation/SETTINGS.md). Use a fresh agent state with
sessions, automatic compaction and unrelated context disabled. Credentials stay
outside the repository.

## 3. Generate six runs per dataset

### Piika retrieval

Expose only `search` and `read_document` over the `climbmix-400b` BM25 index.
The recorded transport was Pyserini REST. A local implementation is acceptable
if it preserves the index, ranking and model-visible output and records its
provenance; a hosted endpoint is optional.

Search returns up to five hits, with whitespace-normalized previews of at most
500 characters. The archived REST launchers use paginated backend reads by
default: the two-tool `read_document` call supplies only a document ID, so it
receives the first 200 lines. It does not expose offset/limit arguments to the
model. This is the operation called `get_document` in the projection pipeline.
Provider web-search tools are not enabled.

Choose provider/model, thinking and timeout explicitly from the settings record:

```bash
npm run run:benchmark:query-set -- \
  --benchmark "cmass-$dataset-20261020" --query-set final \
  --query-file "$CMASS_WORK/$dataset/generation/questions.tsv" \
  --qrels "$CMASS_WORK/$dataset/evaluation/qrels.txt" \
  --output-dir "$CMASS_WORK/runs/$dataset/$model/piika" \
  --model "$PI_MODEL" --thinking "$THINKING" --pi "$PI_BIN" \
  --timeout-seconds "$TIMEOUT_SECONDS" --prompt-variant plain_minimal \
  --output-mode answer --tool-interface pyserini-rest-2tool \
  --index-path climbmix-400b
```

For REST set `PYSERINI_REST_BASE_URL`, `PYSERINI_REST_INDEX=climbmix-400b`, and
`PYSERINI_REST_TOKEN_ENV` to your credential environment-variable name. The
archived launcher default is `PYSERINI_REST_READ_MODE=paginated`; explicitly
setting `full` would change the model-visible document content. The
[service documentation](https://github.com/TREC-RAG/trec-rag-skills/tree/main/skills/pyserini-rest-api)
maintains access details. Record the actual index/service revision and date;
nominal client BM25 defaults do not necessarily configure a prebuilt REST index.

### Closed-book

Use the closed-book system prompt and exactly one user message containing the
question. The isolated Pi runner disabled tools, sessions, extensions, skills,
prompt templates, themes and project context. Full-context Qwen continuations
used direct vLLM requests with the same two-message input. This is a separate
path, not the retrieval CLI with a zero search budget.

Enforce the ban on the **outgoing provider request**, including OAuth-backed
Sol: `tools: []`, `tool_choice: "none"`, `parallel_tool_calls: false`; no previous
response or conversation IDs. The open-model gateway may omit an empty `tools`
field but must still send `tool_choice: "none"`. OAuth provides authentication,
not the tool policy. Audit request bodies and events for tool activity.

To render the exact messages without calling a model, provide a private JSON
object containing `question`:

```bash
python3 evaluation/protocol.py --dataset dsqa generation \
  --condition closedbook --input "$CMASS_WORK/private-case.json" \
  > "$CMASS_WORK/private-messages.json"
```

Run both conditions for Sol, Gemma and Qwen. Select replacements by revised
input and execution validity, never answer score. Higher-budget continuations
apply only to unfinished cases; record their actual settings. Keep completed
wrong answers, subsets and abstentions. Terminal context-exhausted outcomes
count as incorrect; infrastructure errors must remain separately identified.

## 4. Judge the frozen responses

For BCP/TRQA/HotpotQA, use Piika gold-answer mode:

```bash
npm run evaluate:run -- \
  --benchmark "cmass-$dataset-20261020" \
  --inputDir "$CMASS_WORK/runs/$dataset/$model/$condition" \
  --evalDir "$CMASS_WORK/evals/$dataset/$model/$condition" \
  --groundTruth "$CMASS_WORK/$dataset/evaluation/ground_truth.jsonl" \
  --judgeMode gold-answer --model "$SOL_JUDGE_MODEL" --thinking high \
  --pi "$PI_BIN" --timeoutSeconds 900
```

**DSQA uses the custom dual-verdict request, not the scalar judge.** Prepare a
private object containing `question`, the full final `response`, and `gold` as
the decoded canonical `answer_set` list (member/aliases/value):

```bash
python3 evaluation/protocol.py --dataset dsqa judge \
  --input "$CMASS_WORK/private-judge-case.json" \
  > "$CMASS_WORK/private-judge-request.json"
```

Send that tool-free Responses request through your authenticated transport.
Save request/response, model, reasoning level, generation hash and gold hash.
Validate parsed JSON with `verify_dsqa_verdict` from `evaluation/protocol.py`.
It checks set arithmetic, not the correctness of the judge's semantic matches.
Retry execution/parsing failures only. Closed-book, Piika and DSQA qrels-only
responses use the same DSQA judge.

## 5. Compute metrics

Let `R_q` be the union of unique document IDs shown in all search results in
question `q`'s selected attempt, and `G_q` its phase-2 relevant IDs.
`Recall@all(q) = |R_q ∩ G_q| / |G_q|`. Report its arithmetic mean across questions,
times 100. This is not recall over only opened documents or citations.
Repeated retrieval of one ID counts once; distinct duplicate-backed IDs stay
distinct in both numerator and denominator.

Average tool calls is `sum(search_calls + read_document_calls) / N`, including
selected model-unanswered attempts. Count agent invocations, not internal HTTP
retries. Accuracy uses the full dataset denominator. DSQA reports strict and
relaxed accuracy from the same responses. [metrics.py](evaluation/metrics.py)
implements these formulas for private normalized records and decoded qrels.

Role-specific recall uses each role's qrels as its denominator; exclude questions
with a zero denominator for that role and report the eligible count. Hop coverage
averages `covered_hops / total_hops` per question, where a hop is covered if at
least one retrieved ID belongs to its supporting set. The per-hop mapping is
withheld, so hop coverage cannot be recomputed from public question-level qrels
alone. It measures retrieved support, not causal use of evidence.
The [hop-groundedness guide](evaluation/HOP_GROUNDEDNESS.md) provides the exact
evaluator, input schema and twelve results. The [system-run sharing guide](evaluation/SYSTEM_RUNS.md)
provides retrieval-ID exports and cross-model candidate pools for qrel expansion.
Those exports remain private and are available from the authors on request.

## Separate qrels-only diagnostic

For Sol questions incorrect in the revised retrieval run, supply phase-2 qrel
document text and ban all tools/search/web at the request level. Supply no gold
or prior judgment. Oversized evidence used full-coverage evidence batches and
final synthesis. This is an oracle-evidence diagnostic on a failure subset,
not an all-question score. Its [results](evaluation/RESULTS.md#qrels-only-sol)
use the revised failure subset; additional requested diagnostics do not enlarge
that denominator.

## Validation and publication

Pin inputs, qrels, prompts, runtime, configuration and selected-attempt hashes.
Check exact ID coverage, no gold leakage, tool policies, final-answer extraction
and judge validity. Keep per-question artifacts private; publish aggregate
results and code here. This documentation update does not rerun the models.
