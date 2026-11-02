# Final gates guide (Stage 1, steps `extreme`, `completeness`, `coherence`, `annotator`)

Four gates, one agent per question per gate, applied in this order to every strict-audited question; a
question that fails a gate skips the rest. Each agent writes one file, `stage1/<set>/<gate>/<qid>.json`.
This is the guide the workflow `stage1_final_gates.js` gives each agent; its content is the prompt of the
real run (private repo `artifacts/trqa/final_pipeline/trqa_final_pipeline.js`, "the DSQA final filtering
pipeline adapted to aggregation questions"), with the corpus commands mapped onto this pipeline's `cm.py`
and its examples replaced by invented ones (the island nation of Oskeria). The gates filter and never
repair, except the annotator, which may fix a hop's snippet or clue.

## Tools

Run from the run directory; always `--stage 1 --step <gate> --set SET --qid QID`, where `<gate>` is your
gate's step name (`extreme`, `completeness`, `coherence`, `annotator`): each gate has its own search log, so
earlier passes' queries are not refused as near duplicates.

```
python3 S1/trqa_state.py input SET <gate> QID                                       # the question and its hops
python3 TOOLS/cm.py doc    --stage 1 --step <gate> --set SET --qid QID DOCID --max 1000000      # a document IN FULL
python3 TOOLS/cm.py search --stage 1 --step <gate> --set SET --qid QID "<keywords>" --hits 40 --chars 300 --center
python3 TOOLS/cm.py quote  DOCID "first words" "last words"                         # an exact span
python3 TOOLS/cm.py verify DOCID "<snippet>"                                        # is it verbatim?
python3 S1/trqa_state.py check SET <gate> QID                                       # 0 errors before you finish
```

The real run's commands were `cm.py doc <docid>` and `cm.py search "<keywords>" 40 300` (gate 1) or
`... 60 300` (gate 2): hits, then preview characters, previews centered on the query terms (`--center`). The input (`trqa_state.py input`) is
`{record_id, subset, question, gold_answer, operator, target_attribute, entity_set_description,
hops: [{index, kind, clue, doc_ids, snippet}]}`. Gates 2 to 4 also read the extreme gate's file
`stage1/<set>/extreme/<qid>.json` (its re-grounded hops are the verified ones). If you cannot finish,
write nothing and report `completed=false` with the reason: the workflow checks the file on disk.

## The shape of these questions (every gate)

TotalRecallQA questions are AGGREGATION questions, not lookup questions. The answer is a NUMBER produced by
an operator (count / min / max / average / sum / median / range) over a COMPLETE set of entities that
satisfy the question's filters. Two consequences you must honour:

1. **Completeness is load-bearing.** The any-one-member waiver used for DeepSearchQA set answers DOES NOT
   APPLY HERE. A single qualifying entity missing from the set can change the answer, so an incomplete set
   is a WRONG answer, not a partial one.
2. **Hops come in two kinds.** An EVIDENCE hop asserts a fact about the world (entity X is in the set;
   entity X's attribute = V) and MUST be established by the full text of a corpus document. A DERIVATION
   hop performs arithmetic or logic over values that this question's own evidence hops already establish;
   it legitimately cites no document.

   A derivation hop that asserts a FACT about the world rather than computing over established values is
   an UNGROUNDED EVIDENCE HOP in disguise. Examples of this exact failure: "COMPLETENESS FLOOR: no
   lighthouse of Oskeria is shorter than 10 metres", or "BRANCH BOUND: any conservatory founded in the
   1870s has a founding year >= 1870" used to dismiss a whole branch without enumerating it, or any claim
   that a set contains nothing further. Those are world-claims and need documents.

## Gate 1: extreme audit (`extreme`)

An EXTREME-STRICTNESS grounding audit. The bar: EVERY meaningful phrase and word of the question must be
represented by a hop, and EVERY evidence hop must be grounded by the full text of a corpus document. You
FILTER, never repair: an uncovered atom or an ungrounded hop fails the question. Verify everything
yourself; trust nothing recorded. The recorded snippets were written to a loose convention (composites,
commentary, fragments joined with '|'): do NOT treat them as evidence, and do not reuse them.

1. Read the input.
2. **Atomize** the question text. List every information-bearing atom: the set-defining noun and EVERY
   filter on it (colour, category, nationality, region, price bound, date range, relation to another
   entity), the target attribute, its UNIT of measure, any temporal qualifier, and any source attribution.
   If a phrase carries two facts, split it.
   - EXEMPT from atomization, do NOT record these as uncovered: pure function words; bare "as of <year>"
     timestamps; and THE OPERATOR ITSELF (min, max, count, average, median, range, ratio, ordering words).
     The operator is the question's own instruction to the solver, not a claim about the world, so it needs
     no hop and no document. Whether the operator applied to the verified values actually yields the gold
     number is recomputed by a later gate; it is not your test, and a question must never fail here for
     lacking an operator or aggregation hop.
   - Map each atom to the hop index representing it; an atom no hop represents gets hop_index 0 and goes in
     `uncovered_atoms`.
   - SET CLOSURE is likewise NOT your test: whether the enumerated entities are ALL the entities satisfying
     the filters is attacked by a dedicated adversarial completeness gate that runs after you. Do not fail a
     question for lacking a closure hop. What you DO test is that every filter in the question is
     represented by a hop for each entity that IS enumerated: a member admitted without its colour, region,
     date or price filter established is an uncovered atom.
   - CALIBRATION, real failures from human annotation of the BCP and DSQA benchmarks; treat every analogous
     case as a failure: a named source no document attributes the data to; a date or year qualifier absent
     from the corpus; a bare count word ("twice") with no document; a distance figure with no document; a
     bridging step that was never a hop at all; a unit of measure the documents never state.
3. **Re-ground every evidence hop from scratch.** Fetch each cited document IN FULL. `grounded=true` ONLY IF
   a document's own text states that hop's fact: the SAME entity (a similarly-named or related entity fails;
   a county in one state cited for a same-named county in another is a real failure that was caught),
   numbers and dates IN the text, relations stated explicitly. Never inferred, never from your own
   knowledge, never keyword overlap.
   - UNITS: if the hop asserts a value in a unit, the document must state that value in that unit or state a
     conversion. A bare number with the unit supplied by you is ungrounded.
   - Write a FRESH verbatim snippet (at most 280 characters) copied exactly from the document for every hop
     you pass (take it with `cm.py quote`), and keep in `doc_ids` only the documents that genuinely ground
     it; put the single best one in `doc_id`. If elision is needed use " ... " between the kept fragments.
   - If every cited document fails a hop you may run at most 4 rescue searches (`--hits 40 --chars 300 --center`) and
     read promising hits IN FULL. A genuine replacement grounds the hop (note it). Otherwise
     `grounded=false` and the index goes in `ungrounded_hops`.
4. **Check each derivation hop.** It is legitimate only if it computes over values this question's evidence
   hops establish. If it asserts a world-fact (a bound, a floor, an absence, a membership claim, a
   set-closure claim), set its `grounded=false` and list it in `miskinded_hops` AND in `ungrounded_hops`.
5. `keep` = `all_atoms_covered` AND `all_hops_grounded`. Write `stage1/<set>/extreme/<qid>.json`:
   `{qid, all_atoms_covered, all_hops_grounded, keep, atoms: [{atom, hop_index}], hops: [{index, kind,
   grounded, doc_id, doc_ids, snippet, clue, note}], uncovered_atoms, ungrounded_hops, miskinded_hops,
   reason}`. `reason`: one or two sentences naming exactly what failed, or "clean pass". Every input hop
   index appears in `hops`.

## Gate 2: adversarial completeness (`completeness`)

You attack ONE question's entity set. Every hop already passed individual grounding. You are testing
something else: whether the set is COMPLETE with respect to the corpus. Your job is to FIND A MEMBER THEY
MISSED.

1. Read the input and the extreme gate's file (use its hops, not the input's snippets). Write down the
   enumerated members and the operator.
2. SEARCH HARD for qualifying entities that are NOT in the enumerated set. Run AT LEAST 8 and up to 20
   distinct searches (`--hits 60 --chars 300 --center`). Vary the angle; do not issue paraphrases of one query.
   Use: the set-defining noun with each filter; synonyms and plural/singular forms of the category; the
   attribute term with typical values; list-shaped phrasings ("list of", "top 10", "best", "all"); sibling
   entities of members you already have; the filter value alone within the category; and, where the set is
   geographic or organisational, the parent entity's roster. For the operator, target the region that
   matters: for min, hunt for a SMALLER value; for max, a LARGER one; for count / sum / average, ANY
   additional member counts.
3. For every candidate, fetch the document IN FULL and verify BOTH that it satisfies every filter of the
   question AND its attribute value, from the document's own text. A candidate you cannot verify from full
   text is not a finding: discard it rather than reporting it.
4. Record each verified extra member in `extra_members` with `entity`, `why_qualifies`, `doc_id`, a verbatim
   `snippet`, `attribute_value`, and `changes_answer` (does adding it change the gold answer under this
   operator?). `set_complete` = no verified extra member exists. `aggregate_unchanged` = no verified extra
   member changes the answer. `recomputed_answer` = the answer after adding every verified extra member.
5. `keep` = `set_complete` OR `aggregate_unchanged`. Report `searches_run` honestly. If you ran fewer than 8
   searches, keep=false is NOT justified on completeness grounds; say so in `reason`. Write
   `stage1/<set>/completeness/<qid>.json`: `{qid, set_complete, aggregate_unchanged, keep, searches_run,
   extra_members, recomputed_answer, reason}`.

## Gate 3: coherence and recomputation (`coherence`)

You judge whether ONE question's hops COHERE into a justification of its numeric answer. Every hop already
passed an individual grounding check and the set survived a completeness attack. That is NOT what you are
testing. You are testing whether the hops, read TOGETHER, use CONSISTENT content and actually produce the
gold number. Fetch documents in full when you need to.

1. Read the input and the re-grounded hops in the extreme gate's file.
2. **Cross-hop consistency.** Read the verified snippets together and check they describe the same thing:
   - unit / measure: do members' attribute values mix units (cups vs litres, km vs miles, days vs years,
     nominal vs real currency)? The aggregate is meaningless across mixed units.
   - attribute definition: is the SAME sense of the attribute used for every member (usable capacity vs
     total capacity; metro population vs city population; list price vs sale price)?
   - reference year / vintage: does the question fix a year while members' values come from different years
     or editions?
   - entity: do hops silently switch between similar entities (a city vs its metro area, a county in one
     state vs the same-named county in another, a company vs its subsidiary)?
   - scope: do the membership hops apply the filters to different populations?
   Any mismatch the question's logic depends on => `cross_hop_consistent=false`.
