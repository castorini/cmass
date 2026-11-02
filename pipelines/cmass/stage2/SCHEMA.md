# Stage 2 record formats

Every Stage 2 step writes one JSON file per question in the run directory (`cmass/run.py`), so each
step can be inspected, checked and resumed. A step is done when its file exists and checks clean;
`tools/stage2_state.py` derives each question's next step from these files alone.

| file | written by | checked by |
|---|---|---|
| `inputs/<set>.jsonl` | Stage 1 (candidate records) | C1 in `cmass/stage2.py` (`candidate_problems`) |
| `packets/<set>/<qid>.md` | `tools/packets.py` | |
| `work/<set>/<qid>.audit.json` | auditor, then second reader (C2-C3) | `tools/validate.py` |
| `work/<set>/<qid>.audit.r<N>.json` | `tools/packets.py` (the audit of revision N, archived before a re-audit) | |
| `work/<set>/<qid>.searchlog.jsonl` | `tools/cm.py search` (every agent of every step) | |
| `revisions/<set>/<qid>.r<N>.json` | revision agent (C4) | `tools/check_records.py` |
| `adversarial/<set>/<qid>.<kind>[.r<N>].json` | hunters and verifier (C5) | `tools/check_records.py` |
| `review/reviews/reviews.json` | the review site (C5, human) | |
| `final_check/<set>/<qid>[.r<N>].json` | final checker (C6) | `tools/check_records.py` |
| `audited/<set>.jsonl`, `audited/rw_<set>.jsonl`, `review/site_config.json` | `tools/assemble.py` | `review_site/build_site_data.py` |
| `final/<set>_final.jsonl`, `final/<set>_excluded.jsonl` | `tools/finalize.py` | `tools/finalize.py` |

**Versions.** Revision 0 is the source question and answer. Each successful repair attempt N
(`revisions/<set>/<qid>.r<N>.json`) creates revision N; the current version is the last successful
one. Adversarial and final-check files carry the version they examined: no suffix for revision 0,
`.r<N>` for revision N. The audit file always holds the audit of one version (its `revision` field);
`packets.py` archives it as `<qid>.audit.r<N>.json` before a newer version is audited.

**Quotes.** Every quote anywhere in Stage 2 is a verbatim span of its document: identical except for
runs of whitespace (spaces, tabs, line breaks). Agents take them with `cm.py quote`, never retype them.

**Document ids** are the corpus's ids. A cited id the corpus does not have is listed by the auditor in
`rejected_docs` with the reason "not in the corpus".

## Candidate record (input, C1)

One line of `inputs/<set>.jsonl`, as Stage 1 writes it (`cmass/records.py`):

```json
{"record_id": "<id>", "question": "<question>", "answer": "<answer, or a list for a set>",
 "answer_type": "single | set | aggregate",
 "hops": [{"clue": "<atomic fact>", "doc_id": "<id>", "supporting_doc_ids": ["<id>"],
           "snippet": "<passage...>", "kind": "derivation", "redundant": false}],
 "hints": ["<source paragraphs: leads only, never evidence>"]}
```

C1 excludes a record (stage `validation`, reason `invalid_candidate`) without a question, without an
answer, with an unknown `answer_type`, with a `single` answer listing several members, or with a hop
without a clue. A hop citing no document, or a record without hops, is only a warning (every hop of
the audit is then an added hop). `answer_type` defaults to the set's (config.py).

## Audit: `work/<set>/<qid>.audit.json` (C2-C3)

`tools/validate.py` is the contract; this section states what it enforces. ERROR = the audit is not
finished; warn = look again.

```json
{"record_id": "<id>", "set": "<set>", "revision": 0,
 "question": "<the version audited>", "answer": "<its answer>",
 "constraints": [{"id": "C1", "text": "<verbatim span of the question>", "covered_by": [1, "A1"],
                  "status": "covered", "note": "..."}],
 "hops": [{"index": 1, "type": "required", "role": "inclusion", "constraints": ["C1"], "status": "grounded",
           "evidence": [{"doc_id": "<id>", "source": "original", "supports": "full",
                         "quotes": ["<verbatim span of the document>"]}],
           "rejected_docs": [{"doc_id": "<id>", "reason": "..."}], "clue_issue": "", "note": ""}],
 "added_hops": [{"id": "A1", "clue": "<the fact the hop establishes>", "type": "required", "role": "exclusion",
                 "candidate": "<the rival it excludes>", "constraints": ["C1"], "status": "grounded",
                 "evidence": ["..."], "rejected_docs": [], "note": "<why it is needed>"}],
 "candidates": [{"name": "<candidate>", "role": "answer", "status": "qualifies", "hops": [1, 2],
                 "aliases": [], "value": "", "note": ""}],
 "completeness": {"basis": "list_document", "doc_ids": ["<id>"], "queries": {"list": [], "named": [], "category": []},
                  "note": "..."},
 "contamination": {"leak": false, "single_document": false, "doc_ids": [], "note": ""},
 "answer_check": {"status": "derives_gold", "note": "<the chain from hops to answer>"},
 "verdict": "pass_with_fixes", "fail_reasons": [], "summary": "<1-3 sentences for the reviewer>",
 "verification": {"result": "confirmed", "changes": [], "note": "..."}}
```

