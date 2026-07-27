# BCP -> ClimbMix v1

This release contains 65 BrowseComp-Plus questions whose complete reasoning
chains were verified against ClimbMix-400B. Every one of the 400 listed hops has
at least one relevant ClimbMix document in the published hop qrels.

## Files

| File | Purpose |
| --- | --- |
| `questions.jsonl` | Questions, answers, hop decomposition, and Stage-2 seed documents |
| `qrels.jsonl` | Canonical question-level evidence sets |
| `qrels_hops.jsonl` | Transparent per-hop qrels used to audit coverage |
| `qrels.trec` | Four-column TREC qrels: `qid 0 docid 1` |
| `evidence_review.md` | Reviewer packet with 721 short supporting excerpts |
| `manifest.json` | Release identity, file map, and checked statistics |

## Question schema

```json
{
  "record_id": "25",
  "question": "...",
  "answer": "...",
  "n_hops": 8,
  "hops": [
    {
      "clue": "...",
      "redundant": false,
      "supporting_doc_ids": ["shard_..."]
    }
  ],
  "source": "ours"
}
```

`redundant: true` marks a confirmatory clue that is not required once the other
constraints identify the answer. It remains grounded and is included for a
faithful decomposition of the original question.

## Qrel schema

`qrels.jsonl` has one evidence set per question:

```json
{"record_id":"25","question":"...","answer":"...","qrel":["shard_..."]}
```

`qrels_hops.jsonl` exposes the union construction:

```json
{
  "record_id": "25",
  "hops": [
    {"hop_id": 1, "clue": "...", "redundant": false, "qrel": ["shard_..."]}
  ]
}
```

For every question, the question-level qrel is exactly the union of its hop
qrels. Relevance is binary. The standard `qrels.trec` file assigns label `1` to
every released question-document pair.

## Evaluation use

Give an evaluated agent only the question text. Answers, hops, qrels, and review
snippets are evaluator-side artifacts and will leak the target reasoning chain.
The release contains plaintext evaluation answers; do not use it as training
data or include it in retrieval corpora.

ClimbMix document texts are not redistributed here. Retrieve the corpus through
the infrastructure described in [`pipelines/bcp/README.md`](../../pipelines/bcp/README.md).
