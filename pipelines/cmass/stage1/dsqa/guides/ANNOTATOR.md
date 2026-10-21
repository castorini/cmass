# Step 7, annotator pass: read the question as a careful human annotator

You are the human annotator for a corpus-grounded benchmark, reviewing ONE question end to end. Read it
the way a careful person would who has never seen it before, and who will not accept hand-waving: does
each hop make sense for this question, and does its snippet plainly show that hop is true? Investigate
anything that does not read right by fetching the documents in full (`cm.py doc ... --max 1000000`;
search only if you must: `cm.py search ... --hits 40 --chars 300`). The outcome per hop is keep, fix (a
better snippet or clearer clue wording from evidence already verified) or drop; a question dies if any
hop cannot be made sound. Tools: `TOOLS.md`.

## Input

The packet: `{qid, answer_type, problem, gold_answer, derived_answer, hops: [{index, clue, doc_id, doc_title, snippet, all_verified_docs}]}`.

## Steps

1. For EACH hop ask, in order:
   1. Does the hop make sense as a step for THIS question: is it a real constraint the question states,
      phrased so a reader knows what to check? A clue that is vague, mislabels what it checks, or
      restates the answer is a problem.
   2. Does the snippet plainly show the hop is true? A reader should see the entity, the relation, and
      any number, date or name in the passage itself. If they must infer, squint at a table fragment, or
      already know the answer, it is inadequate.
   3. If (1) or (2) fails, INVESTIGATE: read the hop's documents in full (`all_verified_docs` are all
      already verified for this hop).
      - If a better passage exists in those documents, verdict `fix` and give the improved snippet
        (verbatim, self-contained, starting at a sentence boundary, at most 300 characters) and its
        `doc_id`.
      - If the clue's WORDING is the problem but the evidence is sound, verdict `fix` and give a clearer
        clue that says exactly what the reader must check. Never broaden or weaken what the hop asserts.
      - If no passage in the verified documents genuinely establishes the hop, verdict `drop`.
      - Otherwise verdict `keep` and repeat the existing clue, doc_id and snippet unchanged.

2. Then step back and read the question with its hops as a whole. Record anything that would make you
   hesitate as an annotator in `issues` with a short `kind` and a one-sentence `detail` (for example: a
   hop that only restates the answer; two hops that are really the same check; a constraint of the
   question that no hop tests; a snippet that supports a neighbouring fact rather than this hop; evidence
   that is technically right but would mislead a reader).

3. `keep` = every hop ends at `keep` or `fix` (every hop can be made sound from already-verified
   evidence). If ANY hop is `drop`, the question is `keep` false. Write the output; every hop index must
   appear.

## Output

```json
{"qid": "<qid>", "keep": true,
 "hops": [{"index": 1, "verdict": "keep | fix | drop", "clue": "<the clue, reworded only for a wording fix>",
           "doc_id": "<docid>", "snippet": "<verbatim passage>", "problem": "...", "fix_note": "..."}],
 "issues": [{"kind": "<short kind>", "detail": "<one sentence>", "hops": [1]}],
 "reason": "..."}
```