- Required keys: `constraints`, `hops`, `added_hops`, `candidates`, `answer_check`, `verdict`, `summary`.
- `revision` (default 0) is the version audited. At revision 0, `question` and `answer` (when given)
  must equal the record's. At revision N > 0 the revision file `revisions/<set>/<qid>.r<N>.json` must
  exist with outcome `evidence_only` or `revised`, and `question` and `answer` must equal its own.
- **constraints**: unique `id`s; `text` is a verbatim span of the audited question (non-contiguous
  parts joined by ` ... `); `status` `covered | partially_covered | not_covered`; `covered_by` lists hop
  indices (1..n) and added-hop ids, and is non-empty when `covered`. At least one constraint.
- **hops**: exactly one entry per record hop, `index` 1..n. `type` `required | confirmatory |
  derivation`; `role` `inclusion | exclusion` (default inclusion); `constraints` are constraint ids.
  - A factual hop has `status` `grounded | partially_grounded | ungrounded`. Each `evidence` entry has
    `doc_id`, `source` (`original` = cited by this hop; `other_hop` = cited by another hop; `search`),
    `supports` (`full | partial`) and at least one quote, each verbatim in the document. `grounded` needs
    at least one `supports: full`. `partially_grounded` and `ungrounded` need a `note` saying what is
    missing (and what was searched). `rejected_docs` entries are `{doc_id, reason}`.
  - A `derivation` hop has `status` `derivation_ok | derivation_wrong` and a `note` with the operation
    and its grounded inputs.
  - `dropped` only in an audit of a revision (revision > 0), with a `note` naming the removed
    constraint the hop served.
- **added_hops**: `id` `A1`, `A2`, ... (no `index`), a `clue`, a `note` on why it is needed, `type`
  `required | confirmatory`, `status` `grounded | partially_grounded | ungrounded`, evidence as above.
  An `exclusion` hop names the `candidate` it excludes (warn otherwise).
- **candidates**: every plausible answer the audit considered. `role` `answer | member | rival`;
  `status` `qualifies | excluded | unresolved`; `hops` the hops that decide it. `qualifies` and
  `excluded` need at least one grounded hop among them; `excluded` needs an `exclusion` hop among them;
  `unresolved` needs a `note`. Optional `aliases` (other names in the documents) and `value` (a value
  the answer states for the member).
- **completeness**: required for `set` and `aggregate` answers, with `basis` `list_document` (with
  `doc_ids` listing the members or the set size), `fixed_domain` (every member of a fixed domain checked)
  or `three_way_search` (with `queries` for each of the routes `list`, `named`, `category`). A single
  answer may give `basis` `unique_by_constraints`. warn without a `note`.
- **contamination**: `leak` (a document reproduces the question and answer) or `single_document` (one
  document supplies every fact needed) requires `contamination` among `fail_reasons`.
