# CMASS projection pipeline: audit, revision, human review and qrels

The dataset- and corpus-agnostic pipeline behind CMASS's four benchmarks. It takes projected question-answer
candidates and a retrieval endpoint with `search` and `get_document`, and produces audited, revised and
human-reviewed questions with question-level relevance judgments (qrels).

| Paper stage | Where |
| --- | --- |
| Stage 0: dataset and corpus preparation | duplicate detection in [`corpus_analysis/`](../../corpus_analysis/); task construction for BrowseComp-Plus in [`pipelines/bcp_climbmix/`](../bcp_climbmix/) |
| Stage 1: projection (hop/clue decomposition, grounding, answerability check, all-hops verification) | [`pipelines/bcp_climbmix/`](../bcp_climbmix/) |
| **Stage 2: audit, revision and human review** (steps C1-C6) | [`stage2/`](stage2/) and [`tools/`](tools/); human review in [`review_site/`](../../review_site/) |
| **Stage 3: qrels construction** (candidate pooling, precision filter, duplicate expansion) | [`stage3/`](stage3/) |

![Projecting QA benchmarks onto ClimbMix](../../assets/projection-pipeline.png)

## Layout

| path | what |
| --- | --- |
| `cmass/` | shared package: the run directory (`run.py`), configuration and dataset rules (`config.py`), the corpus client (`corpus.py`), text matching (`text.py`), candidate records (`records.py`), Stage 2 state (`stage2.py`) |
| `tools/` | `cm.py` (the agents' corpus CLI: search, doc, quote, verify, history), `validate.py` (audit schema and verbatim quotes), `packets.py`, `assemble.py`, `check_records.py`, `stage2_state.py`, `finalize.py` |
| `stage2/` | agent guides (`AUDIT.md`, `REVISION.md`, `ADVERSARIAL.md`, `FINAL_CHECK.md`), record formats (`SCHEMA.md`), Workflow scripts (`audit.js`, `adversarial.js`, `revision.js`, `final_check.js`) |
| `stage3/` | the judging standard (`QRELS.md`), `qrels.py` (phase 1), `dups.py` (phase 2), `qrels_state.py`, Workflow scripts (`phase1.js`, `phase2.js`) |
| `demo/` | an invented corpus and offline corpus CLI, and generators that run every deterministic step end to end |

Agentic steps are Claude Code Workflow scripts: one agent per question (or per chunk of documents), one
output file per unit of work, and progress read from disk by the state tools, so every workflow resumes
where it stopped and never trusts an agent's own report. Deterministic steps are Python 3.10+ with the
standard library only (phase 2 reads the duplicate map with `pyarrow` when it is a parquet copy).

## The run directory and its configuration

Each run is one directory holding the Stage 1 candidates and every stage's per-question records (layout in
[`cmass/run.py`](cmass/run.py)). Its `cmass.json` names the corpus backend and the candidate sets:

```json
{
  "corpus": {
    "name": "climbmix-400b",
    "backend": "cli",
    "cli": {"search": ["python3", "/path/to/cm.py", "search"], "doc": ["python3", "/path/to/cm.py", "doc"]},
    "min_gap_s": 2.0, "slots": 1
  },
  "sets": {
    "set_a":    {"input": "inputs/set_a.jsonl", "family": "generic", "group": "released"},
    "nm_set_a": {"input": "inputs/set_a_near_misses.jsonl", "family": "generic", "group": "near_miss"}
  }
}
```

- **Corpus backend.** `cli` runs the same two-command CLI as Stage 1 (`search "<query>" <hits> <preview_chars>`
  and `doc <docid>`, both printing JSON); `rest` talks to a Pyserini-style REST server (`base_url`, `index`,
  and `token_env`, the name of the environment variable holding a bearer token). Documents and searches are
  cached in the run directory; requests are paced across every process on the host (`min_gap_s`, `slots`),
  and identical searches in flight are shared, because many agents use one corpus service.
- **Dataset families** carry the paper's revision limits: `hotpotqa` (correct wording or add one grounded
  qualifier; the answer stays), `bcp` (evidence-only repairs, near misses may change one clue, range or
  qualifier; the answer stays except for typos), `trqa` (a qualifier, or a new attribute when the answer is
  trivial; recompute only under the completeness standard), `dsqa` (remove a source clause, add a qualifier,
  adjust a year or threshold), and `generic`. The final check runs for every family except `hotpotqa`. A set
  can override any of these in `cmass.json`; see [`cmass/config.py`](cmass/config.py).

## Run it

Stage by stage, with the commands in order: [`stage2/README.md`](stage2/README.md) and
[`stage3/README.md`](stage3/README.md). In short:

```bash
cd my_run                                          # holds cmass.json and inputs/<set>.jsonl
python3 .../tools/packets.py SET                   # C1: auditor packets
# Workflow stage2/audit.js        {"set": SET, "qids": [...]}   C2-C3 audit and second reader
# Workflow stage2/adversarial.js  {"set": SET, "qids": [...]}   C5 hunter and verifier
# Workflow stage2/revision.js     {"set": SET, "qids": [...]}   C4 repairs, re-audit, re-hunt
python3 .../tools/assemble.py                      # review records and the review-site config
python3 review_site/build_site_data.py review/site_config.json --out review/data
python3 review_site/serve.py --site review         # C5 human review
# Workflow stage2/final_check.js  {"set": SET, "qids": [...]}   C6 final check
python3 .../tools/finalize.py                      # final/<set>_final.jsonl, final/<set>_excluded.jsonl
python3 .../stage3/qrels.py --set SET pool         # Stage 3, phase 1 (then Workflow stage3/phase1.js)
python3 .../stage3/qrels.py --set SET build && python3 .../stage3/qrels.py --set SET verify
# Stage 3, phase 2: stage3/dups.py and Workflow stage3/phase2.js
```

`tools/stage2_state.py` and `stage3/qrels_state.py` say where every question stands at any point.

## Try it offline

The demo uses an invented corpus about a fictional island nation and invented candidates, so it needs no
corpus service and holds no benchmark content:

```bash
python3 pipelines/cmass/demo/test_stage3.py           # Stage 3 phase 1 and phase 2, end to end
python3 pipelines/cmass/demo/make_stage2_demo.py      # Stage 2 records for every path of the flow
cd pipelines/cmass/demo/run
python3 ../../tools/validate.py && python3 ../../tools/check_records.py && python3 ../../tools/stage2_state.py
python3 ../../tools/finalize.py --partial
```

## Privacy

A run directory holds benchmark questions, answers, hops, quotes, document text and qrels. **Keep run
directories out of this public repository** (`demo/run/` is gitignored; see [`AGENTS.md`](../../AGENTS.md)).
Released datasets go out only through the obfuscated Hugging Face release, which, like BrowseComp-Plus, Total
Recall QA and DeepSearchQA, publishes questions, answers and question-level qrels but not hop decompositions,
per-hop qrels or per-question pipeline records.
