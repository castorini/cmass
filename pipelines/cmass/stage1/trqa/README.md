# Stage 0 and Stage 1 for Total Recall QA (TRQA)

This directory is the Stage 0 task construction and the Stage 1 projection **as they were actually run** for
Total Recall QA (TRQA; Hugging Face `mahtaa/trqa`, test split), ported from the private repository onto this
pipeline's run directory (`../../cmass/run.py`) and corpus CLI (`../../tools/cm.py`). It ends by writing the
Stage 2 inputs `inputs/trqa.jsonl` (candidates) and `inputs/trqa_near_misses.jsonl`, plus a separate step that
chose the "TRQA expansion" candidates Stage 2 added later. It holds code and agent guides only: no question,
answer, hop, document or identifier from the benchmark.

TRQA questions are aggregation queries: a set of entities defined by constraints, an operator (count, min,
max, average, sum, ...) and a target attribute give one value. Answering needs **total recall** of the set,
so TRQA's Stage 1 differs from the paper's generic description in several places (section "Paper mapping").

## Files

| file | step | what |
|---|---|---|
| `trqa_source.py` | 0 | load and decrypt the TRQA queries (and, for the expansion, qrels); `download` the raw files |
| `stage0_build_tasks.py` | 0 | one task per test question -> `tasks/<set>.jsonl` |
| `stage1_project.js`, `PROJECT.md` | 1 | Workflow + guide: the total-recall projector (one agent per question) |
| `collect_verdicts.py` | 2 | answerability check: the verdict table `stage1/<set>/verdicts.jsonl` |
| `build_strict_inputs.py` | 3 | the SUPPORTED questions -> `stage1/<set>/strict_inputs.jsonl` |
| `stage1_strict_audit.js`, `STRICT_AUDIT.md` | 4 | Workflow + guide: the strict audit (one agent per question, batches of 6) |
| `build_strict_list.py` | 5 | verdict table, the strict-audited list, the gate inputs (stops for the manual backfill) |
| `verify_snippets.py` | 6 | gate 0: a deterministic verbatim measurement of the strict list's snippets (not a filter) |
| `stage1_final_gates.js`, `FINAL_GATES.md` | 7 | Workflow + guide: the four gates (extreme, completeness, coherence, annotator) |
| `build_final.py` | 8 | apply annotator fixes under the guard, verify every snippet -> `stage1/<set>/final_list.jsonl` |
| `write_stage2_inputs.py` | 9 | `inputs/<set>.jsonl` (survivors) and `inputs/<set>_near_misses.jsonl` |
| `trqa_state.py` | all | where each question stands, from disk; validates every agent output; prints each agent's input |
| `expansion/universe.py`, `expansion/pool_sweep.py`, `expansion/select_and_build.py` | X | the expansion candidates -> `inputs/xp_<set>.jsonl` |
| `demo/` | | invented corpus (`corpus.json`, ids `demo_trqa_NNN`) and `make_demo.py`, which runs every deterministic step |

## The procedure as it was run

All counts are the real run's (verified against its files); "agent" steps are Claude Code Workflow
runs, one agent per question, whose per-question files are the record.

| # | step | who | input -> output | count |
|---|---|---|---|---|
| 0 | task construction | script | HF `mahtaa/trqa` "queries", splits wiki1/wiki2/ecommerce_test, decrypted -> `tasks/<set>.jsonl` | 2,327 tasks (wiki1 169, wiki2 1,258, ecommerce 900) |
| 1 | projection | agent | task -> `stage1/<set>/project/<qid>.json` | 2,327 projected |
| 2 | answerability check | script | projections -> `verdicts.jsonl` | SUPPORTED 119, PARTIAL 91, NOT 2,117 |
| 3 | strict-audit inputs | script | SUPPORTED -> `strict_inputs.jsonl` | 119 |
| 4 | strict audit | agent | -> `stage1/<set>/strict/<qid>.json` | 87 kept, 32 dropped |
| 5 | strict list | script (+1 manual backfill) | -> `strict_verdicts.jsonl`, `strict_list.jsonl`, `gate_inputs.jsonl` | 87 |
| 6 | gate 0, snippet measurement | script | -> `snippet_verification.json` | 200 of 527 evidence snippets verbatim (nothing dropped) |
| 7a | gate 1, extreme audit | agent | -> `stage1/<set>/extreme/<qid>.json` | 55 of 87 |
| 7b | gate 2, completeness | agent | -> `.../completeness/<qid>.json` | 53 of 55 |
| 7c | gate 3, coherence | agent | -> `.../coherence/<qid>.json` | 51 of 53 |
| 7d | gate 4, annotator | agent | -> `.../annotator/<qid>.json` | 50 of 51 |
| 8 | final list | script | -> `final_list.jsonl` | 50 questions, 277 hops, 275 of 275 evidence snippets verbatim |
| 9 | Stage 2 inputs | script | -> `inputs/<set>.jsonl`, `inputs/<set>_near_misses.jsonl` | 50 candidates, 37 near misses |
| X | expansion selection | scripts | TRQA pools of questions that had not passed Stage 1 -> `inputs/xp_<set>.jsonl` | 172 candidates (18 reached the final release) |

