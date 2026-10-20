# Final check guide (C6)

After a human accepts a question, sets whose config has `final_check: true` (by default the bcp,
trqa, dsqa and generic families) receive one more, fresh agent check, because their permitted
revisions can remove constraints or change answers. Sets with `final_check: false` (hotpotqa) are
exempt and go straight to script validation. You check one accepted version and write one file:
`final_check/<set>/<qid>[.r<N>].json` (format in `SCHEMA.md`, "Final check"). Do not edit any other
file. Read `AUDIT.md` first: its tools, grounding rules and shared requirements are the standard.
Examples use the invented island nation of Oskeria.

Your task names the version (question, answer, revision), the audit, the revision files (if any), the
review key and the reviewer's notes. Your job is to find every way the accepted question falls short.

## What to check

1. **The answer.** Write out the answer, the aliases documents use for it, and for a set or aggregate
   every member with the value the question asks for (`answer_set`). If the question asks for an order,
   set `ordered` and `order_by`, and verify the order. `answer_matches_source` is true unless a
   revision changed the answer; a changed answer must come from a permitted revision of the set's
   family (`recompute_answer`, or `correct_answer_typo` where only typos may change) with a written
   rationale, and must meet the completeness standard of `REVISION.md`.
2. **Every hop** (`hops`, one entry per accepted hop, original and added): open its documents and read
   each quote in context. Same entity, every qualifier and date, the value used, the named source?
   Reviewer notes may explain an inference from grounded premises; they never supply a missing fact.
3. **Every exclusion** (`exclusions`): is the fact that excludes each rival stated in a cited
   document?
4. **Omissions.** Search yourself (up to 6 queries, `--hits 50`, list documents, named candidates and
   category descriptions) for another entity that satisfies every constraint, a member left out, or a
   conflicting value. Every plausible candidate needs a recorded, grounded decision: a candidate the
   documents show fitting the other constraints with no recorded exclusion is a gap, even when the
   excluding fact exists in the corpus (a later director of the conservatory, whose page says he
   served until 1930, needs an exclusion hop in a question about who directed it "until 1901").
5. **Closure** (`closure`, sets and aggregates): a document giving the list or the size, a fixed
   domain, or the recorded three-way search; is it grounded?

## The result

- `status: "passed"` only if every point holds: no gaps, no blocking problems, every hop and exclusion
  grounded, a grounded closure for a set or aggregate.
- Otherwise `status: "failed"` with:
  - `gaps`, one per problem: `kind` `evidence` (a hop, exclusion or closure fact lacks grounding; give
    the document and a verbatim quote from `cm.py quote` when you found one that closes it), `wording`
    (the question needs new wording, e.g. a rival can be excluded by no stated fact under the current
    wording), or `answer` (the answer is wrong or incomplete);
  - `blocking`: anything else that fails the standard, one line each;
  - `repairable`: true when one repair attempt within the set's permitted revisions can fix it (an
    evidence gap; a wording problem the family's edits can fix; an answer problem only where the
    family allows answer changes); false otherwise.

A first failure that is repairable sends the question back to revision (C4): evidence gaps get one
evidence-only attempt, wording problems the family's limits. The repaired version is re-audited,
re-challenged and reviewed again, then checked here again. A second failure, or an unrepairable one,
excludes the question with your reasons.

Then run `python3 TOOLS/check_records.py SET QID --only final_check` and fix every ERROR. Run corpus
commands one at a time; a failed request is not evidence, so retry it. If a tool call is refused or
keeps failing so that you cannot finish, say so in your report: never claim a check you did not
complete.
