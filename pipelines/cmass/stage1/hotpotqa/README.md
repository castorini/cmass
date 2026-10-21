# HotpotQA → ClimbMix: Stage 0 task construction and Stage 1 projection

This directory is the procedure that produced the HotpotQA candidates of CMASS, as it was run, ported to
the run-directory conventions of [`pipelines/cmass`](../../README.md). It ends by writing the two Stage 2
inputs: `inputs/hotpotqa.jsonl` (157 candidates in the run) and `inputs/hotpotqa_near_misses.jsonl` (103
near misses). It holds code and agent guides only, no data: every source is read at run time.

HotpotQA questions reached Stage 2 by two paths that share one contamination screen and four gates:

- **Path A, NanoKnow-supported** (81 candidates): the 553 questions that
  [NanoKnow](#what-nanoknow-is) had already verified as supported by ClimbMix, with NanoKnow's qrels as
  each question's candidate documents.
- **Path B, ranked** (76 candidates): bridge questions NanoKnow had not verified, ranked by a hosted
  classifier (Jev) on how well ClimbMix appears to state their weakest supporting fact; the top 450, in
  three batches of 150, got candidate documents by retrieval.

There is **no PROJECTABLE / PARTIAL / NOT labelling step** for HotpotQA: no script, prompt or output of
the HotpotQA effort uses those labels. The gates were: contamination screen, bridge only, extreme,
uniqueness, coherence, annotator.

## The procedure as it was run

Dates are from the private repository's commits (all 2026). "Agent" steps are Claude Code Workflow scripts,
one agent per question, of the general-purpose agent type; no script set a model, so the agents ran on the
session's default model. The run's commits are co-authored by Claude Opus 5 and its handoff counts the
agents' usage in Opus tokens; no per-agent model is recorded. From 2026-09-24, during batch 3, the workflows
capped agents in flight at 4, and from 09-25 they stopped launching after 4 consecutive agent deaths; an
agent that died left no verdict and its question was rerun, never dropped. The prompts did not change during
the run.

### Stage 0: task construction

| step | input | what | who | output |
|---|---|---|---|---|
| 0.1 | HotpotQA dev set, distractor configuration (Hugging Face `hotpotqa/hotpot_qa`, `distractor`, `validation`): 7,405 questions | one task per question; the gold supporting paragraphs become `hints`, the official supporting-fact sentences `supporting_facts` | `stage0_tasks.py` | `tasks/hotpotqa.jsonl` |
| 0.2 | NanoKnow's HotpotQA files (`castorini/NanoKnow`, `questions-and-qrels/hotpotqa/`) | join: each task is NanoKnow `supported` (553) or `unsupported` (6,852); supported tasks get NanoKnow's qrels (2,879 pairs, 5.21 documents per question). A NanoKnow qid is the row index of the question in the dev set: all 7,405 question texts match their rows exactly, and HotpotQA's own `type` field (bridge / comparison) comes from the same row | `stage0_tasks.py` | the same tasks |
| 0.3 | NanoKnow's qrels | 49 document ids are zero-padded in their row part (`shard_SSSSS_0RRRR` where the index serves `shard_SSSSS_RRRR`) and 404 against the corpus; repaired (43 questions affected) | `stage0_tasks.py` (originally repaired in the local document cache) | the same tasks |

The NanoKnow file paths in `stage0_tasks.py` are the ones NanoKnow's README names (they could not be checked
against the Hugging Face repository while porting); if they differ, put the four files in the `--nanoknow`
directory by hand under those file names. Use the distractor configuration: its `context` holds the gold
paragraphs by construction. The fullwiki configuration's `context` holds retrieved paragraphs, and 1,243 of
the 7,405 questions reconstruct none of their supporting facts from it (found and fixed during the run,
before the ranking).

### Path A: NanoKnow-supported (2026-09-12 to 09-21)

