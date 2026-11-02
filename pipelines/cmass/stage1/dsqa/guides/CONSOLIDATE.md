# Step 5c, evidence consolidation: fewer documents per question, never weaker evidence

You consolidate the EVIDENCE DOCUMENTS for one question so a human annotator reads as few documents as
possible. Every document listed for a hop was already verified to ground that hop. You are choosing WHICH
verified document to show, and copying the best passage from it. You are not re-judging whether the hop
is grounded. Evidence quality is never traded away: a hop keeps its current document unless the shared
one states the fact at least as plainly. Tools: `TOOLS.md`.

## Input

The packet: `{qid, problem, gold_answer, derived_answer, hops: [{index, clue, current_doc, current_snippet, verified_docs, doc_titles}]}`.

## Steps

1. Work out which document is used by the most hops (the question's ANCHOR document). Two documents that
   are different shards of the same page count as separate documents here: prefer whichever single shard
   can carry the most hops.
2. For each hop, decide its document:
   - If the hop's current document IS the anchor, keep it.
   - Otherwise, if the anchor document is among that hop's `verified_docs`, fetch it
     (`cm.py doc ... --max 1000000`) and look for a passage that states the hop's fact. Switch to the
     anchor ONLY IF that passage is AT LEAST AS CLEAR as the current snippet: same entity, same relation,
     and any number, date or name the clue asserts present in the passage. If the anchor's passage is
     vaguer, partial, or needs more context, KEEP the current document: a shorter document list is never
     worth weaker evidence.
   - If the anchor is not among the hop's `verified_docs`, consider any other verified document already
     used by a sibling hop, under the same test. Otherwise keep the current document.
3. For every hop output the final `doc_id` and `snippet`. The snippet must be copied VERBATIM from THAT
   document (trim at sentence boundaries, normalise whitespace, nothing else), self-contained, starting at
   a sentence boundary, one to three sentences, ideally under 300 characters. If a hop is unchanged,
   repeat its current snippet exactly. `changed` is true only when you moved the hop to a different
   document. `why`: at most 12 words, and for an unchanged hop that could have moved, say briefly why the
   anchor was not as clear.
4. Write the output, covering EVERY hop.

## Output

```json
{"qid": "<qid>", "docs_before": 3, "docs_after": 2,
 "hops": [{"index": 1, "doc_id": "<docid>", "snippet": "<verbatim passage>", "changed": false, "why": "..."}]}
```
