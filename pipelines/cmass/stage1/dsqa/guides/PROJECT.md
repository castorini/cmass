# Step 1, projection: decompose, ground, derive the answer, verdict

You PROJECT one DeepSearchQA problem onto the corpus under a STRICT standard. DSQA answers are a
Single Answer (one unique answer) or a Set Answer (the gold is a list). You decompose the causal-chain
problem into hops (one per information-bearing constraint), ground each hop in the FULL text of a corpus
document, derive the answer, and give a verdict. Tools: `TOOLS.md`.

## Input

The packet: `{record_id, problem, answer (gold), answer_type ('Single Answer' | 'Set Answer'), problem_category}`.

## Steps

1. **Decompose** the problem into its causal chain of hops: one hop per information-bearing constraint
   (entities, relations, filters, dates, quantities, superlatives). DSQA problems are "causal chains":
   each step depends on the previous; capture that order. Every piece of information in the problem
   must become a hop.

2. **Ground** each hop by deep retrieval (`cm.py search ... --hits 30` to `--hits 200`; `cm.py doc ... --max 1000000`).
   Try 3-8 query variants (aliases, paraphrases, bridge facts); READ candidate documents IN FULL.
   `grounded` is true ONLY IF a document's full text establishes the hop's fact (same entity and
   relation; not a keyword collision; not your own knowledge). Copy a verbatim snippet.
   Temporal qualifiers are non-disqualifying when the entities and relations are grounded; only explicit
   IN-TEXT dates count as date evidence (the corpus exposes no metadata).

3. **Derive the answer** from the grounded hops and compare it to the gold:
   - Single Answer: derive the one answer; `matches_gold` = derived equals gold (semantic match).
   - Set Answer (the gold is a LIST, often comma-separated): you do NOT need the whole list.
     `matches_gold` = AT LEAST ONE gold-list member is derivable from the grounded hops and
     corpus-grounded (any-one-answer). Completeness, no missing member and no extra member are NOT
     required. Pick the member you can most solidly ground and report it in `derived_answer`.

4. **Verdict.** `SUPPORTED` iff every QUESTION-CONSTRAINT hop is grounded AND `matches_gold` (Single:
   derived equals gold; Set: at least one gold-list member grounded). `PARTIAL` if the answer is
   reachable but some constraint hop is ungrounded. `NOT` if a decisive constraint is absent or
   contradicted. List any ungroundable clue in `failed_clues`.

5. **Write** the output.

## Output

```json
{"record_id": "<qid>", "answer_type": "<copied from the packet>",
 "all_hops_grounded": true, "matches_gold": true, "complete": false,
 "derived_answer": "<the answer, or the one grounded member of a set>",
 "verdict": "SUPPORTED | PARTIAL | NOT",
 "hops": [{"clue": "<one atomic fact>", "grounded": true, "doc_ids": ["<docid>"], "snippet": "<verbatim passage>"}],
 "failed_clues": ["<a clue no document grounds>"],
 "reason": "<one or two sentences>"}
```

Required fields: `record_id`, `answer_type`, `all_hops_grounded`, `matches_gold`, `verdict`; every hop
needs `clue` and `grounded`. `complete` (Set Answer only: no missing or extra member) is recorded but no
longer decides the verdict.
