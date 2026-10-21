# Near-miss repair guide (BrowseComp-Plus Stage 1, step `repair`)

You REPAIR one near-miss BrowseComp-Plus question for a strict corpus-grounded benchmark: a PROJECTABLE question
whose hops are all grounded except one or two (phase A: one, or an independent projection's near miss; phase B:
two). Your job is ONLY the hops marked FAILING.

This is the prompt of the original `repair_nearmiss.js` (phase A: 65 targets, 5 claimed repairs, 4 kept after
verification; phase B: 84 targets, 1 claimed, rejected later), with the corpus commands mapped onto
`pipelines/cmass/tools/cm.py`. A claimed repair is not accepted on your word: the operator checks every fixing
document and decides (MANUAL.md, "repairs"), and the repaired question then goes through the coverage audit.

## Steps

0. If your output file already exists and `--check` prints OK, stop: do not redo the work.
1. `python3 STAGE1/bcp_state.py SET repair QID --input` writes the question and prints its path; read it:
   `{qid, phase, source, question, answer, hops: [{index, clue, grounded, doc_ids, prior_note, FAILING}]}`. Hops
   with `FAILING = true` lack ClimbMix grounding; all others are already grounded.
2. For EACH FAILING hop, search ClimbMix iteratively:

   ```
   python3 TOOLS/cm.py search --stage 1 --step repair --set SET --qid QID "<query>" --hits 30 --chars 600 --center
   python3 TOOLS/cm.py doc    --stage 1 --step repair --set SET --qid QID DOCID --max 200000
   ```

   Try 4-8 varied queries: synonyms, entity aliases, alternate phrasings, the bridge or component fact (a date,
   a name pair), broader then narrower. Escalate hits to 100-200 if needed (`--force` reruns a logged query
   deeper; previews are centred on the query match). Read promising documents IN FULL. A hop is FIXED only if a document's retrieved CONTENT genuinely
   states or establishes the hop's fact about the SAME entity: not a keyword collision, not merely on-topic, not
   your own knowledge. Copy a VERBATIM snippet from the document (`cm.py quote DOCID "first words" "last words"`).
   The `prior_note` may say why earlier searches failed: use it to avoid repeating dead ends, but re-check
   honestly, because earlier passes sometimes just missed the document.
3. `repaired_all` = every FAILING hop fixed. If a hop cannot be fixed after honest effort, put it in
   `unfixable` with a one-line `why` and stop spending on it.
4. Write `stage1/<set>/repair/<qid>.json`, naming each FAILING hop by its `index` in `fixed` or `unfixable`
   (a hop fixed by two documents gets two `fixed` entries):

```json
{"qid": "<qid>", "repaired_all": false,
 "fixed": [{"index": 1, "clue": "<the hop's clue>", "doc_id": "<docid>", "snippet": "<verbatim>"}],
 "unfixable": [{"index": 2, "clue": "<the hop's clue>", "why": "<one line>"}]}
```

5. `python3 STAGE1/bcp_state.py SET repair QID --check` must print OK.

`STAGE1` is `pipelines/cmass/stage1/bcp`, `TOOLS` is `pipelines/cmass/tools`; run commands from the run
directory, one corpus command at a time (a failed request is not evidence). If you cannot finish, report that
you did not; never report success for a file you did not write.
