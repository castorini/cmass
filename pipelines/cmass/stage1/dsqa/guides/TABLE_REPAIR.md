# Step 10, table-quote repair

Replace the evidence snippets of one question that show REFORMATTED table quotes with passages that occur
verbatim in the cited documents, or report that no verbatim passage can carry the hop. Tools: `TOOLS.md`.

## Input

The packet: `{qid, problem, gold_answer, derived_answer, answer_type, hops: [{index, clue, doc_id, snippet, all_verified_docs}], repair_hops}`.
The hops listed in `repair_hops` currently show snippets that do not occur in their document: typically
the figures are real but the text as displayed does not occur in the document, because columns were
rebuilt with ` | ` separators or a document id was pasted inside the quote.

## Steps

For EACH hop in `repair_hops`: fetch the hop's documents (`cm.py doc ... --max 1000000`) and find a
passage that (a) occurs VERBATIM in that document, character for character apart from whitespace, and
(b) shows what the hop asserts.

- A run of table cells IS acceptable if you copy it exactly as the document renders it, including its own
  separators, with line breaks collapsed to single spaces. Do not insert separators, do not paste a
  document id inside the quote, do not join text from two documents.
- If a hop's fact only exists as a column layout that cannot be quoted intelligibly, set `possible` false
  and explain in `note`: do not invent readable prose.

Return every hop of `repair_hops` with `possible`, `doc_id`, `snippet`, `note`.

## Output

```json
{"qid": "<qid>", "hops": [{"index": 1, "possible": true, "doc_id": "<docid>", "snippet": "<verbatim passage>", "note": "..."}]}
```
