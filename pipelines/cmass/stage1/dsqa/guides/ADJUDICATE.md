# Step 9, final adjudication of a flagged question

You make the FINAL call on one flagged question for a benchmark that must be clean. The question
survived review but carries a serious flag (fabricated or unverifiable evidence, a gold answer the corpus
contradicts, a question premise the corpus contradicts, evidence that would mislead a reader). Go to the
documents (`cm.py doc ... --max 1000000`) and decide: drop the question, or keep it with a note an
annotator must see. Tools: `TOOLS.md`.

## Input

The packet: `{qid, problem, gold_answer, derived_answer, answer_type, hops: [{index, clue, doc_id, snippet, all_verified_docs}], flagged_issues, snippets_not_verbatim}`.
`snippets_not_verbatim` lists the hop indices whose snippet does not occur verbatim in the document it cites.

## Steps

1. Investigate EVERY flagged issue at the documents. Decide for each whether it is FATAL or SURVIVABLE.

   FATAL: the question cannot be part of a clean benchmark:
   - a snippet does not occur in the document it cites (check `snippets_not_verbatim` first; re-check
     yourself);
   - the corpus contradicts the gold answer, so a correct solver would be marked wrong;
   - the question's own premise is contradicted by the corpus;
   - the evidence chain cannot reach the answer without the reader supplying an unstated fact;
   - a hop's passage would lead a careful reader to the WRONG conclusion.

   SURVIVABLE: worth telling an annotator, but the question still stands:
   - some other document in the corpus disagrees, while the cited passage is right and clearly the
     better source;
   - the gold list is not proven exhaustive (the any-one-member rule waives completeness for Set Answers);
   - a hop is redundant with another, or worded loosely, without changing what is verified.

2. `decision` is `drop` if ANY flag is fatal, else `keep_with_note`. For `keep_with_note` write
   `annotator_note`: one or two plain sentences telling the annotator exactly what to watch for (for
   example: "another corpus document dates the founding to 1876; the cited passage is the conservatory's
   own history"). No jargon. `rationale`: two sentences on how you decided, naming the documents you
   checked.

3. Write the output.

## Output

```json
{"qid": "<qid>", "decision": "drop | keep_with_note", "annotator_note": "...",
 "fatal_flags": ["..."], "survivable_flags": ["..."], "rationale": "..."}
```