3. **Recompute.** Apply the operator to the verified attribute values of the verified members.
   `recomputed` = that value; `gold` = the gold answer. `aggregate_matches_gold` = they agree numerically
   (allow ordinary rounding, not a different value).
4. **Attribution.** If the question names a source, the hop grounding that source must license THE VERY
   DATA the other hops read: the same table, dataset or publication. A credit that sits in a different
   edition than the figures used, or a document that merely mentions the organisation in passing, is
   `attribution_ok=false`. If the question names no source, `attribution_ok=true`.
5. **Joint derivation.** Do the hops as evidenced actually lead to the gold number, with no step left to
   inference? If a step is missing, `jointly_justifies=false`.
6. Record every failure in `failures` with a short `pattern` name (reuse where they fit: unit-mismatch,
   attribute-definition-drift, year-mismatch, edition-mismatch, entity-drift, scope-mismatch,
   derivation-gap, attribution-not-for-data, aggregate-mismatch) and a one-sentence `detail`. Anything none
   of those names fit goes in `new_pattern`. `keep` = `cross_hop_consistent` AND `aggregate_matches_gold`
   AND `jointly_justifies` AND `attribution_ok`. Be strict: this decides a released benchmark. Write
   `stage1/<set>/coherence/<qid>.json`: `{qid, cross_hop_consistent, aggregate_matches_gold,
   jointly_justifies, attribution_ok, keep, recomputed, gold, failures, new_pattern, reason}`.

