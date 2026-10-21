# Hop scrutiny guide (BrowseComp-Plus Stage 1, steps `scrutiny1` and `scrutiny2`)

Two independent passes re-check every hop of every PROJECTABLE question against ClimbMix. Each pass is a fresh
agent per question that sees only the question, the answer and the hop list (the full decomposition the
projector wrote), never the projector's evidence or the other pass. Pass 1 is the original
`scrutinize_hops.js`, pass 2 the original `scrutinize_hops_v2.js`; their prompts are reproduced below with the
corpus commands mapped onto `pipelines/cmass/tools/cm.py`. Do the section for your pass only.

What happens to your output: `bcp_build.py all-hops` grounds hop *i* when **either** pass found hop *i*
supported. Your `hops` list must therefore have exactly one entry per input hop, in the input order, even when
you think a hop is redundant or wrong. Your own `keep` flag is recorded but not used.

`STAGE1` is `pipelines/cmass/stage1/bcp`, `TOOLS` is `pipelines/cmass/tools`; run every command from the run
directory.

## Tools

```
python3 STAGE1/bcp_state.py SET STEP QID --input          # writes your input; read the printed path
python3 TOOLS/cm.py search  --stage 1 --step STEP --set SET --qid QID "<query naming the hop's entities + fact>" --hits 30 --chars 600 --center
python3 TOOLS/cm.py doc     --stage 1 --step STEP --set SET --qid QID DOCID --max 200000      # a whole document
python3 TOOLS/cm.py doc     --stage 1 --step STEP --set SET --qid QID DOCID --grep "phrase"
python3 TOOLS/cm.py quote   DOCID "first words" "last words"                                 # an exact span for the note
python3 TOOLS/cm.py history --stage 1 --step STEP --set SET --qid QID                        # your queries so far
python3 STAGE1/bcp_state.py SET STEP QID --check          # must print OK
```

`STEP` is `scrutiny1` or `scrutiny2`, your pass. `--step` gives your pass its own search log, so you start with
no history from the projector or the other pass. The input is `{qid, question, answer, n_hops, hops: ["<clue>",
...]}`. Search with `--hits 30`, then 100 or 200 for a hard hop (`--force` reruns a query of your own log deeper),
and `--center` (previews centred on the query match). Run one corpus command at a time; a failed request is not
evidence.

## Pass 1 (`scrutiny1`)

You SCRUTINIZE the hops of a BrowseComp-Plus question to decide if it qualifies for a strict benchmark: EVERY
hop must be (a) supported on the ClimbMix corpus and (b) necessary.

1. Read your input: question, answer, and `hops` (the full decomposition into atomic clues).
2. For EACH hop, check SUPPORT on ClimbMix (the answer may NOT be assumed; ground the hop's fact). Issue several
   expanded queries (synonyms, aliases, paraphrases); escalate hits; read promising documents in full.
   `supported = true` ONLY IF a specific ClimbMix document states or establishes the hop's fact (copy a verbatim
   snippet); NOT merely topical, NOT from your own outside knowledge.
3. For EACH hop, check NECESSITY: would the answer still be UNIQUELY determined by the OTHER hops if this hop
   were removed? `necessary = true` if removing the hop would let a different entity or value satisfy the
   remaining hops (it adds discriminating power); `necessary = false` (REDUNDANT) if the remaining hops already
   uniquely pin the answer without it.
4. Aggregate: `all_supported` = every hop supported; `all_necessary` = every hop necessary;
   `keep` = all_supported AND all_necessary.
5. Write `stage1/<set>/scrutiny1/<qid>.json`:

```json
{"qid": "<qid>", "n_hops": 0, "n_supported": 0, "n_necessary": 0,
 "all_supported": false, "all_necessary": false, "keep": false,
 "hops": [{"clue": "<the input clue>", "supported": false, "doc_ids": [], "necessary": true, "note": "<verbatim snippet and why>"}]}
```

## Pass 2 (`scrutiny2`)

You SCRUTINIZE a BrowseComp-Plus question for a STRICT corpus-grounded benchmark. A question QUALIFIES
(`keep = true`) iff every NECESSARY hop is corpus-supported on ClimbMix, AND the necessary hops are
COMPREHENSIVE: they jointly and uniquely determine the answer using ONLY the corpus (no missing discriminating
hop).

0. If your output file already exists and `--check` prints OK, stop: do not redo the work.
1. Read your input: question, answer, and `hops` (the full decomposition into atomic clues).
2. SUPPORT (grounding) for EACH hop on ClimbMix (the answer may NOT be assumed; ground the hop's own fact).
   Issue several expanded queries (synonyms, aliases, paraphrases); escalate hits; read promising documents in
   full. `supported = true` ONLY IF a specific ClimbMix document states or establishes the hop's fact (copy a
   verbatim snippet into `note`); NOT merely topical, NOT from your own outside knowledge.
3. NECESSITY for EACH hop: would the answer still be UNIQUELY determined by the OTHER hops if this hop were
   removed? `necessary = true` if removing it would let a different entity or value satisfy the remaining hops;
   `necessary = false` (REDUNDANT) if the remaining hops already uniquely pin the answer without it.
4. COMPREHENSIVENESS, no missing hop (judge the SET of NECESSARY hops together, not one at a time):
   - (a) COVERAGE: enumerate every distinguishing constraint stated in the QUESTION; verify each maps to at
     least one NECESSARY hop. Any question constraint with no corresponding hop means a hop is MISSING.
   - (b) UNIQUENESS (adversarial): treating ONLY the necessary hops as the constraints, SEARCH ClimbMix for a
     DIFFERENT entity or value that ALSO satisfies ALL of them. Issue real queries; actively try to find a
     competitor to the gold answer.
   - `comprehensive = true` ONLY IF every question constraint is covered by a necessary hop AND no alternative
     corpus entity satisfies all the necessary hops. Otherwise `comprehensive = false`, and `missing_fact` names
     the uncovered question constraint, or the competing entity you found.
5. Aggregate: `all_supported` = every hop supported; `all_necessary` = every hop necessary;
   `keep` = (n_necessary >= 1) AND (every NECESSARY hop is supported) AND comprehensive. Write
   `stage1/<set>/scrutiny2/<qid>.json`:

```json
{"qid": "<qid>", "n_hops": 0, "n_supported": 0, "n_necessary": 0,
 "all_supported": false, "all_necessary": false, "comprehensive": false, "missing_fact": "",
 "keep": false,
 "hops": [{"clue": "<the input clue>", "supported": false, "doc_ids": [], "necessary": true, "note": "<verbatim snippet and why>"}]}
```

## Both passes

Run `--check` until it prints OK. If you cannot finish (a refused tool, a corpus that keeps failing), report
that you did not finish and why; never report success for work you did not do.
