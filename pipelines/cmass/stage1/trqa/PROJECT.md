# Projection guide (Stage 1, step `project`)

You assess whether one Total Recall QA (TRQA) question can be **answered from the corpus**, and write
one file: `stage1/<set>/project/<qid>.json`. This is the guide the workflow `stage1_project.js` gives
each agent; its content is the prompt of the real run (private repo `artifacts/trqa/tools/trqa_project_chunk.js`),
with the corpus commands mapped onto this pipeline's `cm.py`.

TRQA questions are **aggregation** queries: they define a **set** of entities by constraints, then
apply an operator (count / sum / average / median / max / min / ...) over a target attribute, producing
one verifiable value. Answering **requires total recall**: you must recover the **complete** entity set
and each member's target attribute from the corpus.

Examples below use the invented island nation of Oskeria and invented documents (`demo_trqa_NNN`).

## Tools

Run every command from the run directory (it holds `cmass.json`). `TOOLS` is `pipelines/cmass/tools`,
`S1` is `pipelines/cmass/stage1/trqa`. Always pass `--stage 1 --step project --set SET --qid QID`: every
search is logged to this step's own search log (`stage1/<set>/project/<qid>.searchlog.jsonl`), and `search
--center` shows each hit's preview around its densest window of query terms, as the real run's CLI did.

```
python3 S1/trqa_state.py input SET project QID                                    # your task (question, gold answer, ...)
python3 TOOLS/cm.py search  --stage 1 --step project --set SET --qid QID "<query>" --hits 30 --chars 600 --center
python3 TOOLS/cm.py search  --stage 1 --step project --set SET --qid QID "<query>" --hits 100 --chars 600 --center --force   # deeper
python3 TOOLS/cm.py doc     --stage 1 --step project --set SET --qid QID DOCID --max 1000000      # a whole document
python3 TOOLS/cm.py doc     --stage 1 --step project --set SET --qid QID DOCID --grep "<phrase>"  # read around a phrase
python3 TOOLS/cm.py history --stage 1 --step project --set SET --qid QID                          # queries already run
```

- The real run's CLI was `cm.py search "<query>" [hits] [preview]` (default 30 hits, 600-character
  previews centered on the densest query-term match) and `cm.py doc <docid>` (the whole document): the
  commands above are its equivalents (`doc` prints 6,000 characters unless you pass `--max`).
- `search` refuses a query too close to one already run for the question. Change the words; to rerun
  the same query deeper (escalating 30 -> 100 -> 200 hits) pass `--force` with the larger `--hits`.
- BM25 takes keywords. Use the corpus only: your own knowledge may suggest queries, never values.
- The corpus service is shared: one command at a time. A failed request is not evidence; wait and rerun.

## Steps

1. **Read the task**: `python3 S1/trqa_state.py input SET project QID` prints the question, the answer
   (the **gold** value), datatype and subset.
2. **Parse** the question into: (a) the entity-set definition (population + filtering constraints);
   (b) the aggregation operator; (c) the target attribute. Estimate the expected entity-set size if
   determinable.
3. Using corpus BM25 **only**, (a) **enumerate** the entity set (find the members satisfying the
   constraints) and (b) for each member **retrieve** the target attribute, capturing a grounding doc_id +
   snippet. Issue **many** expanded queries; escalate hits 30 -> 100 -> 200. This is total recall: try
   hard to find **every** member, not just a few.
4. **Compute** the aggregate from what you recovered and compare it to the gold answer (allow rounding).
5. **Verdict**:
   - `SUPPORTED`: you recovered the **complete** entity set and each target attribute from the corpus,
     **and** the computed aggregate **matches** the gold value.
   - `PARTIAL`: you recovered some but not all of the set or attributes (incomplete recall), or the
     computed value is close but does not match because members are missing.
   - `NOT`: the entity set and/or attributes are largely absent from the corpus; the answer cannot be
     derived.
6. **Write** `stage1/<set>/project/<qid>.json` (the path the workflow gives you):

```json
{"record_id": "<qid>", "subset": "<subset>", "verdict": "SUPPORTED | PARTIAL | NOT",
 "operator": "count", "target_attribute": "...", "entity_set_description": "...",
 "n_entities_expected": 3, "n_entities_recovered": 3, "computed_value": "3", "gold_answer": "<the task's answer>",
 "matches_gold": true, "recall_complete": true,
 "evidence": [{"entity": "<member>", "attribute_value": "<its value>", "doc_id": "demo_trqa_001"}],
 "rationale": "..."}
```

Every field is required (`python3 S1/trqa_state.py check SET project QID` must report 0 errors). Then
return the structured summary the workflow asks for. If you cannot finish (a tool fails, the corpus is
unreachable), write nothing and report `completed=false` with the reason: the workflow checks the file on
disk, never your report.
