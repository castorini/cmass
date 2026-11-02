# Expansion audit guide (BrowseComp-Plus Stage 1, steps `strict_audit` and `rejudge`)

After the candidate list was fixed, every PROJECTABLE question that did not reach it (261 in the run) was
audited once more, one agent per question, to find near misses for Stage 2. Two judgments ran in order:

1. **`strict_audit`**: every hop at the human reviewers' verbatim standard, with a genuine rescue search for
   any hop the scrutiny passes had failed, plus a coverage check. In the run 1 of 261 qualified.
2. **`rejudge`**: every strictly failing hop of the other questions re-judged at the standard the human
   reviewers actually applied: core entity and relation grounded, a secondary qualifier waived. In the run 49
   questions survived (with the strict qualifier), 177 failed on a missing core fact and 35 on coverage.

**Provenance.** The prompts of these two steps were not committed to the private repository; only their
standard (`stage3/EXPANSION_326.md`, `stage3/VERIFIED53.md`) and their output
(`stage3/expansion_candidates49.jsonl`: per hop `index`, `clue`, `fully_grounded`, `basis`, `missing_qualifier`,
`snippet`, `supporting_doc_ids`) were. This guide states that documented standard and asks for that output; its
wording is a reconstruction, not the original prompt. The examples are invented.

`STAGE1` is `pipelines/cmass/stage1/bcp`, `TOOLS` is `pipelines/cmass/tools`; run commands from the run
directory, one corpus command at a time (a failed request is not evidence).

## Tools

```
python3 STAGE1/bcp_state.py SET STEP QID --input                # writes your input; read the printed path
python3 TOOLS/cm.py doc    --stage 1 --step STEP --set SET --qid QID DOCID --max 200000     # read a cited document IN FULL
python3 TOOLS/cm.py search --stage 1 --step STEP --set SET --qid QID "<query>" --hits 50 --chars 600 --center
python3 TOOLS/cm.py search --stage 1 --step STEP --set SET --qid QID "<query>" --hits 200 --only "<rare phrase>"   # deep sweep
python3 TOOLS/cm.py quote  DOCID "first words" "last words"                                 # an exact span
python3 TOOLS/cm.py verify DOCID "quote"                                                     # is it verbatim?
python3 STAGE1/bcp_state.py SET STEP QID --check                # must print OK
```

`STEP` is `strict_audit` or `rejudge`; `--step` gives each its own search log (the re-judging agent gets the
strict audit's result in its input, not its queries).

## Step `strict_audit`

Input: `{qid, question, answer, stratum, hops: [{index, clue, prior_grounded, doc_ids, prior_note}]}`: the full
decomposition, with the documents the two scrutiny passes found (`prior_grounded` is false where neither did).

- **Per hop**, read every cited document IN FULL (excerpts and previews are not enough). A hop is `SUPPORTED`
  only when ClimbMix text states the hop's fact verbatim at the reviewers' strictness: the right entity named;
  the relation explicit; dates and numbers present in the text itself (only in-text dates count as date
  evidence); no support by inference alone. Several documents may together support one hop (union across
  documents). `PARTIAL` when the documents establish part of the fact; `UNSUPPORTED` when they do not.
- **Rescue.** For every hop that is not `SUPPORTED` (in particular every hop with `prior_grounded` false),
  attempt a genuine rescue by deep search before you give up: reformulate, sweep with `--only`, read the
  documents that contain the rare terms. Set `rescued = true` on a hop your search moved to `SUPPORTED`.
- **Coverage.** Enumerate the question's information-bearing clues (entities, relations, events,
  superlatives, numbers, counts, intervals; "as of <year>" timestamps are not clues) and list in
  `uncovered_phrases` every one that no hop represents.
- `all_hops_grounded` = every hop `SUPPORTED`; `coverage_ok` = no uncovered phrase;
  `qualifies` = both. Write `stage1/<set>/strict_audit/<qid>.json`, one `hops` entry per input hop, in order:

```json
{"qid": "<qid>", "all_hops_grounded": false, "coverage_ok": true, "qualifies": false,
 "uncovered_phrases": [], "reason": "<one line>",
 "hops": [{"index": 1, "clue": "<the input clue>", "verdict": "SUPPORTED | PARTIAL | UNSUPPORTED",
           "doc_ids": [], "snippet": "<verbatim, or empty>", "rescued": false}]}
```

## Step `rejudge`

Input: the strict audit's input plus its result (`strict_audit: {hops, uncovered_phrases, reason}`). It runs
only for questions that did not qualify.

The human reviewers accept a hop when its CORE entity and relation are grounded, even if a secondary qualifier
is missing; they reject when the evidence concerns a DIFFERENT entity. Invented illustrations of that line:

- accepted: a hop says the Velmaran quays were rebuilt "in the 1910s after a flood"; the documents give the
  flood of 1911 and the rebuilding but no decade for the rebuilding itself (the decade is waived);
- accepted: a hop says Ilsa Renvaar "directed the conservatory for over two decades"; the documents give the
  founding and her death but not the length of her tenure (the duration is waived);
- rejected: a hop about the founder of the Velmaran Conservatory whose only document names Davin Kesh, the
  founder of a different conservatory (a different entity, not a missing qualifier).

For EVERY hop of the question, in input order:

- a hop the strict audit found `SUPPORTED` keeps its evidence: `fully_grounded = true`,
  `basis = "verbatim grounded"`, `missing_qualifier = ""`;
- re-judge every other hop at the reviewers' standard, reading its documents in full and searching further if
  needed. When the core entity and relation are grounded and only a secondary qualifier is missing:
  `fully_grounded = false`, `basis = "core fact grounded; qualifier waived: <the qualifier>"`,
  `missing_qualifier = "<the qualifier>"`. When the core fact itself is not found:
  `fully_grounded = false`, `basis = "core fact absent"`, `missing_qualifier = ""`;
- give each hop its best verbatim `snippet` and its `supporting_doc_ids`.

Coverage gaps remain hard failures: a clue that never became a hop cannot be waived. `outcome` is
`fail_coverage` when the strict audit listed uncovered phrases (re-check them; drop one only if a hop does
represent it), else `fail_core` when any hop's core fact is absent, else `survive`. Write
`stage1/<set>/rejudge/<qid>.json`:

```json
{"qid": "<qid>", "outcome": "survive | fail_core | fail_coverage", "reason": "<one line>",
 "hops": [{"index": 1, "clue": "<the input clue>", "fully_grounded": true, "basis": "verbatim grounded",
           "missing_qualifier": "", "snippet": "<verbatim>", "supporting_doc_ids": []}]}
```

Run `--check` until it prints OK. If you cannot finish, report that you did not; never report success for a
file you did not write.
