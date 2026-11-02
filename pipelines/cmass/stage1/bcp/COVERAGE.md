# Coverage audit guide (BrowseComp-Plus Stage 1, step `coverage`)

You check ONE thing for one question: does every information-bearing CONTENT phrase in the QUESTION have a
corresponding hop? You judge ONLY from the question's data. Do NOT search the corpus, read documents, or use
outside knowledge, and do NOT assess how well the hops are grounded: only whether each content phrase is
REPRESENTED by a hop.

This is the prompt of the original `audit_coverage.js` (run on every all-hops-grounded question) and of
`coverage_repaired.js` / `coverage_538.js` (the same check on repaired near misses). The original embedded the
data in the prompt and gave the agent no tools; here you read it from one file and write one file, and run
nothing else. The examples of numbers below are invented.

## Steps

1. `python3 STAGE1/bcp_state.py SET coverage QID --input` writes the data and prints its path; read that file.
   It is `{qid, kind, question, answer, hops: [{clue, grounded, snippet, doc_ids}]}`. Use the hop `clue`s.
2. A CONTENT phrase is a substantive constraint the answer depends on: a named entity, a relation between
   entities, a specific event, a superlative, a count or quantity, or a numeric or temporal relation.
3. **Specific numbers are critical.** When the question states a specific number, count or interval (for
   example "rebuilt twice", "two sons and one daughter", "between 31 and 33 years later", "renamed 214 days
   later", "a crowd of 12,400", the exact minutes of goals), that NUMBER is itself a content sub-clue. A hop
   that mentions the surrounding event or entity but OMITS the specific number does NOT cover it: list the
   number as uncovered. Read each hop's clue text and check that the actual number appears.
4. List every uncovered content phrase (including omitted specific numbers) in `uncovered_phrases`.
5. IGNORE "as of <year>" / "in <year>" TIMESTAMP qualifiers: they are non-blocking, not sub-clues.
6. `coverage_ok` = `uncovered_phrases` is empty; `keep` = `coverage_ok`; give a one-line `reason`. Write
   `stage1/<set>/coverage/<qid>.json`:

```json
{"qid": "<qid>", "coverage_ok": true, "uncovered_phrases": [], "keep": true, "reason": "<one line>"}
```

7. `python3 STAGE1/bcp_state.py SET coverage QID --check` must print OK.

`STAGE1` is `pipelines/cmass/stage1/bcp`; run the two commands from the run directory. If you cannot finish,
report that you did not; never report success for a file you did not write.
