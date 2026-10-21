# Step 5a, snippet polish: the clearest evidence passage per hop

You prepare EVIDENCE SNIPPETS for human annotators. Each hop of the question is already verified as
grounded. Your job is presentation, not re-judging: for every hop, pick the ONE passage a human can read
in five seconds and immediately say "yes, that supports this hop". Tools: `TOOLS.md`.

## Input

The packet: `{qid, problem, gold_answer, derived_answer, answer_type, hops: [{index, clue, doc_ids, current_snippet}]}`.

## Steps

1. For each hop, fetch its cited documents in full (`cm.py doc ... --max 1000000`).
2. Choose the single best passage, judged by these rules in order:
   1. It states the hop's fact OUTRIGHT: the entity, the relation, and any number, date or name the clue
      asserts appear in the passage itself.
   2. It is SELF-CONTAINED: it begins at a sentence boundary, reads as normal prose or a clearly labelled
      table row, and needs no surrounding context to make sense. Never begin mid-sentence.
   3. It is SHORT: one to three sentences, at most about 300 characters. Trim unrelated tail text.
   4. Prefer a single continuous passage. Only if the fact genuinely spans two places in the SAME
      document, join two fragments with ` ... `; never join across different documents.

   Copy the passage VERBATIM from the document (you may trim at sentence boundaries and normalise
   whitespace, nothing else). Record which `doc_id` it came from. If several cited documents qualify, pick
   the one whose wording is most explicit and least ambiguous. The `current_snippet` is a starting point
   only: replace it whenever a cleaner passage exists in the same documents.
   `why`: at most 12 words on what makes this passage decisive (for example "names the river and its
   1911 flood directly").
3. Write the output, covering EVERY hop index.

## Output

```json
{"qid": "<qid>", "hops": [{"index": 1, "doc_id": "<docid>", "snippet": "<verbatim passage>", "why": "<at most 12 words>"}]}
```
