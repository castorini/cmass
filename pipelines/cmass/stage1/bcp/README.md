# BrowseComp-Plus: Stage 0 task construction and Stage 1 projection, as run

This directory ports the procedure that actually produced the BrowseComp-Plus (BCP) questions that entered
Stage 2 (`pipelines/cmass/stage2/`): the 65 projected candidates and the 55 near misses. It follows the
procedure step by step, in the order it ran, with the original prompts, labels, filters and rules; where a
person decided, the step is a manual step with a decisions file (`MANUAL.md`), not automation. It holds code and
guides only: no question, answer, hop, document or per-question output.

Everything a run produces lives in one run directory (`../../cmass/run.py`): Stage 0 writes `tasks/bcp.jsonl`,
each agent writes `stage1/bcp/<step>/<qid>.json`, `bcp_build.py` writes the lists `stage1/bcp/*.jsonl`, and the
run ends with the Stage 2 inputs `inputs/bcp.jsonl` and `inputs/bcp_near_misses.jsonl` (record format of
`../../cmass/records.py`).

## Files

| file | what |
| --- | --- |
| `stage0_tasks.py` | Stage 0: one task per BCP test question, decrypted with the canary from `$BROWSECOMP_PLUS_CANARY` |
| `project.js`, `PROJECT.md` | step `project`: decomposition, grounding and answerability verdict, one agent per task |
| `scrutiny.js`, `SCRUTINY.md` | steps `scrutiny1` and `scrutiny2`: two independent re-groundings of every hop |
| `coverage.js`, `COVERAGE.md` | step `coverage`: the tool-free coverage audit |
| `external_audit.js`, `EXTERNAL_AUDIT.md` | step `external_audit`: the audit of independently produced projections |
| `repair.js`, `REPAIR.md` | step `repair`: near-miss repair of the failing hops |
| `expansion.js`, `EXPANSION.md` | steps `strict_audit` and `rejudge`: the near-miss search among unused PROJECTABLE questions |
| `MANUAL.md` | the six manual decisions and their files |
| `bcp_state.py` | what each agent step still has to do, its inputs, output checks (the workflows call it) |
| `bcp_build.py` | every deterministic step, in order |
| `bcp_common.py` | shared paths, rules (the union of the two passes) and validators |
| `demo/` | an invented corpus and `make_demo.py`, which runs every deterministic step on hand-written outputs |

## The procedure as it ran