| step | what | who | in → out |
|---|---|---|---|
| A1 | select every NanoKnow-supported question; its candidate documents are its NanoKnow qrels | `select_questions.py nanoknow` | 553 |
| A2 | **contamination screen**: leakage (one document poses the question and gives the gold answer, or packages its final hop as a ready-made Q&A) and single-document answerability (one document carries the whole chain, bridge included); clean = neither ([`CONTAMINATION.md`](CONTAMINATION.md)) | agent, `contamination.js` | 553 → **190** clean: 57 leaked (35 packaged-qa-for-final-hop, 18 poses-question-and-answers-it, 4 quiz-or-answer-key-page), 353 single-document answerable, 47 both |
| A3 | **bridge only**: comparison questions excluded by HotpotQA's `type` field. A decision taken during the run, after the screen, by the person running it (manual decision, applied deterministically) | `select_questions.py stage-gates`, `hpqa_state.py` | 190 → **120** (70 clean comparison questions excluded; comparison questions were 16% of the 553 but 37% of the clean 190, since "are X and Y both ..." needs one document per entity) |
| A4 | **extreme**: atomize the question, decompose it into hops (one marked as the bridge), ground every hop on full document text with a fresh verbatim snippet (at most 4 rescue searches); keep = every atom covered and every hop grounded ([`GATES.md`](GATES.md)) | agent, `gates.js` | 120 → **103** (17 dropped, all on an ungrounded hop) |
| A5 | **uniqueness**: 6 to 15 searches for a rival answer the evidence does not exclude, and is the gold answer supported; keep = unique and supported | agent, `gates.js` | 103 → **81** (22 dropped: 16 answer multiplicity, 5 both, 1 corpus contradicts the gold answer) |
| A6 | **coherence**: the hops agree on entity, date, sense and bridge, and jointly derive the gold answer | agent, `gates.js` | 81 → **81** |
| A7 | **annotator**: an agent prompted as a careful human annotator reads each hop: keep, fix (better snippet from the hop's verified documents, or clearer clue wording) or drop | agent, `gates.js` | 81 → **81** |

The gates were calibrated first on 10 of the 190 (6 kept), and 29 comparison questions got gate runs
before the bridge-only decision; none of those reached the outputs (two that passed every gate were
removed by the type filter). The port never sends a comparison question to the gates.

### Path B: ranked (2026-09-21 to 09-26)

| step | what | who | in → out |
|---|---|---|---|
| B1 | the pool: NanoKnow-unsupported bridge questions (type filter first on this path) | `select_questions.py pool` | 6,852 → **5,456** |
| B2 | **ranking screen**: per supporting fact, BM25 retrieval (`"<title> <sentence>"`, 20 hits, the title alone when that gives fewer than 10 documents), the first 5 documents judged by Jev ("does this document state this fact?"); a fact scores its best document, a question its weakest fact. Ranking only: nothing is dropped on the score | `rank_screen.py screen` (Jev via OpenRouter; see [the ranking step](#the-ranking-step)) | 5,456 → 5,441 screened (15 retrieval failures, never retried, not recorded) → **5,440** complete and ranked; 274 scored ≥ 0.95, 1,148 ≥ 0.7; $5.02 |
| B3 | order by weakest-fact score | `rank_screen.py order` | `ranked.jsonl` |
| B4 | **batches**: the next 150 of the ranking (batch size and the number of batches chosen by the person running it: manual) | `select_questions.py batch` | batch 1 = ranks 1-150 (scores 0.96-0.98), batch 2 = 151-300 (0.94-0.96), batch 3 = 301-450 (0.91-0.94) |
| B5 | **candidate documents**: the same two queries per supporting fact, the top 6 documents of each kept, union | `collect_docs.py` (k = 6) | mean 22.1 / 22.8 / 22.5 documents per question |
| B6 | contamination screen, as A2 | agent, `contamination.js` | 150 → 44 / 45 / 51 clean (leaked 35 / 41 / 34, single-document 96 / 93 / 92, both 25 / 29 / 27): **140** of 450 |
| B7 | extreme, uniqueness, coherence, annotator, as A4-A7 | agents, `gates.js` | extreme 44 → 32, 45 → 31, 51 → 39 (102); uniqueness → 24, 23, 29 (**76**; 26 dropped: 24 answer multiplicity, 1 both, 1 corpus contradicts the gold answer); coherence and annotator dropped none |

A fourth batch (ranks 451-600) was collected and staged but only one of its questions was screened, and
a fifth was collected only; neither contributed a question. The run stopped at 157 against a target of
200, with 4,990 ranked questions unspent.

### End of Stage 1: the Stage 2 inputs

| step | what | who | output |
|---|---|---|---|
| C1 | **candidates**: annotator keep and bridge. Hops are the extreme audit's; an annotator "fix" is applied only under the guard (its document is one the extreme audit verified for that hop and its snippet is verbatim in those documents), else the hop keeps the extreme evidence; every snippet is verified verbatim | `build_inputs.py` | **157** (81 path A + 76 path B): 497 hops (mean 3.17), 902 distinct documents; hop outcomes 302 keep, 182 fixes applied, 13 fixes rejected by the guard; 497 / 497 snippets verified |
| C2 | **near misses**: every bridge question that reached the gates and was dropped there; each failed exactly one gate | `build_inputs.py` | **103** = 55 extreme (17 A + 38 B) + 48 uniqueness (22 A + 26 B). Of the 48: 40 answer multiplicity (the question does not say which of several valid answers is wanted: HotpotQA's wording, not a gap in ClimbMix), 6 both, 2 corpus contradicts the gold answer |

Both lists went to Stage 2 unchanged (the HotpotQA set and its near-miss set).

Measured cost per batch of 150: retrieval about 20 minutes and no tokens; the contamination screen about
10M tokens; the four gates about 10M tokens.

## How this maps to the paper's Stage 1

| paper operation | HotpotQA | difference |
|---|---|---|
| hop/clue decomposition | inside the **extreme** gate (step 2 of `GATES.md`), after the contamination screen; the hops are built by the same agent that grounds them | HotpotQA's supporting facts are not the hops: they are retrieval queries (path B) and NanoKnow's verification units (path A) |
| grounding | the **extreme** gate grounds each hop on the question's candidate documents (NanoKnow qrels, or the retrieved pool), with at most 4 rescue searches | not an open search per hop; for path A, NanoKnow had already retrieved and verified every supporting fact |
| answerability check | no PROJECTABLE / PARTIAL / NOT step. The closest are NanoKnow's own verification (path A: every supporting fact stated and the answer follows, 553 of 1,235 verified questions held) and the **uniqueness** gate (both paths: no rival answer, gold answer supported) | path B had no answerability filter before the agents: the ranking never drops a question |
| all-hops verification | the **extreme** gate's keep rule (every atom covered, every hop grounded), then **coherence** and the **annotator** | one agent decomposes, grounds and verifies in one pass; there is no independent re-check of the same hops |
| (not in the paper's Stage 1) | the **contamination screen** before everything; **bridge only**; Jev ranking and batches; the annotator's limited repairs (snippet, clue wording) under the guard | — |

No person reviewed or repaired a question in Stage 1: the "annotator" is an agent. The manual steps were
decisions about the run: excluding comparison questions, the batch size, and how many batches to run.

## What NanoKnow is

NanoKnow (`castorini/NanoKnow` on Hugging Face) is a separate Castorini project that projects QA benchmarks
(NQ, SQuAD, HotpotQA) onto ClimbMix. For HotpotQA its README (copied into the private repository) says: 864
questions were removed up front (answer guessable from the question text, and nationality questions);
each official supporting fact was searched several ways (the fact sentence, the article title plus the fact,
the title plus the fact's rarest terms, the title alone); an agent then verified each fact against one
document stating the whole fact with every date, number and name, and judged separately that the gold
answer follows; 1,235 questions were verified this way and 553 held; snippets were machine-checked; qrels
were granted on confirmed evidence only and closed over the ClimbMix duplicate map (Jaccard ≥ 0.7, both
directions). Verification was spent highest-yield first, so the unsupported file holds unverified questions,
not refuted ones: that is the pool path B ranked. CMASS used NanoKnow's partition and qrels as given.

## The ranking step

`rank_screen.py` drives retrieval and aggregation; the per-document judgement is a plug-in:

```python
def judge(fact: str, fact_about: str, document: str) -> dict:
    # fact: one supporting-fact sentence; fact_about: its article title; document: first 12,000 characters
    return {"states_fact": 0.97, "same_entity": 0.99, "grade": 3, "cost": 0.0009}   # or {"error": "...", "cost": 0}
```

Only `states_fact` (a probability) decides the ranking. `stub_ranker.py` is a word-overlap stand-in for the
demo. The ranker that was run is **Jev** (TypeSafe), called directly from the original `jev/screen.py` (not
through the llm-rankers project):

- endpoint `POST https://openrouter.ai/api/alpha/decisions`, model `~typesafe/jev-latest`, key from the
  environment variable `OPENROUTER_API_KEY` (bearer token); up to 4 tries, backing off on 429 and 5xx;
- request body `{"model": ..., "state": {"fact", "fact_about", "document"}, "questions": {...}}` with three
  questions:
  - `states_fact` (type `noul`): instructions "Does the document's own text state this fact, about the same
    entity, including any dates, numbers and names it contains?"; criteria true "The document explicitly
    states this fact about the same entity.", false "The document is merely on topic, is about a similar but
    different entity, or the fact must be inferred.";
  - `same_entity` (type `noul`): "Is the document about the same entity the fact is about?"; true "Same
    entity.", false "A different or similarly-named entity.";
  - `support_grade` (type `score`): "Rate how well the document supports the fact."; levels "Unrelated.",
    "On topic but does not state the fact.", "States part of the fact.", "States the whole fact explicitly.";
- response: `answers.states_fact.noul`, `answers.same_entity.noul`, `answers.support_grade.score`,
  `usage.cost`.

An adapter with this contract is a few lines; it is not shipped here. Measured afterwards on documents the
gate agents demonstrably accepted or rejected, Jev was at chance for the pipeline's adequacy judgement
(precision 43.8% against a 39% base rate; AUC 0.625 on matched pairs), which is why it only orders the pool.
Its scores are rounded, so ties are common; the original ranking script was not kept and its tie order
cannot be recovered (it decided which of the questions scoring 0.91 fell inside the top 450). This port
breaks ties by mean fact score, then record id. The screen made about 27,000 corpus requests: at a 30
requests per minute token limit, plan on many hours.

## Commands, in order

`S1` is this directory, `TOOLS` is `pipelines/cmass/tools`. Create a run directory **outside this
repository** with a `cmass.json`:

```json
{"corpus": {"name": "climbmix-400b", "backend": "rest",
            "rest": {"base_url": "https://<pyserini-server>", "index": "climbmix-400b", "token_env": "PYSERINI_API_TOKEN"},
            "min_gap_s": 2.0, "slots": 1},
 "sets": {"hotpotqa":    {"input": "inputs/hotpotqa.jsonl", "family": "hotpotqa", "group": "released"},
          "nm_hotpotqa": {"input": "inputs/hotpotqa_near_misses.jsonl", "family": "hotpotqa", "group": "near_miss"}}}
```

```bash
cd RUN
# Stage 0
python3 S1/stage0_tasks.py --nanoknow source/nanoknow [--download-nanoknow] [--hotpot FILE]

# Path A
python3 S1/select_questions.py nanoknow hotpotqa
# Workflow S1/contamination.js  {"run": RUN, "tools": TOOLS, "set": "hotpotqa"}
# Workflow S1/gates.js          {"run": RUN, "tools": TOOLS, "set": "hotpotqa"}   (stages the gate inputs itself)

# Path B
python3 S1/select_questions.py pool hotpotqa
python3 S1/rank_screen.py screen hotpotqa --ranker <your Jev adapter>.py --hits 20 --docs 5   # rerun until no retrieval failures
python3 S1/rank_screen.py order hotpotqa
python3 S1/select_questions.py batch hotpotqa --size 150        # batch 1; then 2 and 3 after each batch's gates
python3 S1/collect_docs.py hotpotqa --batch 1 --k 6
# Workflow S1/contamination.js  {"run": RUN, "tools": TOOLS, "set": "hotpotqa"}
# Workflow S1/gates.js          {"run": RUN, "tools": TOOLS, "set": "hotpotqa"}

# At any time
python3 S1/hpqa_state.py list hotpotqa [--next contamination]   # every question's next step
python3 S1/hpqa_state.py summary hotpotqa                       # the funnel per path and batch

# End of Stage 1
python3 S1/build_inputs.py hotpotqa       # inputs/hotpotqa.jsonl, inputs/hotpotqa_near_misses.jsonl
python3 TOOLS/packets.py hotpotqa         # Stage 2 starts here (stage2/README.md)
```

Every agent step writes one file per question (`stage1/hotpotqa/<step>/<qid>.json`, layout in
[`hpqa_common.py`](hpqa_common.py)) and `hpqa_state.py` decides from those files alone what is pending; a
file counts only when `hpqa_state.py check` reports 0 errors for it, and its verdict field (not its
existence) decides whether the question goes on. Rerunning a workflow resumes where it stopped.

## Files

| file | what |
|---|---|
| `stage0_tasks.py` | Stage 0: tasks from HotpotQA (distractor dev) and NanoKnow, with the checks above |
| `select_questions.py` | path A selection, path B pool and batches, gate-input staging |
| `rank_screen.py`, `stub_ranker.py` | path B ranking (pluggable ranker) and the demo's stand-in ranker |
| `collect_docs.py` | path B candidate documents and screen inputs |
| `contamination.js`, [`CONTAMINATION.md`](CONTAMINATION.md) | the contamination screen: Workflow script and the agents' guide |
| `gates.js`, [`GATES.md`](GATES.md) | the four gates: Workflow script and the agents' guide |
| `hpqa_state.py` | per-question state, output checks, the funnel |
| `build_inputs.py` | candidates (guard, snippet verification) and near misses, in `cmass/records.py` format |
| `hpqa_common.py` | shared paths and the snippet guard |
| `demo/` | invented corpus and `make_demo.py`, which runs every deterministic step on hand-written agent outputs |

## Differences between this port and the original scripts

- One run directory with per-step folders, instead of `contamination/runs/` and `pipeline/<gate>/runs/`.
- Agents read documents through `tools/cm.py doc` (the run's cache) instead of `grep` over local text files,
  and the gates search with the shared `cm.py search --stage 1 --step <gate> --center`: previews centred on
  the query terms, as the original CLI's were. Each step (the screen and each gate) has its own search log,
  `stage1/<set>/<step>/<qid>.searchlog.jsonl`, and `search` refuses a query too close to one the same step
  already ran for the question (the original CLI had no such refusal).
- Agent prompts are the original prompts, moved into `CONTAMINATION.md` and `GATES.md`; the original
  "if the output exists, return it" step is replaced by the state tool. `hpqa_state.py check` enforces the
  original output schemas and keep rules (every original output satisfies them) and warns about snippets it
  cannot find verbatim in cached documents, feedback the original agents did not get.
- NanoKnow's zero-padded document ids are repaired at Stage 0. The original repaired them in its document
  cache and kept the padded ids in its records (7 remained in the 157 candidates).
- The contamination-screen input drops the original's `doc_dir` field (the documents are in the run cache).
- Comparison questions never reach the gates; ranking ties are broken deterministically (above); a
  question whose ranking retrieval fails can be retried by rerunning (the original left 15 unretried).
- Output records follow `cmass/records.py`: `answer` (a string), `answer_type` `single`, hops with `kind`
  `fact` and `redundant` false plus HotpotQA's `index` and `is_bridge`, `hints`, `source`, `hotpot_type`,
  `provenance`; near misses add `failed_gate`, `stage_rank`, `reason` and the gate detail. The original
  records had `answers` (a list), `hotpot_type` and `n_hops`.

`detect.py`, a deterministic keyword pre-screen for leakage and single-document answerability run before
the agent screen, only flagged questions for the agents' attention and filtered nothing; it is not ported.
The Jev exploration and document-filter analyses contributed no questions. The same contamination screen
and gates were later reused, with dataset-specific edits, for MuSiQue and ORBIT.

## Try it offline

```bash
python3 pipelines/cmass/stage1/hotpotqa/demo/make_demo.py      # builds stage1/hotpotqa/demo/run (gitignored)
```

Nine invented questions about the island nation of Oskeria cover both paths: a survivor of every gate with
an annotator fix applied, one with a fix rejected by the guard, a leaked question, a single-document one, a
comparison question excluded after a clean screen, near misses at extreme and at uniqueness, and a ranked
question outside the batch. The script ends by running `tools/packets.py` on both Stage 2 sets.

## Sources in the private repository

Branch `hotpotqa-climbmix` (consolidated into `main`): `artifacts/hotpotqa/HANDOFF.md`,
`artifacts/hotpotqa/data/README.md`, `artifacts/hotpotqa/contamination/{contamination.js,detect.py}`,
`artifacts/hotpotqa/pipeline/{hpqa_pipeline.js,build_final.py,survivors.py}`,
`artifacts/hotpotqa/jev/{screen.py,collect_docs.py,stage_batch.py,validate_docfilter.py,used_vs_rejected.py}`,
`artifacts/hotpotqa/tools/{cm.py,fetch_docs.py}`, `artifacts/near_miss/{build_near_miss.py,README.md}`,
`artifacts/HANDOFF.md` (runbook); what entered Stage 2: `artifacts/projection_audit/original/SOURCES.md` and
`artifacts/projection_audit/near_miss/original/SOURCES.md`. Reuse: `artifacts/musique/` (branch
`musique-climbmix`) and `artifacts/orbit/` (branch `orbit-climbmix`).
