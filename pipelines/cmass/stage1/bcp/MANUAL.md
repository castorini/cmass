# Manual steps of BrowseComp-Plus Stage 1

Six decisions in the procedure that ran were made by a person (or, for the deep audit, by an agent run whose
tool and model were not recorded), not by a committed script. They are recorded here as decisions files in
`stage1/<set>/manual/`, each an object keyed by question id. `bcp_build.py` refuses to build past a manual step
until its file exists; write `{}` when nothing is decided. Nothing here automates these decisions: the files
only make them explicit and checkable.

| file | when | what was decided in the run | criteria recorded |
|---|---|---|---|
| `skips.json` | before `bcp_build.py verdicts` | 1 of 830 questions had no projection: the agent refused it three times on content-policy grounds; recorded as `AUP_SKIP` | the refusal itself |
| `review.json` | after `coverage-final` | 8 of the 58 coverage-audited questions were removed by hand (58 -> 50) | none beyond the commit title "Remove unsupported final BCP questions" (2026-07-05) |
| `repairs.json` | after `repaired`, and again before `candidates` | 5 claimed repairs checked against the fixing documents: 4 verified, 1 rejected; the 4 accepted under the relaxed temporal-qualifier policy after a strict deep audit had rejected them | below |
| `overrides.json` | before `candidates` | the hop lists of 5 candidates were replaced by hand: 1 rewritten by the deep audit, the 4 repairs edited when restored | below |
| `expansion_trim.json` | after `expansion` | 2 of the 49 expansion survivors removed (49 -> 47) | none beyond the commit title "Keep 47 evidence-supported BCP questions" (2026-08-03) |
| `qrels_review.json` | before `near-misses` | 8 of the 65 candidates cut by the later manual review of their qrels; they became the tier-1 near misses (and stayed candidates) | below |

## `skips.json`

`{"<qid>": {"reason": "content-policy refusal"}}`. The question gets verdict `AUP_SKIP`, method
`content_policy_block`, and goes no further. A task with neither a valid projection nor a skip entry stops
`bcp_build.py verdicts`.

## `review.json` (58 -> 50)

`{"<qid>": {"decision": "drop" | "keep", "reason": "..."}}`, over `coverage_final.jsonl`. In the run a person
deleted 8 records from the list. The commit records no per-question reasons. A dropped question is also
excluded from the near-miss repair pools.

## `repairs.json` (5 claimed -> 4 accepted)

`{"<qid>": {"verified": true | false, "decision": "accept" | "reject" | "", "reason": "..."}}`, over
`repaired.jsonl`.

- `verified`: the operator fetched every document a repair cited and confirmed that its content states the
  failing hop's fact about the same entity. In the run one claim was rejected here: its document supported only
  part of the failing clue. Only `verified` repairs get a coverage bundle
  (`bcp_build.py coverage-bundles`, then the `coverage` step, the original `coverage_repaired.js`).
- `decision`: the release decision. In the run the 4 verified repairs were first added (61 -> 65), then removed
  by a strict deep audit, then restored under a policy change:
  - **Deep audit** (`stage2/near_miss_recovery/ROUND3_DEEP200.md`, `ROUND4_RECONSIDERATION.md`, 2026-07-09;
    tool and model not recorded). Standard: every factual atom of the untouched question is an explicit hop;
    every hop has full-document ClimbMix support; the chain uniquely derives the answer; no single document
    collapses the question by revealing subject and answer together; a failed search is "not recovered", never
    proof of absence. Retrieval: six or more decomposed queries per question at up to 200 hits, at least 250
    unique full documents per question (`near_miss_recovery/deep_retrieve.py`), screened deterministically for
    every atom's entities, dates and numbers, then adjudicated in full. Result: all 4 rejected, so they were
    removed (65 -> 61); a focused reconsideration with new relation-specific queries kept all 4 rejected.
  - **Temporal-qualifier policy** (2026-07-10). The release policy was relaxed: every question atom must still
    be a hop with grounded entities and relations, but an exact temporal qualifier may be inferred across
    documents or marked non-disqualifying when the underlying entities and relations are supported (the corpus
    API exposes no publication or crawl dates, so a date cannot come from metadata). Under it the 4 were
    restored (61 -> 65), with two evidence corrections and the remaining caveats written into the hop text
    (`overrides.json`).
  - The one phase-B claim (two failing hops) passed its coverage check but was rejected by the same deep audit
    on grounding and answer collapse, and was not restored.

`bcp_build.py candidates` adds a repaired question only when `verified` is true, `decision` is `accept` and its
coverage audit passed.

## `overrides.json`

`{"<qid>": {"hops": [{"clue": "...", "redundant": false, "supporting_doc_ids": ["..."]}], "reason": "..."}}`,
over the candidates. The hop list replaces the built one in `candidates.jsonl` and `inputs/<set>.jsonl`. In the
run: the deep audit expanded one already-kept question from six compound hops (some labelled redundant) to eight
explicit supported hops; and the 4 restored repairs got corrected evidence and caveats stated in their hop text.
49 of the 54 records from our own chain are exactly what `bcp_build.py` rebuilds; these 5 are the exceptions.

## `expansion_trim.json` (49 -> 47)

`{"<qid>": {"decision": "drop", "reason": "..."}}`, over `expansion_survivors.jsonl`. In the run 2 survivors
were removed and other survivors' hop records edited in the same commit; no reasons are recorded.

## `qrels_review.json` (the 8 tier-1 near misses)

`{"<qid>": {"reason": "..."}}`, over the candidates. This decision came after Stage 1: the 65 candidates' qrels
were built and reviewed by people in a workbook (the earlier BCP-only qrels workflow,
`pipelines/bcp_climbmix/qrels/`). A question was kept iff every required hop was SUPPORTED in the
human-verified audit column or covered by a document a reviewer marked "Supports"; blank verdicts were later
ruled supported. 57 were kept; the other 8 (a required hop judged PARTIAL or UNSUPPORTED) were written to the
near-miss list as tier 1 (`failed_gate` "manual qrels review"). They were still in the candidate list that
entered Stage 2, so Stage 2 audited them both as candidates and as near misses.