## Gate 4: annotator pass (`annotator`)

You are the human annotator for a corpus-grounded benchmark, reviewing ONE question end to end. Read it the
way a careful person would who has never seen it before, and who will not accept hand-waving. Fetch any
document in full when something does not add up.

1. Read the input and the re-grounded hops in the extreme gate's file (use those hops, doc_ids and
   snippets: they are the verified ones).
2. For EACH hop ask, in order:
   - (a) Does the hop make sense as a step for THIS question: a real constraint the question states,
     phrased so a reader knows what to check? A clue that is vague, mislabels what it checks, or simply
     restates the answer is a problem. For a membership hop the reader must be able to see WHICH filter it
     satisfies; for an attribute hop, WHICH attribute and in what unit.
   - (b) Does the snippet plainly show the hop is true? A reader should see the entity, the relation, and
     any number / date / unit in the passage itself. If they must infer, squint at a table fragment, or
     already know the answer, it is inadequate.
   - (c) If (a) or (b) fails, INVESTIGATE: read the hop's verified documents in full.
     - A better passage in those documents => `verdict="fix"` with the improved snippet (verbatim,
       self-contained, starting at a sentence boundary, at most 300 characters) and its `doc_id`.
     - The clue WORDING is the problem but the evidence is sound => `verdict="fix"` with a clearer clue
       saying exactly what the reader must check. Never broaden or weaken what the hop asserts.
     - No passage in the verified documents genuinely establishes the hop => `verdict="drop"`.
     - Otherwise `verdict="keep"`, repeating the existing clue / doc_id / snippet unchanged.
3. Step back and read the question with its hops as a whole. Record anything that would make you hesitate
   in `issues` with a short `kind` and a one-sentence `detail` (a hop that only restates the answer; two hops
   that are really the same check; a filter of the question no hop tests; a snippet supporting a
   neighbouring fact; evidence technically right but misleading; a members list a reader would doubt is
   complete).
4. `keep` = every hop ends at "keep" or "fix". If ANY hop is "drop", keep=false. Write
   `stage1/<set>/annotator/<qid>.json`: `{qid, keep, hops: [{index, verdict, clue, doc_id, snippet, problem,
   fix_note}], issues, reason}`. Every hop index must appear.

A fix is applied later only if `build_final.py` finds its snippet verbatim in a document the extreme gate
verified for that hop; otherwise the hop keeps the extreme gate's evidence.
