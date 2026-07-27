# BCP projection pipeline

This directory contains the release implementation of the BCP -> ClimbMix
construction process:

1. Build official BrowseComp-Plus inputs.
2. Project every question into corpus-grounded hops.
3. Verify all hops and answer uniqueness with a no-search judge panel.
4. Expand each hop into high-recall qrels with BM25 query families and RRF.
5. Verify candidate documents and assemble question-level qrels.

The LLM stages use the local Codex CLI and its ChatGPT OAuth login. No OpenAI API
key is required. The ClimbMix REST token is independent of Codex authentication.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r pipelines/bcp/requirements.txt
codex login
cp .env.example .env.local
```

Set `PYSERINI_API_TOKEN` in `.env.local` or export it in the environment. The
default endpoint is `http://api.castorini.uwaterloo.ca` and the default index is
`climbmix-400b`; override them with `CM_BASE_URL` and `CM_INDEX`.

Select an available OAuth model with `--model` or `CMASS_CODEX_MODEL`. Reasoning
effort is controlled by `--reasoning-effort` or
`CMASS_CODEX_REASONING_EFFORT`.

## 1. Build inputs

```bash
python3 pipelines/bcp/build_inputs.py
```

This streams the official dataset, applies its published deobfuscation scheme,
and writes resumable per-question inputs under `work/bcp/inputs/`. Use `--qids`
or `--limit` for a small run.

## 2. Project onto ClimbMix

```bash
python3 pipelines/bcp/project.py --concurrency 4
```

Each OAuth-backed Codex task decomposes one question, searches ClimbMix through
`cm.py`, and records verbatim supporting snippets. A question is PROJECTABLE
only when every hop is grounded and the answer is uniquely derivable.

Projection requires the Codex task to call the retrieval helper over the
network. The launcher therefore defaults to `--sandbox danger-full-access`.
Run it in an isolated checkout with no unrelated secrets, or pass a stricter
sandbox supported by your Codex installation.

## 3. Verify projections

```bash
python3 pipelines/bcp/verify.py --judges 3 --concurrency 3
```

The panel receives only the document IDs already found by projection. Judges may
fetch those known IDs but may not search for replacements. Majority aggregation
requires every hop to be supported and the answer to be uniquely derivable. The
verified list is written to `work/bcp/verified_questions.jsonl`.

## 4. Expand and verify qrels

Build deterministic per-question qrel inputs:

```bash
python3 pipelines/bcp/build_qrel_inputs.py \
  --input work/bcp/verified_questions.jsonl --clean
```

Run hop-level query-family expansion, deep BM25 retrieval, Reciprocal Rank
Fusion, and document verification:

```bash
python3 pipelines/bcp/run_qrel_pipeline.py \
  --mode all --depth 120 --top-per-hop 80 --limit 10
```

For ranked lists `L_i`, the per-hop fusion score is:

```text
RRF(d) = sum_i 1 / (k + rank_i(d)), with k = 60 by default
```

Seeds are always retained as candidates. Every selected candidate is judged
against one hop with question context. Work is resumable through one JSON file
per question under `work/bcp/qrel_runs/`.

Assemble strict qrels only after every question and hop is verified:

```bash
python3 pipelines/bcp/build_qrels.py \
  --questions-jsonl work/bcp/verified_questions.jsonl \
  --output work/bcp/qrels.generated.jsonl \
  --hop-output work/bcp/qrels_hops.generated.jsonl \
  --strict
```

## Reproducibility boundary

Input building, RRF, aggregation, and release assembly are deterministic.
Projection, query generation, and LLM judgments are agentic and can vary across
models and reruns. Treat `data/bcp/` as the canonical reviewed release.
