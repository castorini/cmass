# Evaluation provenance

This directory documents the October 9–12, 2026 four-dataset evaluation and
its revised-input overlays. It does not turn the older BCP57/GPT-5.6 or BCP830
experiments into four-dataset results. Public files contain only templates,
code, settings and aggregates; private source maps, requests and model outputs
remain outside this repository.

## Sources checked for this update

| Archived source | What it establishes |
| --- | --- |
| `hotpotqa-piika-gpt61-high-20261009/piika/src/pi-search/agent_prompt.ts` | Standard two-tool retrieval prompt |
| Archived `tool_handlers.ts`, `pyserini_rest/adapter.ts`, and REST launchers | Search hard cap of five; ID-only reads with the launcher's paginated default (first 200 lines) |
| `hotpotqa-piika-gpt61-high-20261009/piika/src/evaluation/judge_prompt.ts` | Scalar gold-answer judge |
| `dsqa-final-sixruns-20261010/protocol.py` and `prepare.py` | Complete-set instruction, DSQA closed-book and judge strings, exact insertion into the Piika prompt |
| `projection-release-reruns-20261010/protocol.py` | Revised BCP/TRQA prompt hashes, 32k configuration and validity-only selection |
| `projection-exact-revisions-20261011/generate.py`, `judge.py`, `serve.sbatch` | Final exact-revision settings, judge model and no-tool request fields |
| `projection-final-budget-20261010/open_proxy.py` and `run.py` | Context-aware output clipping and native-context continuations |
| `projection-qwen-closedbook-native-h200-20261011/run.py` | Native Qwen input purity, 262144-token cap and 12-hour safeguard |
| `projection-qwen-native-judgments-20261011/final_results.json` | Final BCP Qwen 55 judged, 25 unanswered, 4 correct |
| `piika-results-tables-20261011/consolidated-tables/` | Final accuracy, recall/tool and qrels-only tables |
| `projection-inclusion-exclusion-recall-20261012/input/updated_results.json` | Revised source-pinned retrieval aggregates |

These are archival artifact names, not dependencies on someone else's home
directory. The public helpers accept caller-provided private output locations.
Source aggregate hashes are recorded in [results.json](results.json).

## Prompt pins

SHA-256 of the archived TypeScript source files (not the rendered templates):

| Source | SHA-256 |
| --- | --- |
| Standard `agent_prompt.ts` | `5389001e74dff70e81b6b37c2d3f718b352db7cb5d00d5292d98dd0bdb81cfbd` |
| DSQA `agent_prompt.ts` | `9889a1b4c9f737799223ac91c0d61e3efbe55f4a86364b6805da236f07c7e3af` |
| `judge_prompt.ts` | `9ede807c70adfb86f0e3555813867e025d6895f748a1c65a538f9b92208aebff` |

The standard agent and judge source hashes were checked against the archived
remote runtime as well as the local source. DSQA adds exactly the complete-set
bullet after `Keep Exact Answer directly responsive to the question.`
The closed-book DSQA prompt and judge were extracted from the archived
`protocol.py` constants. Templates have one final LF for repository formatting;
`protocol.py` removes that LF when rendering the original runtime string.

Piika is MIT-licensed; these prompt templates derive from
[`castorini/piika`](https://github.com/castorini/piika). The applicable notice is
preserved in [PIIKA_LICENSE](PIIKA_LICENSE).

## Corrections to earlier documentation

- The four-dataset retrieval prompt has no added strict-evidence-policy block.
  The older guide remains explicitly labeled as the historical BCP57 experiment.
- The four-dataset judge is GPT-6.1 Sol high, not the earlier GPT-5.5 judge.
- The archived general judge has no answer-type parameter. DSQA requires the
  separate answer-set judge; the scalar template's acceptable-alternatives
  wording must not be interpreted as strict set equality.
- Maximum context caps do not imply that every accepted output was regenerated
  with that cap. The selected tables retain earlier completed responses.
- Retrieval call counts are not percentages, and unanswered outcomes stay in
  the accuracy denominator.

## Validation boundary

Tests cover prompt rendering, guarded Piika prompt configuration, DSQA set
arithmetic (including wrong extra members), question/answer-set preparation,
macro recall with repeated IDs, and aggregate consistency. Input preparation
was checked on the pinned four-dataset release without exposing decoded items
in public logs. No fresh generation or GPT judgment was performed for this PR,
and this check does not claim a new independent full raw-trace audit.

## Hop-groundedness and system-run extension (October 24)

All 1,269 selected artifact JSON files were copied read-only and matched to the
SHA-256 pins in the archived `projection-hop-coverage-20261012/input/source_map.json`.
Their question text matches the pinned release after whitespace normalization.
The new evaluator reproduced all twelve archived hop summaries from 2,708
retained-hop mappings. The exact archived result/source-map and phase-2 hop
file hashes are in [hop_groundedness_results.json](hop_groundedness_results.json).

The [private retrieval export](SYSTEM_RUNS.md), available from the authors on
request, uses that same selection, including the
single Gemma DSQA model-unanswered attempt. It is built with an explicit field
allowlist and contains no benchmark text or raw trajectories. Cross-model pool
labels are derived from the unchanged phase-2 qrels; no new relevance judgment
or score-based attempt selection is performed. This additional check verifies
artifact integrity and metric arithmetic, not a new full event-stream audit.
Plaintext document IDs and relevance labels remain sensitive even without
benchmark text. The export is owner-only, and neither its data nor internal
access paths are part of the current public documentation.
