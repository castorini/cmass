# Step 6, coherence audit: do the hops, read together, justify the answer?

You judge whether ONE question's hops COHERE well enough to justify its answer. Every hop here already
passed an individual grounding check. That is NOT what you are testing. You are testing whether the hops,
read TOGETHER, actually justify the answer using consistent content: a named source must license the
very data the other hops read, and hops must agree on entity, edition or vintage, year, unit and
population. Passing every hop individually is not enough. Filter only. Fetch documents in full when you
need to (`cm.py doc ... --max 1000000`). Tools: `TOOLS.md`.

## Input

The packet: `{qid, answer_type, problem, gold_answer, derived_answer, hops: [{index, clue, doc_id, doc_title, snippet, all_verified_docs}]}`.

## Steps

1. **Attribution coverage.** If the question names a source ("according to X", "using data from X"), the
   hop grounding that source must license THE VERY DATA the other hops read: the same table, the same
   dataset, the same publication. Fetch the documents and check.
   An example of a real failure, retold in invented terms: a question said "According to the Oskerian
   Fisheries Board"; the Board's compilation credit sat in the 1910 edition of a page, while the figures
   used came from the 1905 edition of that page, which never names the Board. The credit was grounded,
   but not for the data used: `attribution_ok` false.
   Also false when the source document merely mentions the organisation in passing (for example as an
   industry partner) rather than crediting the data.

2. **Cross-hop consistency.** Read the snippets together and check they describe the SAME thing:
   - edition or vintage: do two hops read different snapshots, editions or report years of the same
     source, so their numbers are not comparable?
   - reference year: does the question fix a year while a hop's evidence is for a different year?
   - entity: do hops silently switch between similar entities (a city and its metro area, a country and
     a region, a company and its subsidiary)?
   - unit or measure: do hops mix units, currencies or measures (value and volume, gross and net,
     nominal and real) where the question needs one?
   - population or scope: do hops apply filters to different populations (all provinces and coastal
     provinces only, all products and one category)?
   Any such mismatch that the question's logic depends on means `cross_hop_consistent` false.

3. **Joint derivation.** Do the hops, as evidenced, actually lead to `derived_answer` (and is
   `derived_answer` a gold answer)? For a Set Answer the any-one-member rule applies: one gold member must
   be established against every constraint; list completeness is not required. If the hops leave a step
   to inference, `jointly_justifies` false.

4. Record every failure you found in `failures` with a short pattern name (reuse these where they fit:
   `attribution-not-for-data`, `edition-mismatch`, `year-mismatch`, `entity-drift`, `unit-mismatch`,
   `scope-mismatch`, `derivation-gap`) and a one-sentence detail. If you see a failure that none of those
   names fit, describe it in `new_pattern`.

5. `keep` = `attribution_ok` AND `cross_hop_consistent` AND `jointly_justifies`. Be strict: this decides
   a released benchmark. Write the output.

## Output

```json
{"qid": "<qid>", "attribution_ok": true, "cross_hop_consistent": true, "jointly_justifies": true, "keep": true,
 "failures": [{"pattern": "year-mismatch", "detail": "...", "hops": [2, 3]}],
 "new_pattern": "", "reason": "..."}
```