**Who ran it and with which model.** Steps 0 to 8 were run by one co-author between 2026-06-29 and
2026-09-12, and the near-miss list of step 9 by the same co-author on 2026-10-02; the expansion selection
was made in the later projection-audit work (Stage 2) on 2026-10-08 and 2026-10-09. No workflow set a
`model:`; their agents were `agentType: "general-purpose"` and inherited the session's model, so the model
below is the one the session's commits name (trailer `Co-Authored-By`):

| step | model named by the commits |
|---|---|
| 0 and the projector harness, 40-question pilot (2026-06-29) | Claude Opus 4.8 |
| 1, the other 2,287 projections in chunks of about 200 (17 commits, 2026-06-30 to 07-03, some cut short by session limits), and step 2 | not recorded (those commits carry no trailer; the session that wrote the harness was Claude Opus 4.8) |
| 3 to 5, strict audit and list (2026-07-19 to 07-23) | Claude Opus 4.8 |
| 6 to 8, the four gates and the final list (2026-09-12) | Claude Opus 5 |
| 9, the near-miss list (2026-10-02, private branch `hotpotqa-climbmix`) | Claude Opus 5 |
| X, expansion selection (2026-10-08/09) | Claude Opus 5.5 |

Codex (OpenAI) did not choose the candidate batches: it took the first ten not-yet-audited questions of the
recall batch, in the batch's order, and audited them in Stage 2 (commits marked "Assisted-by: Codex"); those
audits were superseded when a Claude Opus 5.5 run re-audited all 122.

### Step 0: task construction

`stage0_build_tasks.py` loads the three test splits with `datasets` (or the raw JSONL files with `--raw`),
decrypts `question` and `answer` (base64, then XOR with sha256 of the dataset's own canary repeated with
period 32: `trqa_source.py`; base64 alone gives wrong text), and writes one task per question:
`record_id` = `<subset>__<TRQA id>` (the "stem" every later file uses), `question`, `answer`, `answer_type`
`aggregate`, `source` (the TRQA id), `subset`, `datatype`. No hints. An earlier, uncommitted pilot had run on
the still-encrypted text and was discarded; every kept projection (the 40-question pilot included) copied a
gold answer equal to the decrypted one.

### Step 1: projection (`stage1_project.js`, `PROJECT.md`)

One agent per question reads its task (question and gold answer), parses the entity-set definition, the
operator and the target attribute, then uses BM25 alone to enumerate the complete set and retrieve each
member's attribute ("issue many expanded queries; escalate hits 30 -> 100 -> 200"), computes the aggregate,
compares it with the gold value and writes `{record_id, subset, verdict, operator, target_attribute,
entity_set_description, n_entities_expected, n_entities_recovered, computed_value, gold_answer,
matches_gold, recall_complete, evidence: [{entity, attribute_value, doc_id}], rationale}`. The verdict bar is
strict: `SUPPORTED` = the complete set and every attribute recovered from the corpus **and** the computed
aggregate equal to the gold value; `PARTIAL` = incomplete recall, or close but not matching because members
are missing; `NOT` = the set or the attributes are largely absent. The real run launched chunks of about 200
question ids at a time (the remaining tasks without a projection file); the 40 pilot projections were kept.
Yield by population: wiki1 36 of 169 (21.3%); wiki2 "classic" (`wiki2__<n>_p<prop>`) 33 of 83 (39.8%);
wiki2 "quest" (`wiki2__quest_*`) 47 of 1,175 (4.0%); ecommerce 3 of 900 (0.3%, a synthetic product catalogue
the corpus does not contain).