- **answer_check.status**: `derives_gold | partial | does_not_derive`.
- **verdict**: `pass` when every constraint is covered, every hop grounded by its own cited documents
  (or `derivation_ok`), the answer derived, every answer or member `qualifies`, no rival qualifies, no
  candidate is unresolved, no contamination, and no added hops. `pass_with_fixes` when the same holds
  only thanks to the audit (added hops, or evidence from documents the hop did not cite). `fail`
  otherwise, with `fail_reasons` from: `ungrounded`, `incorrect_question`, `incorrect_answer`,
  `unresolved_alternative`, `incomplete_set`, `unsupported_member`, `inconsistent_evidence`,
  `contamination` (the paper's failure-reasons table; see `AUDIT.md`). No `fail_reasons` unless `fail`.
- **verification** (the second reader, C3; `check_records.py`): `result` `confirmed | revised`,
  `changes` (one line per status, verdict, evidence or constraint change; empty when confirmed), `note`.
  An audit without it is not finished.

## Revision attempt: `revisions/<set>/<qid>.r<N>.json` (C4)

One attempt per trigger. The revision agent writes it; it never edits the audit (the repaired version
is re-audited from scratch).

```json
{"record_id": "<id>", "set": "<set>", "attempt": 1,
 "trigger": {"stage": "adversarial", "ref": "<the file or review key that asked for the repair>",
             "reasons": ["..."], "evidence_only": false},
 "based_on_revision": 0,
 "evidence_retry": {"queries": ["<query>"], "resolved": false, "note": "..."},
 "outcome": "revised",
 "question": "<the new version's question>", "answer": "<its answer>",
 "edits": [{"kind": "add_qualifier", "from": "", "to": "<the added words>", "note": "..."}],
 "edit_rationale": "<why the edit is the least extensive permitted one and keeps the answer correct>",
 "hop_changes": [{"hop": "A1", "change": "added", "note": "..."}],
 "completeness_note": "", "trivial_as_written": false, "blocker": "", "note": ""}
```

- `attempt` is N (consecutive from 1); `based_on_revision` is the version repaired (N-1).
- `trigger.stage`: `audit` (only for revision 0's audit: a failed re-audit of a repair excludes),
  `adversarial` (the verifier refuted the version), `human` (the decision `accept_with_fixes`; `ref` is
  the version's review key), `final_check` (the check failed; `evidence_only` true when every gap is an
  evidence gap, and then the attempt may not edit).
- `evidence_retry`: the searches retried first (they also appear in the search log), whether evidence
  alone resolved the problem.
- `outcome`:
  - `evidence_only`: `question` and `answer` unchanged, no `edits`, `evidence_retry.resolved` true;
  - `revised`: at least one edit, an `edit_rationale`, and the question or answer changed;
  - `unsuccessful`: a `blocker` (what is missing, what was searched and tried). It must be the last
    attempt: the question is excluded (stage `revision`).
- `edits[].kind`, question edits: `correct_wording`, `add_qualifier`, `correct_clue`, `remove_clue`,
  `adjust_range`, `remove_source_clause`, `adjust_year_or_threshold`, `change_attribute_if_trivial`,
  each permitted only if the set's family permits it (`config.permitted_revisions`); answer edits:
  `recompute_answer` (families whose answer may change, with `completeness_note`) and
  `correct_answer_typo` (a small spelling change; families with `answer_may_change` true or
  `"typo_only"`). `from` is the replaced span of the previous question (removals and corrections),
  `to` the new span of the new question (additions and corrections). A question change needs a
  question edit and an answer change an answer edit. `change_attribute_if_trivial` needs
  `trivial_as_written: true`. At most `max_added_qualifiers` (set config, default 1) `add_qualifier`
  edits across all attempts.
- `hop_changes[].change`: `evidence_added | added | dropped | retyped | regrounded`.

## Hunter findings: `adversarial/<set>/<qid>.<kind>[.r<N>].json` (C5)

`kind` is `hunt` (one hunter, route `all`) or, for aggregates and changed answers (or a set with
`"two_hunters": true`), `hunt_list` (route `list`: list, table and catalogue documents) and
`hunt_named` (route `named`: every known candidate searched by name).

```json
{"record_id": "<id>", "set": "<set>", "revision": 0, "kind": "hunt", "route": "all",
 "question": "<the version challenged>", "answer": "<its answer>",
 "findings": [{"id": "F1", "kind": "rival", "candidate": "<entity or value>",
               "claim": "<how it breaks the question>", "in_corpus": true,
               "evidence": [{"doc_id": "<id>", "quotes": ["<verbatim span of the document>"]}],
               "web_lead": "", "excluded_by": "", "changes_answer": true, "confidence": "certain"}],
 "n_searches": 7, "note": "..."}
```

`findings[].kind`: `rival`, `missing_member`, `wrong_member`, `conflicting_value`,
`alternative_reading`, `ungrounded_hop`, `other`. `in_corpus` true needs evidence (verbatim quotes);
a finding known only from outside the corpus has `in_corpus` false and may give a `web_lead`.
`confidence`: `certain | likely | possible`. `question` and `answer` must be the version's.
Empty `findings` is a valid result.

## Verifier rulings: `adversarial/<set>/<qid>.verify[.r<N>].json` (C5)

```json
{"record_id": "<id>", "set": "<set>", "revision": 0, "hunts": ["hunt"],
 "rulings": [{"finding": "hunt:F1", "ruling": "does_not_refute", "docs_read": ["<id>"],
              "reason": "<what the passages say in context>",
              "record_exclusion": {"doc_id": "<id>", "quote": "<verbatim span of the document>"}}],
 "refuted": false, "blocking": [], "note": "..."}
```

One ruling per finding of every hunter file of that version (`finding` = `<kind>:<id>`), `ruling`
`refutes | does_not_refute` with a `reason`. `refuted` is true exactly when some ruling refutes, and
then `blocking` lists the problems. `record_exclusion` (optional) gives the stated fact that excludes a
candidate the audit does not yet record (the final check asks for it). A verified refutation sends the
question to C4 once; a second refutation, of any later version, excludes it (stage `adversarial`).

## Human decision: `review/reviews/reviews.json` (C5)

The review site's store (`review_site/README.md`): `"<tab>~<record id>"` maps to
`{decision: accept | accept_with_fixes | reject | unsure | null, comment, hops: {<id>: {agree, comment}},
constraints: {<id>: {agree, comment}}, updated_at}`. A version's key is `<set>~<qid>` for revision 0,
`<set>~<qid>.r<N>` for an evidence-only revision N, and `rw_<set>~<qid>.r<N>` for a revision N that
changed the question or answer (reviewed in the rewrites tab, beside the source wording). A decision
counts only for the version it was made on. `accept` advances to C6; `accept_with_fixes` sends the
comments to C4; `reject` excludes (stage `human`); `unsure` or none waits.

## Final check: `final_check/<set>/<qid>[.r<N>].json` (C6)

Only for sets whose config has `final_check` true; other sets are `exempt`.

```json
{"record_id": "<id>", "set": "<set>", "revision": 0, "status": "passed",
 "question": "<the version checked>", "answer": "<its answer>", "answer_matches_source": true,
 "answer_set": [{"member": "<member>", "aliases": ["<other name>"], "value": ""}],
 "ordered": false, "order_by": "",
 "hops": [{"id": "1", "grounded": true, "problem": ""}],
 "exclusions": [{"candidate": "<rival>", "grounded": true, "docs": ["<id>"], "problem": ""}],
 "closure": {"basis": "list_document", "grounded": true, "docs": ["<id>"], "note": "..."},
 "gaps": [{"kind": "evidence", "what": "...", "doc_id": "", "quote": ""}],
 "repairable": false, "blocking": [], "note": "..."}
```

`status` `passed` needs no gaps, no blocking problems, every hop and exclusion grounded, and for a set
or aggregate a grounded closure. `failed` lists `gaps` (`kind` `evidence`: a fact lacking grounding,
with a lead `doc_id` and verbatim `quote` when found; `wording`: the question needs new wording;
`answer`: the answer is wrong) or `blocking` problems, and says whether C4 can repair it within the
set's limits (`repairable`). `answer_set` lists every member (one for a single answer) with aliases
and required values; `ordered`/`order_by` when the question asks for an order. A first failure sends a
repairable question to C4 (evidence gaps only: an evidence-only attempt); a second failure, or an
unrepairable one, excludes it (stage `final_check`).

## Question state: `tools/stage2_state.py --json`

`{"questions": [...]}`, one object per question: `set`, `qid`, `revision`, `attempt` (the next repair
attempt's number), `next` (`audit`, `second_read`, `revise`, `hunt`, `verify`, `review`,
`final_check`, `ready`, `excluded`), `detail`, `stage` and `reason` (when excluded), `trigger` (when
`revise`: `{stage, ref, reasons, evidence_only}`), `family`, `group`, `permitted`, `answer_type`,
`question`, `answer`, `question_changed`, `answer_changed`, `final_check_enabled`, `site_dataset`,
`site_record_id`, `review_key`, `decision`, `audit_file`, `packet_file`, `revision_file`, `hunts`
(`[{kind, path, done}]`), `verify_file`, `final_check_file`, `final_check_status`, `warnings`.

## Output records (the contract with Stage 3)

### `final/<set>_final.jsonl`

```json
{"record_id": "<id>", "set": "<set>",
 "source": {"question": "<source question>", "answer": "<source answer>"},
 "question": "<final question>", "answer": "<final answer>", "answer_type": "single",
 "category": "narrowed", "revision": 1,
 "edits": [{"revision": 1, "kind": "add_qualifier", "from": "", "to": "<added words>", "note": "..."}],
 "edit_rationale": "r1: ...",
 "constraints": [{"id": "C1", "text": "<span>", "covered_by": [1, "A1"], "status": "covered", "note": ""}],
 "hops": [{"id": "1", "clue": "<fact>", "type": "required", "role": "inclusion", "constraints": ["C1"],
           "status": "grounded", "evidence": [{"doc_id": "<id>", "supports": "full", "quotes": ["<span>"]}], "note": ""},
          {"id": "A1", "clue": "<fact>", "type": "required", "role": "exclusion", "candidate": "<rival>",
           "constraints": ["C1"], "status": "grounded", "evidence": ["..."], "note": ""}],
 "dropped_hops": [{"id": "3", "clue": "<fact>", "note": "..."}],
 "candidates": [{"name": "<candidate>", "role": "answer", "status": "qualifies", "hops": [1, 2], "note": ""}],
 "completeness": {"basis": "unique_by_constraints", "note": "..."},
 "answer_set": [{"member": "<member>", "aliases": [], "value": ""}], "ordered": false, "order_by": "",
 "search_queries": ["<query>"],
 "audit_verdict": "pass_with_fixes", "audit_summary": "...",
 "adversarial": {"versions_checked": 2, "refuted_at_revisions": [0]},
 "human_decision": "accept", "review_key": "rw_<set>~<id>.r1",
 "reviewer_notes": [{"on": "question", "id": "", "agree": null, "comment": "..."}],
 "final_check": {"status": "passed", "note": "..."},
 "also_in": [{"set": "<other set>", "record_id": "<id>", "review_key": "..."}]}
```

Always present (`cmass.stage2.FINAL_FIELDS`): `record_id`, `set`, `source`, `question`, `answer`,
`answer_type`, `edits`, `edit_rationale`, `constraints`, `hops`, `candidates`, `completeness`,
`search_queries`, `audit_verdict`, `human_decision`, `reviewer_notes`, `final_check`.

- `hops`: every accepted hop: original hops as `"1"`..`"n"` (dropped ones go to `dropped_hops`), then
  added hops `"A1"`, ...; each with `clue`, `type` (`required | confirmatory | derivation`), `role`
  (`inclusion | exclusion`), `candidate` when an exclusion, `constraints`, `status` (`grounded`, or
  `derivation_ok` for a derivation hop, whose `note` gives the operation and inputs), and `evidence`
  `[{doc_id, supports, quotes}]`. Every quote was matched again against its document by `finalize.py`.
  These documents are the accepted evidence Stage 3 builds qrels from.
- `search_queries`: every distinct query in the question's search log, in order (projection, audit,
  adversarial check, revision and final check); the ranked results stay in
  `work/<set>/<qid>.searchlog.jsonl`.
- `completeness`: the audit's block, or null for a single answer audited without one.
- `answer_set`, `ordered`, `order_by`: from the final check (members, aliases, required values,
  ordering), else from the qualifying candidates.
- `category`: `unchanged | reworded | constraint_removed | narrowed | answer_changed` (an answer change
  takes precedence; then an added qualifier, then a removed clause or clue; otherwise reworded).
  `unchanged` includes evidence-only repairs.
- `final_check.status`: `passed`, `repaired` (passed after a final-check failure was repaired), or
  `exempt` (the set's config has `final_check` false).
- `human_decision` is always `accept`; `reviewer_notes` holds the reviewer's comments and
  disagreements on the accepted version (`on`: `question | constraint | hop`). Reviewer explanations
  of inferences are notes, never evidence.
- `also_in`: the same source question and answer accepted from another input list, included here once.

### `final/<set>_excluded.jsonl`

```json
{"record_id": "<id>", "set": "<set>", "stage": "human", "reason": "rejected", "detail": "...", "revision": 0}
```

`stage` and `reason`: `validation` (`invalid_candidate`, `duplicate`), `audit` (`contamination`),
`revision` (`repair_unsuccessful`, `reaudit_failed`), `adversarial` (`second_refutation`), `human`
(`rejected`), `final_check` (`not_repairable`, `second_failure`). `detail` gives the agent's or the
reviewer's reasons.

## Review-site records (`tools/assemble.py`)

`audited/<set>.jsonl` and `audited/rw_<set>.jsonl` follow `review_site/README.md` ("Record format"):
the record's fields, `hops[].audit` from the audit, and `audit` with the verdict, constraints, added
hops, answer check, second reader (`verification`), searches and counts. Added for Stage 2:
`audit.candidates`, `audit.completeness`, `audit.contamination`, `audit.adversarial` (findings and
rulings), `audit.revision`, `audit.salvage` (a revised version: `proposed_question`/`proposed_answer`
shown beside the source wording, `repairs`, `rewrite_rationale`, `caveats`), `cmass` (set, qid,
revision, next step, review key) and `annotator_note` (the state, candidate decisions, completeness and
adversarial findings as a note under the question).
