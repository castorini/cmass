# DeepSearchQA: Stage 0 task construction and Stage 1 projection, as run

This directory is the DeepSearchQA (DSQA) part of the CMASS pipeline's Stage 0 (task construction) and Stage 1
(projection), ported from the private repository where it was run, with no data. It ends by writing the Stage 2
inputs (`inputs/dsqa.jsonl` and `inputs/dsqa_near_misses.jsonl`, plus the later expansion set
`inputs/dsqa_expansion.jsonl`) that [`../../stage2/`](../../stage2/) audits.

It records the procedure **that was actually run**: the same steps in the same order, with the same criteria,
labels, filters and models. Several steps were added during the run, one after another, as each review round
found failures the previous one could not see. Manual steps are documented as manual steps.

DeepSearchQA ([`google/deepsearchqa`](https://huggingface.co/datasets/google/deepsearchqa), Apache-2.0) has 900
prompts, each a "causal chain" whose constraints must be satisfied in order, in 17 categories. Its answers are a
**Single Answer** (316) or a **Set Answer** (584, an exhaustive list given as one string). Stage 1 released a set
answer on **one grounded member** (the "any-one-member rule"); set completeness was first required in Stage 2.

## The funnel

| step | what | questions | Single / Set |
|---|---|--:|--:|
| 0 | tasks (one per DSQA row) | 900 | 316 / 584 |
| 1 | projection (agent): SUPPORTED / PARTIAL / NOT | 296 / 147 / 457 | 57 / 239 SUPPORTED |
| 2 | consistency gate (deterministic) | 258 | 55 / 203 |
| 3 | strict audit (agent) | 170 | 26 / 144 |
| 4 | extreme-strictness audit (agent), required hops only | 84 | 13 / 71 |
| 5 | evidence presentation: snippet polish, labels, consolidation (agents and checks) | 84 | |
| 6 | coherence audit (agent) | 70 | 12 / 58 |
| 7 | annotator pass with verified repairs (agent, then a check) | 66 | 12 / 54 |
| 8-10 | flags (manual), adjudication (agent), table-quote repair (agent, then a manual decision) | 64 | 12 / 52 |
| 11 | final list, every snippet re-verified | **64** (268 hops) | 12 / 52 |
| 12 | near misses: the extreme audit's keeps that did not reach the final list | **20** | 1 / 19 |
| 14 | expansion (during Stage 2): candidates the funnel dropped | **57** (15 sets, 42 singles) | |

Details per step are below. Every deterministic command writes its own counts to `stage1/<set>/counts.json`.

## Who ran what

The DSQA projection was run by one co-author in Claude Code sessions (2026-07-23 to 2026-08-27, near-miss list
2026-10-02); the expansion selection by another author during the Stage 2 audit (2026-10-09). No workflow
except the extreme audit set a model, so the other agent steps ran on the session's model. Where this table
names a session model, it comes from the co-author lines of the commits that launched and checkpointed the step.

| step | original workflow or script | model |
|---|---|---|
| 1 projection | `tools/project_dsqa.js` (batches of 24) | not set; session model Claude Opus 4.8 (commit co-author lines, 2026-07-23 to 07-26) |
| 2 gate | `tools/build_dsqa_final.py` | deterministic |
| 3 strict audit | `tools/audit_dsqa.js` (batches of 16), `tools/build_audit_inputs.py`, `tools/build_dsqa_audited.py` | not set; the launch commit names Claude Opus 4.8, the two later checkpoints only "Claude" (2026-07-26 to 07-27) |
| 4 extreme audit | `extreme_audit/extreme_audit.js` (`model: "fable"`), `extreme_audit/build_extreme_final.py` | **Claude Fable 5 for the first 60 of 170** (including a pilot of 6); after Fable's usage limit the pin was removed (commit `d4da3f05`) and **Claude Opus 5 judged the other 110** with the same prompt (`EXTREME_MODEL` in `stage1.js`) |
| 4 cross-check | `extreme_audit/xcheck_audit.js` | Claude Opus 5 (no pin) re-judged 14 of Fable's 60 |
| 5-10 | `annotation/polish_snippets.js`, `annotation/consolidate.js`, `coherence/coherence_audit.js`, `annotator_pass/annotator_pass.js`, `adjudication/` (two workflows) | not set; session model Claude Opus 5 (2026-08-27) |
| 5-11 checks | `annotation/verify_snippets.py`, `annotation/build_doc_labels.py`, `annotation/verify_consolidated.py`, `annotator_pass/apply_fixes.py`, `verify_final.py` | deterministic |
| 12 near misses | `artifacts/near_miss/build_near_miss.py` (branch `hotpotqa-climbmix`) | deterministic (session model Claude Opus 5) |
| 14 expansion | `artifacts/projection_audit/tools/dsqa_presence_sweep.py`, `tools/dsqa_expansion.py` | deterministic, plus the operator's pilot pick (session model Claude Opus 5.5) |

## The procedure, step by step

Paths are inside the run directory; `S` is `stage1/<set>/` (set `dsqa` by default). Agent steps run through the
Workflow [`stage1.js`](stage1.js) with `{"step": ...}`: one agent per question reads its packet
(`S/packets/<step>/<qid>.json`, written by [`dsqa_state.py`](dsqa_state.py) with exactly the fields the original
agents read) and its guide ([`guides/`](guides/), the original prompt content with the examples replaced by
invented ones), and writes one output `S/<step>/<qid>.json`, which `dsqa_state.py` validates. Deterministic steps
are [`dsqa_build.py`](dsqa_build.py) commands.

**0. Task construction** ([`stage0_tasks.py`](stage0_tasks.py)). The 900 rows of `DSQA-full.csv` (the dataset's
one split, `eval`), in file order, become `tasks/<set>.jsonl` with record_id `dsqa_000`..`dsqa_899` (the row
number), the problem, the gold answer string as DSQA writes it, DSQA's answer-type label and the category. No
filtering, no hints. The run used the CSV downloaded on 2026-07-23 (its SHA-256 is in the script). No task script
was committed; the port reproduces all 900 committed task files exactly.

**1. Projection** (agent, [`guides/PROJECT.md`](guides/PROJECT.md)). One agent per prompt decomposes the causal
chain into hops, one per information-bearing constraint; grounds each hop by deep retrieval (3-8 query variants,
30-200 hits, documents read in full; a hop is grounded only if a document's own text states it); derives the answer
and labels the question `SUPPORTED` (every constraint hop grounded and the answer matches), `PARTIAL` (answer
reachable, some constraint hop ungrounded) or `NOT`. Single Answer: the derived answer must equal the gold. Set
Answer: **at least one gold member** must be derivable and grounded; completeness is not required (the original
workflow required the full set for its first 22 minutes; it was relaxed before any projection was committed).
Temporal qualifiers are non-disqualifying when entity and relation are grounded; only in-text dates count.
900 judged: 296 SUPPORTED, 147 PARTIAL, 457 NOT.

**2. Consistency gate** (`dsqa_build.py gate`). The agent's verdict is not trusted on its own: a SUPPORTED
projection is kept only if it lists at least one hop, `matches_gold` is true, and every listed hop is grounded with
a document id and a snippet. 296 → **258**; the 38 dropped list a hop they marked ungrounded (34) or a grounded hop
without a document id (4). Output `S/released.jsonl` (and `S/supported.jsonl`, the 296).

**3. Strict audit** (agent, [`guides/STRICT_AUDIT.md`](guides/STRICT_AUDIT.md); `dsqa_build.py audited`). One
auditor per released question, told to refute: coverage (every information-bearing constraint, numeric qualifier
and bridging step is a hop), full-document re-grounding (replacement documents allowed), answer check (a set's
claimed member must be in the gold list; completeness waived) and an adversarial check of the claimed member
against every constraint. A named source is waivable only when it is not discriminating. `keep` is the AND of the
four checks. 258 → **170**; failed checks (overlapping): re-grounding 84, member refuted 78, uncovered constraint
69, answer mismatch 20. The kept records carry the auditor's hops. (An early mis-scoped launch also audited 35
gate-rejected questions; the builder ignores audits outside the released list.)

**4. Extreme-strictness audit** (agent, [`guides/EXTREME_AUDIT.md`](guides/EXTREME_AUDIT.md);
`dsqa_build.py extreme`). Every meaningful phrase and word of the question is an atom that must map to a hop
(only function words and bare "as of <year>" stamps are exempt; a source attribution is an atom), with a
calibration list of failures human annotators had found in an earlier projected benchmark; every hop re-grounded
from scratch (at most 4 rescue searches); hops classified required or confirmatory. Filter only. 170 → **84**
(86 dropped: an ungrounded hop 71, an uncovered atom 35, overlapping; most uncovered atoms were named sources the
corpus never attributes). The record keeps **required hops only** (360; 28 confirmatory hops verified and omitted),
with the auditor's documents and fresh snippets and the audited list's clue wording.
*Cross-check* (`xcheck`, manual sample): 14 of the 60 Fable-judged questions, 7 keeps and 7 drops "randomly
sampled" (no seed recorded), were re-judged with the same prompt on the second model: verdicts agreed 14/14, both
gate flags 12/14 (`dsqa_build.py xcheck`). It changed no verdict.

**5. Evidence presentation.** Built for a human-annotation workbook, but its output is the evidence every later
step reads, so it is part of the path:
- *5a Snippet polish* (agent, [`guides/POLISH.md`](guides/POLISH.md); `dsqa_build.py polish-verify`): per hop, the
  one clearest self-contained passage (at most about 300 characters) from the hop's documents. A polished snippet is
  used only if it occurs verbatim in its document (whitespace normalised; fragments joined by ` ... `); one off only
  by the trailing punctuation an agent changed is repaired; otherwise the hop keeps the extreme audit's snippet.
  360 hops: 349 verbatim, 11 fall back. The check does not require the document to be one the extreme audit
  verified for the hop (one hop in the run moved to such a document and kept it to the end).
- *5b Labels* (`dsqa_build.py labels`): each cited document's first substantive line (727 documents), used as its
  title and, through the first six such lines, to detect two corpus shards of one page.
- *5c Consolidation* (`dsqa_build.py consolidate-targets`, agent [`guides/CONSOLIDATE.md`](guides/CONSOLIDATE.md),
  `dsqa_build.py consolidate-verify`): for questions where a hop's verified documents include another hop's current
  document (58) or whose hops rest on two shards of one page (4), 59 in all, an agent re-draws snippets from a
  shared document when the passage is at least as clear. A moved hop must cite a document the extreme audit verified
  for it and quote it verbatim, else it reverts. 264 hops: 56 moved, 202 kept, 6 reverted (5 not verbatim, 1 not a
  verified document); documents shown 303 → 255.
- *5d Evidence* (`dsqa_build.py evidence`): per hop the consolidated evidence where it verified, else the polished,
  else the extreme audit's, with the document's label and every document the extreme audit verified for the hop:
  `S/evidence.jsonl`, the input of steps 6 and 7.

**6. Coherence audit** (agent, [`guides/COHERENCE.md`](guides/COHERENCE.md); `dsqa_build.py coherence`). Do the
individually grounded hops, read together, justify the answer: a named source must license the very data the
other hops read; hops must agree on edition, reference year, entity, unit and scope; the hops must reach the
answer without an inferred step. `keep` is the AND of the three. 84 → **70**. Patterns among the 14 dropped (per
question, overlapping): attribution-not-for-data 11, derivation-gap 8, year-mismatch 7, edition-mismatch 4,
unit-mismatch 4, scope-mismatch 2, internal-figure-conflict 1, conflicting-source-frame 1.

**7. Annotator pass** (agent, [`guides/ANNOTATOR.md`](guides/ANNOTATOR.md); `dsqa_build.py apply-fixes`). Each
question read as a careful human annotator: per hop keep, fix (a better verbatim passage from the hop's verified
documents, or clearer clue wording) or drop; any drop kills the question; issues recorded. A fix is applied only
if its document is one the extreme audit verified for the hop and its snippet is verbatim there; a reworded clue
rides only with verified evidence. 70 → **66** (5 hops dropped in 4 questions); 128 fixes proposed, among the 66
survivors 113 applied and 10 rejected. Output `S/applied.json`.

**8. Verbatim check and issues** (`dsqa_build.py verbatim`, `dsqa_build.py issues`). The hops of the 66 whose snippet
is not verbatim in its document (as run: 3 hops of one question, reformatted table quotes), and every issue the
annotator agents recorded (`S/issues.json`).

**9. Flags (manual) and adjudication.** *Manual:* the operator read the annotator issues of the 66 and picked the
serious ones, per the adjudication workflow's description: fabricated or unverifiable evidence, a gold answer the
corpus contradicts, a premise the corpus contradicts, evidence that would mislead a reader. As run, 16 questions
with 1-3 issues each (the selection also covered the one question with non-verbatim snippets; `dsqa_state.py
adjudicate` warns when such a question is not flagged). Write them to `S/flags.json` as
`{"<qid>": [<issue objects copied from the annotator output>]}`. Then one adjudicator per flagged question
([`guides/ADJUDICATE.md`](guides/ADJUDICATE.md)) decides `drop` (any fatal flag) or `keep_with_note` with a note for
annotators. 16 → 1 dropped (the corpus contradicts the gold answer), 15 kept with a note.

**10. Table-quote repair and the operator's decision.** For a question kept with a note whose snippets are not
verbatim, one agent ([`guides/TABLE_REPAIR.md`](guides/TABLE_REPAIR.md); the original was a one-question workflow)
replaces each reformatted table quote with a verbatim run of the document, or reports that none can carry the hop.
As run: two hops were re-quoted verbatim, as full tables of about 2,000 and 600 characters, and one hop's fact,
derived from two tables, is stated by no document. *Manual:* the operator dropped the question (no verbatim
evidence for a hop, and quotes too long to annotate). Write `S/table_decisions.json` as
`{"<qid>": {"decision": "drop" | "keep", "reason": "..."}}`; on `keep`, the repaired snippets replace the old ones.

**11. Final list** (`dsqa_build.py final`, `dsqa_build.py verify-final`). The 66 minus the adjudication's and the
operator's drops: **64** questions, 268 hops (the shown document first, then every other document the extreme
audit verified for the hop), 14 carrying their adjudication note as `annotator_note`. Every snippet, whatever step
produced it, is re-verified against the document text: 268 verbatim, 0 failures (the annotator pass had caught one
snippet, inherited from the extreme audit, that occurs in no document).

**12. Near misses** (`dsqa_build.py near-misses`). The questions of the extreme list (84) that are not final, each
with the gate that dropped it and how late (`stage_rank` 1 = the last gate): **20** = coherence 14, annotator 4,
adjudication 2 (the adjudication's drop and the table-repair drop: as run, any adjudicated question that is not
final counts as an adjudication drop, with the adjudicator's rationale as its reason). Their hops are the extreme
list's. Questions dropped earlier (by the gate, the strict or the extreme audit) are not near misses.

**13. Stage 2 inputs** (`dsqa_build.py inputs`). `inputs/<set>.jsonl` (64) and `inputs/<set>_near_misses.jsonl`
(20) in the record format of [`../../cmass/records.py`](../../cmass/records.py): `record_id`, `question`, `answer`
(DSQA's string; the auditor splits a set), `answer_type` (`single` or `set`), hops `{clue, doc_id,
supporting_doc_ids, snippet, kind: "required", redundant: false}`, `source`, and the dataset fields
`problem_category`, `dsqa_answer_type`, `derived_answer` (the one grounded member), `annotator_note`, and for near
misses `failed_gate`, `stage_rank`, `reason`, `failure_patterns`. Configure them as sets `dsqa` and `nm_dsqa`
(family `dsqa`; the command prints the `cmass.json` entries).

**14. Expansion candidate selection** ([`dsqa_expansion.py`](dsqa_expansion.py); run during the Stage 2 audit,
2026-10-09, after Stage 2 replaced the any-one-member rule by "every member grounded and the set complete").
- `candidates`: DSQA tasks outside the 64 + 20 whose projection got far enough. **Set answers**: reached at least
  PARTIAL and split into 2-5 members: 182 (passed the strict audit 56, released by the gate 37, projector SUPPORTED
  28, PARTIAL 61). **Single answers**: reached at least the released list: 42 (13 and 29).
- `sweep` (corpus searches): one search per set member, the cleaned member name plus four question keywords, 50
  hits; present = the name occurs in one of the 50 documents, in context = such a document also has a keyword;
  bare values are not searched. 586 members, 552 searched. Use the REST backend (it returns each hit's text; the CLI
  backend fetches 50 documents per search).
- `summary`: 146 of 182 have every member present. `RANKED.md` lists them grouped by why the funnel dropped them:
  for strict-audit survivors, how the extreme audit failed them (uncovered source clause only, other clause only,
  ungrounded hop and uncovered clause, ungrounded hop), else the stage reached.
- `pilot`: the set-answer pilot, picked by the operator from `RANKED.md`; the rule here (walk the groups in order,
  up to 4 per group, 15 in all) reproduces that pick exactly. The set expansion was not scaled beyond the pilot.
- `records`: `inputs/<set>_expansion.jsonl`, 57 records (the 15 pilot sets and all 42 single answers, which ran as a
  pilot of 12 then the other 30) **without hops**, with the projector's hops, the drop reason and the presence
  sweep as `expansion_leads` (leads, not evidence). Configure as set `xp_dsqa`. In Stage 2 the audit kept 3 sets
  and 27 single answers, and 24 entered DSQA's final release.

## Commands, in order

```bash
RUN=/abs/path/to/run          # outside this repository; cmass.json with the corpus backend and "sets": {"dsqa": {"input": "inputs/dsqa.jsonl", "family": "dsqa"}}
S1=/abs/path/to/cmass/pipelines/cmass/stage1/dsqa
cd $RUN
python3 $S1/stage0_tasks.py                                 # 0  tasks/dsqa.jsonl
# Workflow $S1/stage1.js {"run": "$RUN", "tools": "<abs>/pipelines/cmass/tools", "step": "project", "set": "dsqa"}   # 1
python3 $S1/dsqa_build.py gate                               # 2
# Workflow stage1.js {... "step": "strict"}                  # 3
python3 $S1/dsqa_build.py audited
# Workflow stage1.js {... "step": "extreme"}                 # 4  (EXTREME_MODEL; {"model": ""} = session model)
python3 $S1/dsqa_build.py extreme
# optional: Workflow stage1.js {... "step": "xcheck", "qids": [<hand-picked sample>], "model": "<the other model>"}; dsqa_build.py xcheck
# Workflow stage1.js {... "step": "polish"}                  # 5a
python3 $S1/dsqa_build.py polish-verify
python3 $S1/dsqa_build.py labels                             # 5b
python3 $S1/dsqa_build.py consolidate-targets                # 5c
# Workflow stage1.js {... "step": "consolidate"}
python3 $S1/dsqa_build.py consolidate-verify
python3 $S1/dsqa_build.py evidence                           # 5d
# Workflow stage1.js {... "step": "coherence"}               # 6
python3 $S1/dsqa_build.py coherence
# Workflow stage1.js {... "step": "annotator"}               # 7
python3 $S1/dsqa_build.py apply-fixes
python3 $S1/dsqa_build.py verbatim                           # 8
python3 $S1/dsqa_build.py issues
# MANUAL: write stage1/dsqa/flags.json from stage1/dsqa/issues.json      # 9
# Workflow stage1.js {... "step": "adjudicate"}
# Workflow stage1.js {... "step": "table_repair"}            # 10
# MANUAL: write stage1/dsqa/table_decisions.json
python3 $S1/dsqa_build.py final                              # 11
python3 $S1/dsqa_build.py verify-final
python3 $S1/dsqa_build.py near-misses                        # 12
python3 $S1/dsqa_build.py inputs                             # 13  -> Stage 2 (sets dsqa, nm_dsqa)
python3 $S1/dsqa_expansion.py candidates                     # 14  (needs the Stage 2 inputs)
python3 $S1/dsqa_expansion.py sweep && python3 $S1/dsqa_expansion.py summary
python3 $S1/dsqa_expansion.py pilot && python3 $S1/dsqa_expansion.py records   # -> set xp_dsqa
python3 $S1/dsqa_state.py                                    # any time: every agent step's status
```

Agents reach the corpus only through [`../../tools/cm.py`](../../tools/cm.py) with `--stage 1 --step <step> --set
dsqa --qid <qid>` (each step keeps its own search log per question, `stage1/<set>/<step>/<qid>.searchlog.jsonl`);
[`guides/TOOLS.md`](guides/TOOLS.md) maps the original agents' two commands onto it (`search --center` gives the
original client's query-centred previews; `doc --max 1000000` its full documents). The original client had no
near-duplicate query refusal; the shared CLI's applies within a step's own log.

## Try it offline

```bash
python3 pipelines/cmass/stage1/dsqa/demo/make_demo.py
```

Twelve invented tasks over invented documents (the island nation of Oskeria; [`demo/corpus.json`](demo/corpus.json),
ids `demo_dsqa_NNN`, loaded by [`../../demo/demo_cm.py`](../../demo/demo_cm.py)) and a hand-written output for every
agent step run every deterministic step and every path (gate, strict, extreme, polish repair and fallback,
consolidation move and revert, coherence, annotator fix applied and rejected, flags, adjudication, table repair,
near misses, expansion with a member the corpus lacks), ending with Stage 2 packets built by
[`../../tools/packets.py`](../../tools/packets.py) for all three sets. The run directory `demo/run/` is gitignored.

## How this maps to the paper's Stage 1

| paper operation | here | differences in what was run |
|---|---|---|
| hop/clue decomposition | step 1 (the projector) | one hop per information-bearing constraint of the causal chain; hops were not labelled required or confirmatory, inclusion or exclusion at projection (required/confirmatory first appears in step 4); no computational hops with an operation and inputs |
| grounding | step 1 (the same agent, in the same pass) | evidence for the one derived member of a set only |
| answerability check | step 1's verdict (SUPPORTED = the paper's PROJECTABLE) and step 2's deterministic gate | for sets, at least one gold member, as the paper says; the gate drops 38 SUPPORTED projections whose own hop list contradicts the verdict |
| all-hops verification | steps 3-11 | not one filter but five review rounds added during the run (strict audit; extreme audit; coherence; annotator pass; adjudication and table repair) and a final snippet check, plus the evidence presentation of step 5; see below |

Where the run differs from the paper's description of Stage 1:

1. **Several verification rounds, each stricter or different**, instead of one all-hops check: coverage, re-grounding
   and an adversarial member check (3); atom-level coverage with every source attribution an atom (4); cross-hop
   coherence (6); readability (7); fatal-flag adjudication (9).
2. **Confirmatory hops are verified but not released**: the extreme audit keeps required hops only (28 omitted).
   The paper's all-hops verification checks confirmatory hops too, which step 4 did, but they are not in the
   Stage 2 input.
3. **Not filter-only.** Snippets were re-chosen (polish, consolidation), clues were reworded and snippets replaced by
   the annotator pass, and table quotes were repaired, each kept only when a deterministic check verified it.
4. **Set answers rest on one grounded member** through all of Stage 1; completeness was never checked. Stage 2 later
   required every member grounded and the set complete.
5. **Source attribution policy changed between rounds**: not addressed by the projector; waivable when not
   discriminating (3); an atom that must be grounded (4); must license the data the other hops read (6). Temporal
   qualifiers stayed relaxed (in-text dates only; bare "as of <year>" exempt).
6. **Manual steps**: the cross-check sample, the flag selection, the table-repair decision, and the model switch
   in the middle of step 4.
7. **Models differ by round** (Claude Opus 4.8, Fable 5, Opus 5), and step 4 itself ran on two models.
8. **Near misses** are only the 20 questions dropped after the extreme audit, not every question the verification
   dropped. The expansion (step 14) later re-entered 57 questions the funnel had dropped, without hops.

## Fidelity of this port

The deterministic steps were checked privately against the real run: run on the real agent outputs, they reproduce
the committed task files, the 296 / 258 / 170 / 84 lists, every committed agent input (strict audit, extreme audit,
polish, consolidation, coherence, annotator and adjudication inputs), the document labels, the consolidation
targets and verification, the applied fixes, the final 64, the 20 near misses, and the expansion's candidates,
ranking and pilot, exactly. Known differences:

- In the run, one question's polish output was replaced after its verification had run, and the verification was
  not redone; the evidence then mixed the new snippets with the old statuses (one hop kept the extreme audit's
  snippet, the one the annotator pass later found occurs in no document). Rerun in order, the port re-verifies.
- The original checks rewrote agent outputs in place (repaired polish snippets, reverted consolidations); the port
  leaves agent outputs untouched and records the outcome in `polish_verification.json` and
  `consolidation_verification.json`, with the same effect.
- The task files, the agent inputs of steps 4-10, the final list and the expansion's `RANKED.md` grouping were built
  by code that was not committed; the port reconstructs each and matches the committed files.
- `dsqa_state.py` rejects outputs whose fields contradict each other (for example `keep` not equal to the AND of the
  checks, or hop indices missing); every real output passes these checks.
- The expansion records are written sets first, then single answers in id order (the run wrote the single-answer
  pilot of 12 first).

The agent steps are not reproducible bit for bit; their outputs are the record, and the deterministic steps rebuild
every list from them.

## Not ported

The annotation workbook (`dsqa_hop_annotation.xlsm`, `annotation/build_workbook.py`), the reviewer summaries for it
(`summaries/summarize.js`) and an attribution-gap listing were presentation only and fed no step; the Stage 2
review site replaced the workbook.

## Files

| file | what |
|---|---|
| `stage0_tasks.py` | step 0 |
| `stage1.js` | the Workflow for every agent step (`{"step": ...}`); models and batch sizes as run |
| `guides/*.md` | one guide per agent step, and `TOOLS.md` (the corpus CLI) |
| `dsqa_state.py` | per step: eligible questions, packets, output validation; the workflow's state tool |
| `dsqa_build.py` | the deterministic steps 2-13 |
| `dsqa_expansion.py` | step 14 |
| `dsqa_common.py` | shared paths and text checks |
| `demo/` | invented corpus and the offline demo |

## Ported from (private repository, `origin/main` unless noted)

`artifacts/deepsearchqa/`: `README.md`, `HANDOFF.md`, `DSQA_RESULTS.md`, `STRICT_AUDIT.md`, `EXTREME_AUDIT.md`,
`FINAL_AUDIT.md`, `data/HF_CARD.md`, `tools/project_dsqa.js`, `tools/cm.py`, `tools/build_dsqa_final.py`,
`tools/build_audit_inputs.py`, `tools/audit_dsqa.js`, `tools/build_dsqa_audited.py`,
`extreme_audit/extreme_audit.js`, `extreme_audit/xcheck_audit.js`, `extreme_audit/build_extreme_final.py`,
`annotation/polish_snippets.js`, `annotation/verify_snippets.py`, `annotation/build_doc_labels.py`,
`annotation/consolidate.js`, `annotation/verify_consolidated.py`, `coherence/coherence_audit.js`,
`annotator_pass/annotator_pass.js`, `annotator_pass/apply_fixes.py`, `adjudication/` (the adjudication workflow and
the one-question table-quote repair workflow), `verify_final.py`, and the commit history of branch `deepsearchqa`
(commit `d4da3f05` for the model switch). Branch `hotpotqa-climbmix`: `artifacts/near_miss/build_near_miss.py`,
`artifacts/near_miss/README.md`. `artifacts/projection_audit/`: `HANDOFF.md`, `DSQA_SET_GUIDE.md`,
`tools/dsqa_presence_sweep.py`, `tools/dsqa_expansion.py`, `tools/common.py`, `dsqa_sets/expansion/`.
