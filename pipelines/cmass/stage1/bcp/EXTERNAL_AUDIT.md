# Independent-projection audit guide (BrowseComp-Plus Stage 1, step `external_audit`)

You AUDIT another author's projection of one BrowseComp-Plus question for a strict corpus-grounded benchmark.
The projection gives the question, the answer, and hops with the ClimbMix documents it cites. The other
author's projection tool and model are not recorded; treat every cited document id as a claim to check.

This is the prompt of the original `audit_sahel.js` (65 independent projections of PROJECTABLE questions that
our own chain had not kept: 11 passed) and `audit_sahel33.js` (the same check on 33 independent projections of
questions our answerability check had labelled PARTIAL or NOT: none passed), with the corpus command mapped
onto `pipelines/cmass/tools/cm.py`.

## Steps

0. If your output file already exists and `--check` prints OK, stop: do not redo the work.
1. `python3 STAGE1/bcp_state.py SET external_audit QID --input` writes the projection and prints its path; read
   it: `{qid, question, answer, hops: [{clue, corpus_doc_ids, snippets}]}`.
2. COVERAGE + CHAIN: judge whether the hops together (a) cover the question's stated constraints and (b) form a
   reasoning chain that leads from the question to the given answer. `all_hops_present = true` iff no necessary
   step is missing; `chain_ok = true` iff the hops logically pin the answer.
3. GROUNDING (retrieve; do not trust document ids): for EACH hop, fetch its cited ClimbMix documents:

   ```
   python3 TOOLS/cm.py doc --stage 1 --step external_audit --set SET --qid QID DOCID --max 200000
   ```

   Read the returned CONTENT and decide whether it actually states or supports the hop's clue: the same
   entities and fact; NOT merely on-topic, NOT a different person or place, NOT only a keyword match. A hop is
   `grounded = true` iff at least one cited document's retrieved content genuinely supports it. Record
   `doc_used` (the supporting document id, or "") and a one-line `why`. The presence of a document id is NOT
   sufficient.
4. `all_hops_grounded` = every hop grounded; `keep` = all_hops_present AND chain_ok AND all_hops_grounded.
   Write `stage1/<set>/external_audit/<qid>.json`, with one `per_hop` entry per input hop, in order:

```json
{"qid": "<qid>", "all_hops_present": true, "chain_ok": true, "all_hops_grounded": false, "keep": false,
 "reason": "<one line>",
 "per_hop": [{"clue": "<the input clue>", "grounded": false, "doc_used": "", "why": "<one line>"}]}
```

5. `python3 STAGE1/bcp_state.py SET external_audit QID --check` must print OK.

`STAGE1` is `pipelines/cmass/stage1/bcp`, `TOOLS` is `pipelines/cmass/tools`; run commands from the run
directory, one corpus command at a time (a failed request is not evidence). If you cannot finish, report that
you did not; never report success for a file you did not write.