Counts are those of the run (verified against the private repository's committed outputs). "Model" is what the
commit history records: the original workflows set no `model`, so their agents inherited the session model.

| # | step | who / what | output | count in the run |
| --- | --- | --- | --- | --- |
| 0 | task construction | `stage0_tasks.py` (was `build_all_tasks.py`) | `tasks/bcp.jsonl` | 830 tasks (test split) |
| 1 | projection | `project.js`: one agent per task (Claude Opus 4.8 session) | `project/<qid>.json` | 830 -> 326 PROJECTABLE, 107 PARTIAL, 396 NOT, 1 skip |
| 2 | verdicts, projected list | `bcp_build.py verdicts` | `verdicts.jsonl`, `projectable.jsonl` | 326 |
| 3 | scrutiny inputs | `bcp_build.py scrutiny-inputs` | `scrutiny_inputs.jsonl` | 326 full decompositions |
| 4 | scrutiny pass 1 | `scrutiny.js {pass: 1}` (Opus 4.8 session) | `scrutiny1/<qid>.json` | 326; list: 154 (superseded) |
| 5 | scrutiny pass 2 | `scrutiny.js {pass: 2}` (Opus 4.8 session) | `scrutiny2/<qid>.json` | 326; lists: 177, 115 (superseded) |
| 6 | all hops grounded | `bcp_build.py all-hops` | `all_grounded.jsonl` | 63 of 326 |
| 7 | coverage audit | `coverage.js` (Opus 4.8 session), `bcp_build.py coverage-final` | `coverage_final.jsonl` | 63 -> 58 |
| 8 | manual review | a person (`manual/review.json`), `bcp_build.py review` | `reviewed.jsonl` | 58 -> 50 |
| 9 | independent projections | another author's projections (tool and model not recorded); `external_audit.js` (Opus 4.8 session) | `external_verified.jsonl` | 65 audited -> 11; 33 more audited -> 0 |
| 10 | near-miss repair | `repair.js` (Opus 4.8 session), the operator's verification, `coverage.js` | `repaired.jsonl` | phase A 65 -> 5 claimed -> 4 verified; phase B 84 -> 1 -> 0 |
| 11 | deep audit, temporal policy | manual (`manual/repairs.json`, `manual/overrides.json`) | | the 4 removed, then restored; 1 record's hops rewritten |
| 12 | candidates | `bcp_build.py candidates` | `inputs/bcp.jsonl` | **65** = 50 + 11 + 4 |
| 13 | expansion audit | `expansion.js` (Claude, version not recorded), `bcp_build.py expansion` | `expansion_survivors.jsonl` | 261 -> 1 strict + 48 re-judged = 49 |
| 14 | manual trim | a person (`manual/expansion_trim.json`) | | 49 -> 47 |
| 15 | qrels-review cut | people, after Stage 1 (`manual/qrels_review.json`) | | 8 of the 65 |
| 16 | near misses | `bcp_build.py near-misses` | `inputs/bcp_near_misses.jsonl` | **55** = 8 + 47 |

Stage 2 therefore received 65 candidates and 55 near misses, 112 distinct questions: the 8 tier-1 near misses
are also candidates. In the private repository these are `artifacts/projection_audit/original/bcp.jsonl` and
`artifacts/projection_audit/near_miss/original/bcp.jsonl`.

### 0. Task construction

One task per question of `Tevatron/browsecomp-plus`, split `test` (830), streamed with only `query_id`, `query`,
`answer`, `gold_docs` and `evidence_docs`. Every field but `query_id` is decrypted (XOR with the SHA-256 of the
canary, repeated). A task holds the question, the gold answer and `hints`: the question's own gold and evidence
documents (BCP's web pages, deduplicated on id and text hash), which the projector reads to recover the intended
reasoning chain. They never count as ClimbMix grounding.

### 1-2. Projection and the answerability check

One agent per task (`PROJECT.md`) decomposes the question into the minimal hops (aim 4-6), grounds each on
ClimbMix with BM25 search and full-document reads, and labels the question by a uniqueness test: PROJECTABLE (a
corpus-only reasoner can uniquely derive the answer; a discriminating subset of supported hops suffices),
PARTIAL (reachable but not uniquely pinned, or a decisive step weak) or NOT (a decisive fact absent or
contradicted). Grounding here is inference-allowed: a hop counts if stated in or soundly inferable from corpus
documents. The output keeps the supported "discriminating" hops with verbatim snippets and the
"redundant or unsupported" ones; together they are the question's full decomposition.

Method mix in the run: 729 questions were projected with this prompt (293 PROJECTABLE, 89 PARTIAL, 347 NOT); 100
earlier ones with an earlier method, a grounded projection judged by a panel of three judges with the same three
labels and a majority vote, a three-way split counting as PARTIAL (33, 18, 49); 1 question has no projection
because the agent refused it on content-policy grounds three times (recorded as `AUP_SKIP`). The panel's judge
prompt is not in the repository; this port projects every task with `project.js`. The single-agent bar was about
7 points more lenient (40.2% PROJECTABLE against 33.0%).

`bcp_build.py verdicts` writes one verdict per task and the PROJECTABLE list with its grounded hops.

### 3-6. Two scrutiny passes and the all-hops-grounded rule

Each PROJECTABLE question's full decomposition goes to two fresh agents that see only the question, the answer
and the hop clues (`SCRUTINY.md`). Pass 1 judges every hop's support (a specific ClimbMix document states its
fact, verbatim snippet, not topical, not outside knowledge) and necessity. Pass 2 judges support, necessity and
comprehensiveness (every question constraint covered by a necessary hop, and an adversarial search for a rival
answer). The passes were run as they were developed, so three intermediate lists exist; none fed the candidates:

- 154: pass 1, redundant hops trimmed, every necessary hop supported;
- 177: pass 2, every necessary hop supported and comprehensive;
- 115: the 154 minus the 39 that pass 2 judged not comprehensive.

The rule that produced the candidates (`all-hops`) drops nothing: hop *i* of the original decomposition is
grounded when either pass found it supported (hops matched by position; documents of the supporting passes;
redundant only when both passes judged it unnecessary), and a question survives only when every hop is grounded:
**63 of 326**. Pass 1 alone would give 53, both passes agreeing 44. The agents' own `keep` flags were never used.

### 7. Coverage audit

One agent per question, with the question and its hop list only, no tools (`COVERAGE.md`), lists every content
phrase of the question with no hop, a specific number, count or interval included ("as of" timestamps ignored).
Questions with an uncovered phrase are dropped: **63 -> 58**. An earlier version that also judged literal
grounding from the recorded snippets (it cut 63 to 34) was dropped as over-strict, for instance on "as of"
timestamps.

### 8. Manual review

A person removed 8 of the 58 by hand (**58 -> 50**); no criteria were recorded (`MANUAL.md`).

### 9. Independent projections

Another author projected BCP questions with a separate pipeline; its tool and model are not recorded. Of those
projections with status success (131), 98 were PROJECTABLE here (33 already among the 50, 65 not) and 33 were
PARTIAL or NOT here. One agent per projection (`EXTERNAL_AUDIT.md`) judged whether its hops cover the question
and chain to the answer, and fetched every cited document to confirm it supports its hop. Of the 65, **11** passed
and joined the candidates with their own hop lists and cited documents (50 -> 61); of the 33, none passed (a check that
our answerability check had not wrongly rejected them). Two of the 8 manually removed questions and one of the 5
coverage drops came back this way.

### 10-11. Near-miss repair, deep audit and the temporal-qualifier policy

The pools, from the union of the two passes and excluding the 61 and the 8 manual removals: 61 questions one hop
short and 84 two hops short; plus 23 independent projections with an intact chain and one or two ungrounded hops,
4 of them outside our pools. One agent per question searched for the failing hops only (`REPAIR.md`). Phase A (61
+ 4): 5 claimed repairs; the operator's check of the fixing documents rejected 1; the 4 passed the coverage audit
and were added (61 -> 65). Phase B (84): 1 claimed repair, coverage passed, not merged.

A separate deep audit of 21 candidates (at least 250 full documents retrieved and screened per question, every
atom a hop, no single document revealing subject and answer; tool and model not recorded) rejected all 4 repairs, which were
removed (65 -> 61), and rewrote one kept question's six compound hops as eight explicit ones. A reconsideration
kept the 4 rejected under that standard. Then the release policy was relaxed for temporal qualifiers (an exact
temporal qualifier may be inferred across documents or be non-disqualifying when the entities and relations are
grounded), and the 4 were restored with corrected evidence and their caveats written into the hop text
(61 -> 65). The same deep audit rejected the phase-B repair. Net: **65 = 50 + 11 + 4**.

### 13-16. Near misses

Tier 2 came from a later pass over every PROJECTABLE question outside the 65 (**261**; by union-ungrounded hops:
0: 10, 1: 57, 2: 84, 3+: 110). A strict audit at the human reviewers' verbatim standard with a rescue search and a
coverage check qualified 1 (coverage held for 226; the rescue search grounded 21 hops in 20 questions, never every
failing hop of a question). The 777 strictly failing hops of the other 260 were re-judged at the standard the
reviewers applied, core entity and relation grounded with a secondary qualifier waived, coverage gaps still
failing: 48 more survived, 177 failed on a missing core fact, 35 on coverage. The 49 survivors (314 hops, 98 on a
waived qualifier) were trimmed by hand to **47** (299 hops, 94 on a waived qualifier). The prompts of these two
steps were not committed; `EXPANSION.md` states their documented standard and output.

Tier 1 are the **8** candidates that a later manual review of the 65 questions' qrels (the earlier BCP-only qrels
workflow, `../../../bcp_climbmix/qrels/`) judged to have a required hop partial or unsupported (65 -> 57).

