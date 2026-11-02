---
name: cmass-pipeline
description: Run or extend the CMASS projection pipeline - Stage 0 task construction and Stage 1 projection per dataset (BrowseComp-Plus, TRQA, DSQA, HotpotQA), Stage 2 (audit, revision, adversarial check, human review, final check) and Stage 3 (qrels construction and duplicate expansion) on projected question-answer candidates over a fixed corpus. Use when asked to project a benchmark onto the corpus, build tasks, run the projector or the Stage 1 gates, audit projected questions, repair or revise them, run the hunter/verifier check, finalize a reviewed set, build or verify qrels, expand qrels over duplicates, or set up a run directory. Triggers include "project", "stage 1", "projection", "build tasks", "near misses", "audit the projections", "stage 2", "final check", "finalize", "build qrels", "qrels phase 1", "duplicate expansion", "run directory", "cmass.json".
---

# CMASS pipeline (Stages 0-3)

Code in `pipelines/cmass/`; read its `README.md`, then `stage1/<dataset>/README.md`, `stage2/README.md` or
`stage3/README.md` for the step at hand.

## Stage 0 and Stage 1

Each dataset's procedure, as it was run, is in `stage1/<dataset>/` (`bcp`, `trqa`, `dsqa`, `hotpotqa`): task
construction into `tasks/<set>.jsonl`, Workflow scripts with their agent guides, a state tool that decides what is
pending, the builders, and `MANUAL.md`-style manual decisions. It ends by writing `inputs/<set>.jsonl` and
`inputs/<set>_near_misses.jsonl` for Stage 2. Stage 1 agents search with `tools/cm.py ... --stage 1 --step <step>`
(each step its own search log) and `--center`. Check Workflow syntax with `node tools/check_workflows.js <dir>`
(`node --check` is not enough). Do not change a dataset's steps, criteria or filters: they document what was run.

## Setup

- A run directory holds `cmass.json` (corpus backend and candidate sets; see `cmass/config.py`) and
  `inputs/<set>.jsonl` (Stage 1 candidates). Every tool runs inside it (or with `$CMASS_RUN`).
- The corpus is a two-command CLI (`search`, `doc`, JSON out) or a Pyserini-style REST server; requests are
  paced host-wide (`min_gap_s`, `slots`) because many agents share one service, which may limit requests per token.
- Each set has a dataset family (`hotpotqa`, `bcp`, `trqa`, `dsqa`, `generic`) that sets its permitted revisions
  and whether the final check runs.

## Order of work

| step | how | check |
| --- | --- | --- |
| C1 packets | `tools/packets.py SET` | |
| C2-C3 audit + second reader | Workflow `stage2/audit.js` (guide `stage2/AUDIT.md`) | `tools/validate.py SET` 0 errors |
| C5 hunter + verifier | Workflow `stage2/adversarial.js` (`stage2/ADVERSARIAL.md`) | `tools/check_records.py` |
| C4 revision | Workflow `stage2/revision.js` (`stage2/REVISION.md`) | `tools/check_records.py` |
| C5 human review | `tools/assemble.py`, `review_site/build_site_data.py review/site_config.json --out review/data`, `review_site/serve.py --site review` | |
| C6 final check | Workflow `stage2/final_check.js` (`stage2/FINAL_CHECK.md`) | |
| output records | `tools/finalize.py` | stops on any quote mismatch |
| Stage 3 phase 1 | `stage3/qrels.py --set SET pool`, Workflow `stage3/phase1.js` (`stage3/QRELS.md`), `build`, `verify` | `stage3/qrels_state.py` |
| Stage 3 phase 2 | `stage3/dups.py`, Workflow `stage3/phase2.js`, `qrels.py verify --phase phase2` | |

`tools/stage2_state.py` and `stage3/qrels_state.py` are the source of truth for progress: read state from disk,
never from an agent's report of success.

## Rules

- Before launching any Workflow, tell the user what it will run and an estimate of its cost, and wait for approval.
- Never overlap search-heavy steps (audits, hunts, recall) with other runs on the same corpus token.
- Run directories hold benchmark content. **This repository is public: never commit a run directory or anything
  from one** (questions, answers, hops, quotes, document text, qrels, reviews). Examples and demos use invented
  entities only (`pipelines/cmass/demo/`, listed `[synthetic]` in `.leak-scan-allow`).
- Test changes offline first: `demo/make_stage2_demo.py` and `demo/test_stage3.py` cover every deterministic step.
