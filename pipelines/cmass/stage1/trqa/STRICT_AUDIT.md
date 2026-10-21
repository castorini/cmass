# Strict audit guide (Stage 1, step `strict`)

You **audit** one Total Recall QA aggregation question that the projector marked `SUPPORTED`, under a
**strict** corpus-grounded standard, and write one file: `stage1/<set>/strict/<qid>.json`. This is the
guide the workflow `stage1_strict_audit.js` gives each agent; its content is the prompt of the real run
(private repo `artifacts/trqa/strict_audit/audit.js`, "the BCP-65 standard adapted to aggregation
questions"), with the corpus commands mapped onto this pipeline's `cm.py`.

An aggregation question passes **only if**:

1. every information-bearing constraint in the question is represented as a hop;
2. every entity membership **and** attribute value is grounded by **full** corpus document text;
3. the recomputed aggregate equals the gold answer;
4. the entity set is **complete** (adversarial check).

## Tools

As in `PROJECT.md`, run from the run directory, but with this step's own search log: always
`--stage 1 --step strict --set SET --qid QID` (the projector's queries are not your history).

```
python3 S1/trqa_state.py input SET strict QID                                  # your input
python3 TOOLS/cm.py doc    --stage 1 --step strict --set SET --qid QID DOCID --max 1000000   # read the FULL text
python3 TOOLS/cm.py search --stage 1 --step strict --set SET --qid QID "<query>" --hits 30 --chars 600 --center   # 30 to 200 hits
python3 TOOLS/cm.py quote  DOCID "first words" "last words"                                  # copy a verbatim span
```

The real run's commands were `cm.py doc <id>` and `cm.py search "<query>" [hits=30..200]` (600-character
previews centered on the query terms: `--chars 600 --center`). To rerun a query deeper pass `--force`.

## Steps

1. **Read your input** (`trqa_state.py input`): `{stem, question, gold_answer, operator, target_attribute,
   entity_set_description, computed_value, evidence: [{entity, attribute_value, doc_id}]}`, the
   projector's reading and evidence.
2. **Constraints -> hops.** Enumerate the question's constraints (set definition, filters, operator,
   attribute). Build hops: one **membership** hop per entity (the entity satisfies the set definition)
   and one **attribute** hop per entity where the operator needs a value (skip attribute hops for pure
   counts). Every question constraint must be enforced in these hops; set `constraints_covered`
   accordingly.
3. **Ground** each hop: fetch the cited document and **read the full text**; `grounded=true` **only if**
   it genuinely establishes that entity fact (same entity, same relation and value; not a keyword
   collision; not your own knowledge). If a cited document fails, search for a replacement (30 to 200
   hits) and note it; else the hop fails. Copy verbatim snippets. Temporal qualifiers are
   non-disqualifying when the entities and relations are grounded; only explicit **in-text** dates count
   as date evidence.
4. **Arithmetic**: recompute the operator over the **verified** attribute values (or the count of
   verified members); `aggregate_ok` = (recomputed == gold_answer, with numeric tolerance for floats).
5. **Completeness (adversarial)**: search the corpus for **additional** entities satisfying the set
   definition that are **missing** from the evidence (3 to 6 varied queries). `complete=false` if you find
   a genuine corpus-attested extra member that would change the aggregate; say so in `completeness_note`.
6. `keep` = `constraints_covered` AND `all_grounded` AND `aggregate_ok` AND `complete`. **Write**
   `stage1/<set>/strict/<qid>.json`:

```json
{"stem": "<qid>", "constraints_covered": true, "all_grounded": true, "aggregate_ok": true, "complete": true,
 "keep": true,
 "hops": [{"clue": "<member X satisfies the set definition>", "grounded": true,
           "doc_ids": ["demo_trqa_001"], "snippet": "..."},
          {"clue": "<the operator over the verified values>", "grounded": true, "doc_ids": [], "snippet": "..."}],
 "failures": [], "completeness_note": "...", "reason": "..."}
```

Keep every hop in this shape (`clue`, `grounded`, `doc_ids`, `snippet`): `build_strict_list.py` turns a
hop that cites documents into an evidence hop and one that cites none into a derivation hop, and stops
for a hand-written backfill when a kept question's hops lack a `clue`. Run
`python3 S1/trqa_state.py check SET strict QID` until it reports 0 errors, then return the structured
summary. If you cannot finish, write nothing and report `completed=false` with the reason: the workflow
checks the file on disk, never your report.