Side analyses that did not change membership: piika (GPT-5.5) answered 29.2% of all 830 on its own retrieval,
47.2% of the 326, 60.3% of the 63, 63.8% of the 58 and 66.2% of the 65, and, given our documents, 156 of the 172
PROJECTABLE questions it had missed; the tier-2 records carried its per-question correctness, which this port does
not produce. Later audits of the 65 (a deep exam of 60, and a blind run of another dataset's filtering pipeline)
came after the inputs were frozen for Stage 2.

## The paper's Stage 1, mapped

| paper operation | what ran | where it differs |
| --- | --- | --- |
| task construction (Stage 0) | step 0 | BCP tasks carry the question's own gold and evidence documents as hints, and the projector is told to use them; the paper says only HotpotQA tasks include source material |
| hop/clue decomposition | step 1, the projector's discriminating plus redundant-or-unsupported hops; rewritten by hand for one candidate (step 11) | no required/confirmatory or inclusion/exclusion types: Stage 1 marks hops discriminating or redundant, and the candidates carry only a `redundant` flag; no computational hops |
| grounding | step 1 (inference allowed) and steps 4-6 (a document must state the fact; union of two passes) | grounding is split over the projector and two independent passes; the candidate records carry document ids, not snippets |
| answerability check | step 1's verdict | 100 of 830 labels came from an earlier three-judge panel; one question has no label (refusal) |
| all-hops verification | steps 6-8, plus 9-11 for 15 of the 65 | not one check: a union of two passes, a tool-free coverage audit, and a manual review with no recorded criteria; no step of the rule that ran checks that the hops jointly support the answer (pass 2's comprehensiveness fed only a superseded list); 11 candidates were projected by another author with another tool and passed a different audit; 4 are repairs accepted under a relaxed temporal-qualifier policy after a strict audit rejected them |
| near misses retained for audit | steps 13-16 | built later, not by the Stage 1 filters: 8 are candidates cut by a post-Stage-1 qrels review (audited twice in Stage 2), 47 come from a strict audit and a lenient re-judging of the unused PROJECTABLE questions, trimmed by hand |

The paper speaks of "the agent"; Stage 1 used several agents per question (projector, two scrutinizers, a coverage
auditor, and for some questions an independent-projection auditor, a repair agent or two expansion auditors).

## Relation to `pipelines/bcp_climbmix/`

That directory is an early, partial publication of the same line:

- `stage0_task_construction.py` is `build_all_tasks.py` unchanged (it imports the private repository's
  decryption module and writes to a hard-coded path); `stage0_tasks.py` here reads the canary from the
  environment and writes `tasks/<set>.jsonl`.
- `stage1_hop_clue_decomposition_and_grounding.js` is `project_chunk.js` with renamed metadata ("STEP" became
  "ACTION"): the same prompt as `PROJECT.md`. Its two `stage1_answerability_check_*.py` scripts are
  `build_verdicts.py` (with the skipped question hard-coded) and `build_projected_list.py`, and
  `stage1_all_hops_verification_build_inputs.py` is `build_scrutiny_inputs.py`: steps 2-3 here.
- `stage1_all_hops_verification.js` and its `_build_output.py` did **not** run. They are a later single-pass
  reconstruction that merges the scrutiny grounding rule, the coverage audit's number rule and the relaxed
  temporal rule into one agent per question. What ran was steps 4-11 here. Its README's account of the 65 (54 from
  our projection plus 11 "evaluated by the same All-hops verification operation") omits the manual review, the
  different audit of the 11 and the 4 repairs.

Steps 4-16 had no public code before this port.

## Running it

Create a run directory outside this repository with a `cmass.json` naming the corpus and the two sets:

```json
{"corpus": {"name": "climbmix-400b", "backend": "rest", "min_gap_s": 2.0, "slots": 1,
            "rest": {"base_url": "https://<pyserini-server>", "index": "climbmix-400b", "token_env": "PYSERINI_API_TOKEN"}},
 "sets": {"bcp":    {"input": "inputs/bcp.jsonl", "family": "bcp", "group": "released"},
          "nm_bcp": {"input": "inputs/bcp_near_misses.jsonl", "family": "bcp", "group": "near_miss"}}}
```

`STAGE1` is this directory and `TOOLS` is `pipelines/cmass/tools`. Workflow args are `{"run": RUN, "tools":
TOOLS, "set": "bcp"}` plus what is shown; `qids` restricts a workflow to some questions, and without it every
question whose output is missing or invalid is taken. Each workflow asks `bcp_state.py` before and after every
agent, so it resumes from disk. `python3 STAGE1/bcp_state.py --run RUN bcp --summary` shows every step's
progress.

```bash
export BROWSECOMP_PLUS_CANARY='<the canary printed on the dataset card>'
python3 STAGE1/stage0_tasks.py --run RUN --set bcp                      # 0
# Workflow project.js                                                   # 1 (refusals: manual/skips.json)
python3 STAGE1/bcp_build.py --run RUN verdicts                          # 2
python3 STAGE1/bcp_build.py --run RUN scrutiny-inputs                   # 3
# Workflow scrutiny.js {"pass": 1}, then {"pass": 2}                    # 4-5
python3 STAGE1/bcp_build.py --run RUN all-hops                          # 6
python3 STAGE1/bcp_build.py --run RUN coverage-bundles
# Workflow coverage.js                                                  # 7
python3 STAGE1/bcp_build.py --run RUN coverage-final
# write stage1/bcp/manual/review.json                                   # 8 (manual)
python3 STAGE1/bcp_build.py --run RUN review
# copy independent projections to stage1/bcp/external/<qid>.json        # 9
python3 STAGE1/bcp_build.py --run RUN external
# Workflow external_audit.js
python3 STAGE1/bcp_build.py --run RUN external-final
python3 STAGE1/bcp_build.py --run RUN repair-pool                       # 10
# Workflow repair.js {"phase": "A"}  (and {"phase": "B"})
python3 STAGE1/bcp_build.py --run RUN repaired
# write manual/repairs.json ("verified")                                # 10 (manual)
python3 STAGE1/bcp_build.py --run RUN coverage-bundles                  # adds the verified repairs
# Workflow coverage.js
# write manual/repairs.json ("decision") and manual/overrides.json      # 11 (manual)
python3 STAGE1/bcp_build.py --run RUN candidates                        # 12: inputs/bcp.jsonl
python3 STAGE1/bcp_build.py --run RUN expansion-pool                    # 13
# Workflow expansion.js
python3 STAGE1/bcp_build.py --run RUN expansion
# write manual/expansion_trim.json and manual/qrels_review.json         # 14-15 (manual)
python3 STAGE1/bcp_build.py --run RUN near-misses                       # 16: inputs/bcp_near_misses.jsonl
python3 STAGE1/bcp_build.py --run RUN counts
```

Then Stage 2 (`../../stage2/README.md`) runs on the sets `bcp` and `nm_bcp`. In the run, steps 13-16 came weeks
after the candidates were fixed, and the qrels review (15) after the candidates' qrels were built; a rerun can
take them in this order or wait as the run did.

### The corpus CLI

The agents use `pipelines/cmass/tools/cm.py` with `--stage 1 --step <step>`: each step that searches (`project`,
`scrutiny1`, `scrutiny2`, `external_audit`, `repair`, `strict_audit`, `rejudge`) logs to its own
`stage1/bcp/<step>/<qid>.searchlog.jsonl`. The original agents kept no search log at all, so no agent saw another
step's queries; a log per step keeps that, and in particular keeps the two scrutiny passes independent. The
coverage step searches nothing. The original agents used their own client, `cm.py search "<query>" [hits]
[preview]` and `cm.py doc <docid>`; the guides map it as follows:

| original | here | note |
| --- | --- | --- |
| `search "<q>" 30 600`, previews centred on the densest window of query terms | `search --stage 1 --step STEP --set S --qid Q "<q>" --hits 30 --chars 600 --center` | the default is 10 hits, so the guides pass `--hits` |
| the same query rerun deeper (30 -> 100 -> 200 hits) | `--force` | `search` refuses a query with token overlap 0.6 or more with one already in the step's log for the question |
| `doc <docid>` printed the full text | `doc --stage 1 --step STEP ... --max 200000` | the default prints the first 6,000 characters |

### Differences between this port and the run

- Agents read their inputs from files `bcp_state.py --input` writes, and every output is validated; the original
  workflows took hard-coded paths and a `QIDS` constant and resumed by file existence.
- Question and answer texts come from the Stage 0 task; the original copied them from the projector's output.
- The coverage agent reads its data from one file instead of the prompt, and still runs no search.
- Repair outputs name failing hops by index (the original matched them by clue text).
- The coverage bundle's hop clue is pass 2's wording, as in the original bundles (386 of 388 hops); the builder
  that made them was not committed, and its documents were a subset of the union, not the union.
- A claimed repair's record (`repaired.jsonl`) gives each fixed hop the documents of the fix and every other hop
  its union documents; the original builder was not committed (it agreed on every fixed hop and on 12 of the 21
  other hops), and the 4 accepted repairs were then edited by hand in any case (`manual/overrides.json`).
- The expansion prompts (`EXPANSION.md`) are reconstructed from their documented standard.
- The three-judge panel used for 100 early questions is not ported.

## Try it offline

```bash
python3 pipelines/cmass/stage1/bcp/demo/make_demo.py
```

It builds `demo/run/` (gitignored) over the invented documents in `demo/corpus.json` (the island nation of
Oskeria, ids `demo_bcp_NNN`, served by `pipelines/cmass/demo/demo_cm.py`), runs Stage 0 on an encrypted source
file with an invented key, every `bcp_build.py` step and every `bcp_state.py` check on hand-written agent outputs
and manual decisions, and ends with `inputs/bcp.jsonl` and `inputs/bcp_near_misses.jsonl`, for which
`tools/packets.py` writes Stage 2 packets.

## Ported from (private repository)

On `main` under `artifacts/bcp_stage1/` unless noted:

- `REPRODUCE.md`, `merged/README.md`, `q_all/tools/build_all_tasks.py`, `q_all/tools/project_chunk.js`,
  `q_all/tools/build_verdicts.py`, `q_all/tools/build_projected_list.py`, `q_all/tools/cm.py`
- `stage2/build_scrutiny_inputs.py`, `stage2/scrutinize_hops.js`, `stage2/build_all_hops_list.py`,
  `stage2/scrutinize_hops_v2.js`, `stage2/build_all_hops_list_v2.py`, `stage2/build_comprehensive_final.py`,
  `stage2/build_coverage_final.py`, `stage2/audit_coverage.js`, `stage2/audit_coverage_result.json` (format),
  `stage2/audit_inputs/` (format), `stage2/methodology.tex`, `side_lines/bcp-stage2-all-hops-supported/stage2/README.md`
- `stage2/audit_sahel.js`, `stage2/audit_sahel33.js`, `stage2/sahel_audit/SUMMARY.md`, `stage2/sahel_audit/inputs/` (format)
- `stage2/repair_nearmiss.js`, `stage2/coverage_repaired.js`, `stage2/coverage_538.js`,
  `stage2/nearmiss_repair/SUMMARY.md`, `stage2/nearmiss_repair/inputs/` (format),
  `stage2/near_miss_recovery/ROUND3_DEEP200.md`, `stage2/near_miss_recovery/ROUND4_RECONSIDERATION.md`,
  `stage2/near_miss_recovery/deep_retrieve.py`
- `stage3/EXPANSION_326.md`, `stage3/VERIFIED53.md`, `stage3/expansion_candidates49.jsonl` (format), `stage3/HANDOFF.md`
- branch `bcp-stage2-all-hops-supported`: the history of `stage2/` (`build_all_grounded.py`, `audit_hops.js`,
  `audit_hops3.js`, the manual review of the 58, the repairs and their restoration)
- branch `bcp-stage3-qrels`: the history of `stage3/` (the 49 and the trim to 47, the human-verified 57)
- branch `hotpotqa-climbmix`: `artifacts/near_miss/build_near_miss.py`
- `artifacts/projection_audit/original/SOURCES.md`, `artifacts/projection_audit/near_miss/original/NEAR_MISS_README.md`,
  `artifacts/projection_audit/near_miss/original/SOURCES.md`
- branches `bcp-stage1-analysis` (`artifacts/bcp_stage1/REPRODUCE.md`, `q_all/RESUME.md`, `q_all/FINAL_RESULTS.md`)
  and `bcp-stage1-full-backup` (`artifacts/bcp_stage1/q100/inference_audit/`, `q774_803_grounded/`): the method mix
- not ported, later than the Stage 2 inputs: `stage2/audit65/`, `latest_pipeline/bcp_latest_pipeline.js`, `stage3/FINAL47.md`
