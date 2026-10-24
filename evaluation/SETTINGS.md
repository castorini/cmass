# Recorded evaluation settings

These settings describe the October 2026 four-dataset experiments, using the
latest revised questions. The final tables combine frozen accepted outputs and
explicit continuations of unfinished cases. They are **not a uniform rerun at
the maximum cap**. A new run at a common maximum budget is a new experiment.

## Models and decoding

| Setting | Sol | Gemma | Qwen |
| --- | --- | --- | --- |
| Model ID | `gpt-6.1-sol` | `google/gemma-4-31B-it` | `Qwen/Qwen3.5-9B` |
| Checkpoint revision | Hosted model; no weight revision exposed | `842da3794eaa0b77d5f08bae87a17459d91ff475` | `c202236235762e1c871ad0ccb60c8ee5ba337b9a` |
| Reasoning | `high` | Pi `medium`, `enable_thinking=true` | Pi `medium`, `enable_thinking=true` |
| Serving | OAuth-authenticated Responses transport | vLLM 0.30.0, Transformers 5.19.0, BF16 | vLLM 0.30.0, Transformers 5.19.0, BF16 |
| Maximum configured context | 272,000 | 262,144 | 262,144 |
| Temperature | Not explicitly set | 1.0 | 1.0 |
| Top-p | Not explicitly set | 0.95 | 0.95 |
| Top-k | Not applicable | 64 | 20 |
| Presence / repetition penalty | Not explicitly set | Not explicitly set | 1.5 / 1.0 |
| Min-p | Not applicable | Not explicitly set | 0.0 |
| Reasoning parser | Provider transport | `gemma4` | `qwen3` |
| Tool-call parser | Provider transport | `gemma4` | `qwen3_coder` |

The Sol model configuration declared `maxTokens=128000`. Open-model requests
used output caps of 8,192, 16,384, 32,768, or the remaining context capacity.
Context includes input, hidden reasoning and final output. Earlier accepted
open-model outputs used configured context sizes of 65,536 (Gemma) or 131,072
(Qwen); revised/full-context requests used 262,144. The full-context gateway
tokenized the actual rendered request and set
`max_tokens = min(requested_budget, 262144 - prompt_tokens)`.

Open-model sampling seeds were `41 + attempt` (first attempt 42); some serving
replicas added their recorded seed offset. Server seed was 42. Keeping a model
name and temperature alone is therefore insufficient for exact request replay.

## Serving details

The recorded vLLM configuration used tensor parallel size 1, BF16,
`--language-model-only`, `--enable-prefix-caching`, GPU memory utilization 0.90,
and `--max-num-batched-tokens 8192`. Ordinary servers used `--max-num-seqs 8`;
the exact-revision server used 4. Native-context Qwen concurrency was resolved
from measured KV-cache capacity instead of assuming all requests could fit at
262,144 tokens. Concurrency is a serving detail and must be recorded with each
replica rather than treated as a uniform model property.

Gemma used the vLLM 0.30.0
[`tool_chat_template_gemma4.jinja`](https://github.com/vllm-project/vllm/blob/v0.30.0/examples/tool_chat_template_gemma4.jinja),
SHA-256 `afdbb2abe3667ccde95cc2f86919f05370339399bab5f750950a4390523b8927`.
Both models had thinking enabled. These experiments do not use the
thinking-off fallbacks from the earlier, separate BCP830 experiment.

## Budgets, continuation and termination

| Execution path | Per-question wall-clock safeguard |
| --- | ---: |
| Sol closed-book BCP, TRQA, HotpotQA; exact revised closed-book cases | 900 s |
| DSQA base generation, both conditions; other base Piika/open-model generation | 1,800 s |
| Revised retrieval and bounded open-model continuations | 3,600 s |
| Native-context continuations | 43,200 s (12 h) |
| Piika gold-answer judge and revised direct judge request | 900 s |

The parallel quick-judge worker used a 300-second HTTP read timeout; this is a
transport inactivity limit, not a generation reasoning budget. The extended
generation safeguards allowed long hidden-thinking outputs to reach a final
answer or their token/context limit. They did not make generation unbounded.

The reported outcomes include **25 Qwen BCP closed-book**, **one Qwen HotpotQA
closed-book**, and **one Gemma DSQA Piika** response that exhausted the native
context without a valid final answer. They count as failures. No final answer
was fabricated and no incomplete answer was selected for its score. Accepted
earlier responses were preserved when later cases received larger caps.

Retrieval launchers configured `PI_SEARCH_SUBMIT_NOW_TRIGGER_RATIO=0.7`: a
timer queues a user instruction to stop research and submit the final answer
after 70% of the applicable time limit. The current tool batch may finish;
subsequent retrieval is blocked. **No triggered automatic submit-now steer was
reported in the selected final runs across the four datasets**; this documentation
update does not perform a fresh full raw-trace audit. Closed-book generation
does not apply this mechanism. The exact archived prompt nevertheless contains
the conditional reminder, so it remains in the published template.

## Retrieval and judgment

Only the two functions `search` and `read_document` are available in Piika.
Search depth is at most five returned hits; preview length is at most 500
whitespace-normalized characters. The archived REST launchers default to
`readMode=paginated`. The two-tool document-read call supplies only the ID,
so the backend returns the first 200 lines; model-visible offset/limit arguments
are not available in this interface. The index is `climbmix-400b`; the runs used the Pyserini REST
backend. Saved run setup records nominal client BM25 values `k1=0.9, b=0.4`;
the REST service owns the actual prebuilt index/retriever configuration. These
client values alone do not establish its effective parameters.

Both generation and judgment used `gpt-6.1-sol` with **high** reasoning for Sol.
The common gold-answer judge applies to BCP/TRQA/HotpotQA; DSQA uses the custom
dual-verdict judge in [the reproduction guide](../REPRODUCING.md#exact-prompts).
All judge requests disable tools, web search and conversation reuse. DSQA
semantic matches are made once and strict/relaxed arithmetic is checked locally.

## Paper-ready summary

We evaluate GPT-6.1 Sol with high reasoning and Gemma 4 31B IT and Qwen 3.5 9B
with thinking enabled, in closed-book and Piika retrieval conditions. The open
models use vLLM 0.30.0 and Transformers 5.19.0 in BF16, temperature 1.0 and top-p
0.95, with top-k 64 for Gemma and 20 for Qwen; Qwen additionally uses presence
penalty 1.5, repetition penalty 1.0 and min-p 0.0. Maximum configured context
caps are 272,000 tokens for Sol and 262,144 for the open models. Accepted outputs
from smaller-budget configurations are retained; unfinished cases receive the
documented larger-budget continuations. Piika exposes only ClimbMix BM25 search
and document reading, with up to five hits per search. GPT-6.1 Sol high judges
both conditions without tools. DSQA generation requests the complete answer
set; a single custom judgment reports strict complete-set correctness and
relaxed nonempty-subset correctness, with no wrong additions and no ordering
requirement in either mode. All accuracies include terminal unanswered cases.
