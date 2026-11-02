# Step 3, strict audit: coverage, full-document re-grounding, answer check, adversarial member check

You AUDIT one DeepSearchQA projection that is already marked SUPPORTED, under a STRICT
corpus-grounded standard adapted to causal-chain problems. Your job is to REFUTE the claim if it is
refutable. Default to failing a check when the evidence does not clearly carry it. Do
NOT trust the prior run's labels or snippets; re-verify everything yourself. Tools: `TOOLS.md`.

Full-list completeness for Set Answers is WAIVED (the any-one-answer rule).

## Input

The packet: `{record_id, answer_type, problem_category, problem, gold_answer, claimed_answer,
self_reported_failed_clues, hops: [{clue, supporting_doc_ids, snippet}]}`.

The prior run recorded `self_reported_failed_clues`: things IT could not ground. Treat these as leads,
not as verdicts; some are legitimately waived (see the Set rule), others are real defects.

## Steps

1. **Coverage (constraints to hops).** Enumerate EVERY information-bearing constraint stated in the
   problem: entities, relations, filters, dates, quantities, superlatives, orderings, and any numeric
   qualifier embedded in a phrase (an interval, a count, a threshold, a rank). DSQA problems are causal
   chains, so also capture each bridging step the chain depends on. For each constraint, check it maps
   to at least one hop. Any constraint with no corresponding hop is an UNCOVERED CONSTRAINT: list it in
   `uncovered_constraints` and set `constraints_covered` false. A hop that merely restates the answer
   does not cover a constraint.

2. **Re-ground every hop against FULL document text.** For each hop, fetch each cited document
   (`cm.py doc ... --max 1000000`) and READ IT IN FULL. `grounded` is true ONLY IF the document's own
   text establishes that hop's fact for the SAME entity with the SAME relation or value. A keyword
   collision, a merely on-topic document, a document about a similarly named entity, or your own outside
   knowledge all mean `grounded` false. If a cited document fails, you MAY search for a replacement
   (`cm.py search ... --hits 30` to `--hits 200`). If a genuine replacement grounds the fact, keep
   `grounded` true and record the new document id in `doc_ids` with a note. Otherwise the hop fails.
   Copy a verbatim snippet for every hop you pass. `all_grounded` = every hop grounded.

   Qualifier policy (apply carefully):
   - **Temporal** ("as of 1904", "in 1911"): non-disqualifying when the entity and relation are
     grounded, because the corpus exposes NO bibliographic metadata (documents are id and text only).
     Only explicit IN-TEXT dates count as date evidence.
   - **Source attribution** ("according to the Oskerian Statistics Office", "per the Velmaran
     Geographical Society's 1904 yearbook", "the Amberly Trade Register list"): non-disqualifying ONLY IF
     the underlying fact is grounded AND the answer would not change under a plausible alternative
     source. If the named source is DISCRIMINATING (different sources plausibly yield a different
     answer, ranking or set), the source qualifier is information-bearing and must itself be grounded;
     if it is not, that hop fails and the constraint is uncovered.

3. **Answer check.**
   - Single Answer: `answer_ok` = the answer derivable from the grounded hops equals `gold_answer`
     (semantic match).
   - Set Answer: the gold is a LIST. `answer_ok` = `claimed_answer` is genuinely ONE OF the gold list
     members. Full-list completeness is explicitly WAIVED: do NOT fail a question for missing or extra
     members, and do NOT require the ordering.

4. **Adversarial member check** (`member_valid`). Take the `claimed_answer` and actively try to REFUTE
   it: does it genuinely satisfy EVERY constraint you enumerated in step 1, according to corpus evidence
   you fetched? Issue real queries against any constraint the hops cover weakly. `member_valid` is false
   if the corpus contradicts a constraint for the claimed member, or if a constraint is satisfied only by
   your own knowledge rather than corpus text. For a Single Answer, this is the same test applied to the
   gold answer.

5. `keep` = `constraints_covered` AND `all_grounded` AND `answer_ok` AND `member_valid`. Write the output.
   `failures` are short specific strings naming what broke (which hop, which constraint, which document).

## Output

```json
{"record_id": "<qid>", "constraints_covered": true, "all_grounded": true, "answer_ok": true,
 "member_valid": true, "keep": true,
 "hops": [{"clue": "<the hop>", "grounded": true, "doc_ids": ["<docid>"], "snippet": "<verbatim passage>",
           "note": "<e.g. replacement document found>"}],
 "uncovered_constraints": ["<constraint>"], "failures": ["<what broke>"], "reason": "<summary>"}
```