### Step 2: answerability check (`collect_verdicts.py`)

The verdicts are tabulated per question. Only `SUPPORTED` goes on. A "loose" count (`matches_gold`
regardless of grounding: 147) was reported as an upper bound and not used. PARTIAL and NOT questions left
Stage 1 here; no near-miss list was made from them (some re-entered later through the expansion, step X).

### Steps 3 to 5: strict audit (`build_strict_inputs.py`, `stage1_strict_audit.js`, `STRICT_AUDIT.md`, `build_strict_list.py`)

The input per SUPPORTED question is the projector's reading and evidence plus the task's question. One
agent per question (batches of 6) applies "the BCP-65 standard adapted to aggregation questions": every
question constraint represented as hops (one membership hop per entity, one attribute hop per entity where
the operator needs a value), every hop grounded by the full text of a corpus document (replacement searches
allowed; temporal qualifiers non-disqualifying when entities and relations are grounded, only in-text dates
count as date evidence), the aggregate recomputed over the verified values equal to the gold answer, and an
adversarial completeness search (3 to 6 queries); `keep` = all four. 87 of 119 kept (wiki1 23 of 36,
wiki2 62 of 80, ecommerce 2 of 3). The 32 drops failed (overlapping): grounding 19, completeness 17,
aggregate 14, constraint coverage 6.

`build_strict_list.py` turns each kept audit into the strict list: a hop citing documents becomes an
evidence hop (`supporting_doc_ids`, the snippet cut to 300 characters), a hop citing none a derivation hop
(6 of 533 hops). **Manual step:** one kept question's audit file recorded its hops in another shape (no
`clue`); a person rebuilt its three hops by hand from the projector's evidence (one evidence hop per member:
a clue naming the member and its value, the member's cited document, the value; no snippet). The script
stops and names such questions; the hand-written hops go in `stage1/<set>/strict_backfill/<qid>.json`.
The same script writes the gates' inputs (hops numbered from 1).

### Step 6: gate 0, snippet measurement (`verify_snippets.py`)

A deterministic check that each strict-list snippet occurs verbatim in one of its documents. The strict
audit wrote composite snippets (fragments of several documents joined with `|`, commentary, document ids),
so most fail: with the committed script, 200 of 527 evidence snippets pass and 6 of 87 questions are fully
clean. Nothing was dropped for it; gate 1 rewrites every passing hop's snippet instead. (The private
report gives 243 of 527 for this measurement; that figure matches a failure list whose generating code was
not committed. This port reproduces the committed script's output exactly.)

### Step 7: the four gates (`stage1_final_gates.js`, `FINAL_GATES.md`)

"The DSQA final filtering pipeline adapted to aggregation questions": one agent per question per gate, in
order, a failing question skipping the later gates; filter-only except the annotator. Two adaptations:
completeness gets its own adversarial gate (DSQA's any-one-member waiver does not apply to an aggregate), and
a derivation hop that asserts a world fact (a floor, a bound, a closure claim) counts as an ungrounded
evidence hop.

1. **Extreme audit** (55 of 87). Atomize the question (every filter, the attribute, its unit, temporal
   qualifiers, source attributions; the operator and set closure exempt), map atoms to hops, re-ground every
   evidence hop from the full documents with a fresh verbatim snippet (at most 280 characters; up to 4 rescue
   searches), check derivation hops. The 32 drops all had an ungrounded hop; 4 also an uncovered atom, 2 a
   derivation hop asserting a world fact.
2. **Completeness** (53 of 55). At least 8 and up to 20 distinct searches (11 to 28 reported) for a
   qualifying member the set missed, each candidate verified on full text; `keep` = set complete or the
   aggregate unchanged (12 kept with a verified extra member that does not change the answer).
3. **Coherence** (51 of 53). Units, attribute sense, vintage, entity and scope consistent across hops; the
   aggregate recomputed equal to gold; source attribution licensing the data; the hops jointly deriving the
   answer. Both drops were edition-mismatch plus derivation-gap.
4. **Annotator** (50 of 51). Each hop read as a human annotator would: keep, fix (a better passage from the
   hop's verified documents, or a clearer clue) or drop. 116 fixes proposed, 2 drops (in one question).

**Calibration (as run):** the first batch was 10 questions chosen by hand (6 wiki1, 3 wiki2, 1 ecommerce;
the rule was not recorded). It dropped 5, every one for "no hop represents the operator": the prompt was
fixed (the operator and set closure exempted, as `FINAL_GATES.md` says now), the 5 voided extreme files
were moved to `extreme/superseded/` (manual), those 5 were rerun (4 failed again, 1 passed and survived),
and the other 77 were run. The 5 calibration questions that passed kept their pre-fix extreme files.

### Step 8: final list (`build_final.py`)

A fix is applied only if its document is one the extreme gate verified for that hop and its snippet occurs
there verbatim (fragment by fragment over the hop's verified documents for a two-document quote): 110
applied, 6 rejected, 161 hops kept as they were. Then all 275 evidence snippets verify verbatim. 50
questions (wiki2 38, wiki1 11, ecommerce 1), 2 to 10 hops each (mean 5.54); operators count 22, min 11, max 8,
average 4, sum 2, three others.

### Step 9: Stage 2 inputs (`write_stage2_inputs.py`)

The 50 survivors become `inputs/<set>.jsonl` with the gate-verified hops. The **near misses** are the 37
strict-audited questions that failed one gate (extreme 32, completeness 2, coherence 2, annotator 1), with
the hops as they stood after the strict audit, the failed gate, a rank (1 = the last gate), the gate agent's
reason, and the missed members / recomputed answer (completeness) or ungrounded hops / uncovered atoms
(extreme). Both files are in `cmass/records.py` format (`answer_type` `aggregate`; TRQA never marked hops
redundant, so there is no `redundant` field) and pass Stage 2's input check (C1).

## Step X: the TRQA expansion candidates (`expansion/`)

Stage 2 later projected more TRQA questions from **TRQA's own candidate pools** (its qrels name, per
question, the Wikipedia pages the set ranges over). These candidates had not passed Stage 1 and entered
Stage 2 without hops; their selection is ported here because it decided which questions entered Stage 2.

1. `universe.py`: every wiki1 / wiki2 test question that is not among the 87 strict-audited ones (1,342),
   with its pool (qrel documents cut to page ids), the projector's verdict and a prior-recall category;
   "trivial" questions (a four-digit answer that appears in the question) apart (20), the rest in the
   universe (1,322).
2. `pool_sweep.py`: the Wikipedia title of every pool page (Wikipedia API), one corpus search per title (top
   50, smallest pools first), present when the cleaned title occurs in a retrieved document; 678 questions
   had their whole pool present.
3. `select_and_build.py select`: the **pilot**, 30 questions sampled with seed 20261008 from the all-present
   questions with pools of at most 12 pages (470), stratified (wiki1 6, classic 6, quest by property: p105 6,
   p577 3, p407 2, p495 2, p2047 2, p7937 1, p50 1, p9714 1); the **recall batch**, 122: every other
   all-present question whose projection recalled at least half the set, matched the gold with partial
   recall, was SUPPORTED but dropped by the strict audit, or had full recall with a different value, largest
   pool first; the **trivial** batch, the 20 trivial questions. (The recall-batch and trivial rules are
   reconstructed: the real run kept the id lists, not the code that wrote them; these rules reproduce both
   lists exactly, ids and order. The pilot sampling is the original code.)
4. `select_and_build.py leads` and `build`: Wikipedia introductions of the pool pages (two sentences,
   identification only) and one record per candidate in `inputs/xp_<set>.jsonl`: no hops, the leads as
   `hints`, the pool with each page's presence rank, the projector's reading and evidence as leads.

In Stage 2 these 172 questions were projected and audited per question by agents (the private
`trqa_expansion_workflow.js` and, for the trivial ones, `trqa_fix_workflow.js`, under the guide's
"Expansion" rules: every fact an added hop, slight rewrites only, TRQA's gold kept whenever possible). In this
pipeline that corresponds to running Stage 2 on the set `xp_<set>`. 19 were accepted at human review and 18
are in the final TRQA release (2 from the pilot, 16 from the recall batch).

## Paper mapping

The paper's Stage 1 has four operations: hop/clue decomposition, grounding, answerability check and
all-hops verification; passing questions form the candidate list and near misses are kept separately.

| paper operation | TRQA as run | where it differs |
|---|---|---|
| hop/clue decomposition | none in the projector: it wrote the set definition, operator, attribute and an evidence list of {entity, value, document}. Hops were first written by the strict audit (membership and attribute hops per entity, derivation hops) and re-atomized by gate 1. | hops are not typed required / confirmatory or inclusion / exclusion anywhere in Stage 1; decomposition happens inside the verification steps |
| grounding | the projector grounded each member's value to a document id (no snippet); the strict audit read full documents and wrote snippets (mostly composites); gate 1 re-grounded every hop with a fresh verbatim snippet, re-verified by `build_final.py` | derivation hops state the operation in the clue (e.g. "max(...) = ..."), with no list of input hops; Stage 1 kept no search logs, only cited documents (67 of the 2,327 projection files list some of their queries; this port logs every search, per step, with `cm.py --stage 1 --step <step>`) |
| answerability check | `SUPPORTED` / `PARTIAL` / `NOT` (= Projectable / Partial / Not), the bar being complete recall **and** a computed aggregate equal to the gold | stricter than "the members and values support the result": the projector also had to judge its recall complete |
| all-hops verification | five agent filters in sequence: the strict audit, then gates 1 to 4, plus deterministic snippet checks | it includes adversarial set-completeness searches (the strict audit and gate 2), which the paper places in Stage 2, and hop repairs (gate 4 rewrites clues and snippets), which the paper does not describe in Stage 1 |
| near misses | the 37 strict-audited questions that failed one gate | not the 32 strict-audit drops nor the 91 PARTIAL; they carry the strict audit's (unverified, composite) snippets |
| (none) | step X, the expansion: 172 questions that had not passed Stage 1, chosen with TRQA's qrel pools, a corpus presence sweep and the projector's recall, sent to Stage 2 without hops, with Wikipedia leads as hints | bypasses Stage 1; uses the source benchmark's qrels and Wikipedia as hints, where the paper says only HotpotQA tasks carry hints; 18 of the 68 released TRQA questions came this way |

## Run it

Create a run directory **outside this repository** (it will hold decrypted TRQA content) with a
`cmass.json` (see `../../cmass/config.py`; on a shared Pyserini service use `min_gap_s` that respects its
per-token limit):

```json
{"corpus": {"name": "climbmix-400b", "backend": "rest",
            "rest": {"base_url": "https://example.org", "index": "climbmix-400b", "token_env": "PYSERINI_API_TOKEN"},
            "min_gap_s": 2.0, "slots": 1},
 "sets": {"trqa":    {"input": "inputs/trqa.jsonl", "family": "trqa", "group": "released"},
          "nm_trqa": {"input": "inputs/trqa_near_misses.jsonl", "family": "trqa", "group": "near_miss"},
          "xp_trqa": {"input": "inputs/xp_trqa.jsonl", "family": "trqa", "group": "near_miss"}}}
```

`S1` is this directory, `TOOLS` is `pipelines/cmass/tools`; run everything from the run directory. The
Workflow scripts take `{"run": "<abs run dir>", "tools": "<abs TOOLS>", "set": "trqa", "qids": [...]}`
(`qids` omitted: every question whose next step the script handles). `python3 S1/trqa_state.py summary trqa`
says where every question stands at any point.

```bash
python3 S1/stage0_build_tasks.py                       # (or --raw DIR after: python3 S1/trqa_source.py download --out DIR)
# Workflow S1/stage1_project.js      {"run", "tools", "qids": [about 200 at a time]}     step 1
python3 S1/collect_verdicts.py                         # step 2
python3 S1/build_strict_inputs.py                      # step 3
# Workflow S1/stage1_strict_audit.js {"run", "tools"}                                    step 4
python3 S1/build_strict_list.py                        # step 5 (stops for a manual backfill if needed)
python3 S1/verify_snippets.py                          # step 6, a measurement
# Workflow S1/stage1_final_gates.js  {"run", "tools", "qids": [a calibration batch first]}   step 7
python3 S1/build_final.py                              # step 8
python3 S1/write_stage2_inputs.py                      # step 9: inputs/trqa.jsonl, inputs/trqa_near_misses.jsonl
# step X (after step 9; needs the raw files with qrels)
python3 S1/trqa_source.py download --out source/trqa_raw
python3 S1/expansion/universe.py --raw source/trqa_raw
python3 S1/expansion/pool_sweep.py titles && python3 S1/expansion/pool_sweep.py sweep && python3 S1/expansion/pool_sweep.py summary
python3 S1/expansion/select_and_build.py select && python3 S1/expansion/select_and_build.py leads
python3 S1/expansion/select_and_build.py build         # inputs/xp_trqa.jsonl
# then Stage 2 (../../stage2/README.md) on trqa, nm_trqa and xp_trqa
```

The canary is read from the dataset rows (or from `$TRQA_CANARY` when set); it is never needed in a file.
The agents' corpus commands map onto this pipeline's `cm.py`: the real run's `cm.py search "<q>" [hits]
[preview]` (previews centered on the query terms) and `cm.py doc <id>` became `cm.py search --stage 1 --step
STEP --set S --qid Q "<q>" --hits N --chars C --center` and `cm.py doc --stage 1 --step STEP --set S --qid Q
ID --max 1000000`. Each independent step (`project`, `strict`, and each gate) logs to its own
`stage1/<set>/<step>/<qid>.searchlog.jsonl`, so one pass's queries are not refused as near duplicates in the
next; within a step, `cm.py` refuses a near-duplicate query unless `--force` is passed (the guides say to use
it when escalating hits). The state tool and a second attempt for an invalid output file are additions of this
port; the real workflows trusted the agent to write a well-formed file.

## Try it offline

```bash
python3 pipelines/cmass/stage1/trqa/demo/make_demo.py
```

It builds `demo/run/` (gitignored) from invented, encrypted "TRQA" rows and hand-written agent outputs in the
Oskeria world, and runs every deterministic step: 11 tasks, 7 SUPPORTED, the manual backfill, one drop at
the strict audit and at each gate, an annotator fix applied and one rejected by the guard, 2 candidates and
4 near misses, and 4 expansion candidates (2 pilot, 1 recall batch, 1 trivial), then builds Stage 2 packets
for all three sets.

## Sources in the private repository

Paths in the private repository (main branch unless noted), read-only:

- `artifacts/trqa/README.md`, `HANDOFF.md`, `FINAL_RESULTS.md`, `verdicts.jsonl`; `artifacts/trqa/tools/build_trqa_tasks.py`,
  `trqa_project_chunk.js`, `cm.py`; history on branch `trqa-run` (and `trqa-climbmix-projection`)
- `artifacts/trqa/strict_audit/audit.js`, `STRICT_AUDIT.md`, `STRICT_HANDOFF.md`, `inputs/`, `runs/`,
  `strict_verdicts.jsonl`, `strict_keep_stems.json`, `projected_questions_strict.jsonl`
- `artifacts/trqa/final_pipeline/trqa_final_pipeline.js`, `TRQA_FINAL.md`, `build_final.py`, `verify_snippets.py`,
  `inputs/`, `{extreme,completeness,coherence,annotator}/runs/`, `extreme/superseded/`, `calibration_ids.json`,
  `recheck_ids.json`, `remaining_ids.json`, `survivor_ids.json`, `snippet_verification.json`,
  `snippet_failures.json`, `fix_verification.json`, `projected_questions_trqa_final.jsonl`
- branch `hotpotqa-climbmix`: `artifacts/near_miss/build_near_miss.py`, `README.md`
- `artifacts/projection_audit/HANDOFF.md` (section 5.2), `TRQA_GOLD_GUIDE.md` ("Expansion"), `original/SOURCES.md`,
  `near_miss/original/SOURCES.md`, `tools/trqa_hf.py`, `tools/trqa_pool_sweep.py`, `tools/trqa_expansion.py`,
  `tools/trqa_expansion_workflow.js`, `tools/trqa_fix_workflow.js`, `tools/common.py`,
  `trqa_gold/expansion_status.json`, `trqa_gold/expansion_site.json`

Reconstructed (outputs kept, code not): `collect_verdicts.py`, `build_strict_inputs.py`,
`build_strict_list.py` and the recall-batch and trivial rules; each reproduces the real outputs exactly.
`expansion/universe.py`, `select_and_build.py select`, `build_final.py`, `verify_snippets.py` and the
near-miss part of `write_stage2_inputs.py` were also run offline on the real run's files and reproduce
their outputs exactly; `stage0_build_tasks.py` (raw-file path) gives the same 119 strict-audit questions and,
for all 2,327 tasks, the gold answer each projection copied (the real task files were not kept).
