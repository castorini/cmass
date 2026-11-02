# The four gates (HotpotQA, Stage 1)

Every contamination-clean bridge question passes four gates in order, one agent per question per
gate, and is dropped at the first gate it fails:

1. **Extreme**: atomize the question, decompose it into hops, re-ground every hop on full document text
   with a fresh verbatim snippet. This is where the hops are built: HotpotQA has none.
2. **Uniqueness**: an adversarial search for a rival answer the evidence does not exclude, and a check
   that the corpus supports the gold answer.
3. **Coherence**: the hops, read together, agree on entity, date and sense and jointly derive the answer.
4. **Annotator**: a careful reader's pass over each hop; fix or drop.

The gates FILTER. Only the annotator may propose repairs (a better snippet or clearer clue wording), and
`build_inputs.py` applies a repair only under the guard (below). The workflow (`gates.js`) gives you the
run directory, the set, the question id, the gate, its input file and its output file. This guide is the
instruction the original gates' agents followed, unchanged except for file locations and corpus commands.

## Question shape (every gate)

HotpotQA questions are BRIDGE or COMPARISON questions. A bridge question names entity A by description,
requires you to identify it, then asks a fact about it; a comparison question asks which of two named
entities has some property. The answer is a short string.

Two things follow:

1. The BRIDGE STEP IS A HOP. The step that gets you from the question's description to the entity it
   denotes must be grounded like any other fact. A question that silently assumes you already know which
   entity is meant has an ungrounded bridge.
2. UNIQUENESS MATTERS, completeness does not. Nothing is being aggregated, so there is no set to
   enumerate. What must hold is that the question's constraints, as the documents state them, pick out
   the gold answer and not some rival.

The documents cited for this question are the judged ones, but the corpus is far larger; you may search
it whenever the judged set is not enough (see "Corpus access").

## Corpus access (every gate)

From the run directory, with the `<set>`, `<qid>` and `<gate>` (extreme, uniqueness, coherence or annotator)
the workflow gives you:

| what | command |
|---|---|
| search the corpus (40 hits, 300-character previews centred on the query terms) | `python3 <tools>/cm.py search --stage 1 --step <gate> --set <set> --qid <qid> "<query>" --hits 40 --chars 300 --center` |
| the queries this gate already ran for this question | `python3 <tools>/cm.py history --stage 1 --step <gate> --set <set> --qid <qid>` |
| find a phrase in a document, with context | `python3 <tools>/cm.py doc --stage 1 --step <gate> --set <set> --qid <qid> <doc_id> --grep "<phrase>" --window 300` |
| read a document whole | `python3 <tools>/cm.py doc --stage 1 --step <gate> --set <set> --qid <qid> <doc_id> --max 1000000` |
| take an exact snippet | `python3 <tools>/cm.py quote <doc_id> "<first words>" "<last words>"` |
| check a snippet | `python3 <tools>/cm.py verify <doc_id> "<snippet>"` |

Documents are large: grep first, then read the regions that matter, and read a document whole when it
looks decisive. Run corpus commands one at a time. A failed request (an HTTP error, a timeout) is not
evidence: wait and run it again. `search` refuses a query too close to one this gate already ran for the
question: vary the angle, as the gates ask anyway. Every search is logged to this gate's own search log
for the question.

(The original agents read cached text files with `grep`; the `doc` commands above are the equivalents.)

## Gate 1: Extreme

You perform an EXTREME-STRICTNESS grounding audit of one HotpotQA question projected onto the corpus.
The bar: EVERY meaningful phrase of the question must be represented by a hop, and EVERY hop must be
grounded by the full text of a corpus document. You FILTER, never repair. Verify everything yourself;
trust nothing recorded.

**Step 1.** Read the gate input `stage1/<set>/gate_input/<qid>.json`: `{qid, question, gold_answers,
doc_ids, ...}`.

**Step 2. ATOMIZE the question.** List every information-bearing atom: each entity, each descriptor used
to identify an entity, every relation, date, year, number, place, nationality, occupation, superlative,
and qualifying adjective. If a phrase carries two facts, split it. Only pure function words are exempt.

DECOMPOSE the question into numbered hops, each one atomic fact, and map every atom to the hop that
represents it. An atom no hop represents gets `hop_index` 0 and goes in `uncovered_atoms`. Mark the
bridge hop with `is_bridge` true.

CALIBRATION - real failures from human annotation of the BCP, DSQA and TRQA benchmarks; treat every
analogous case as a failure: a named source no document attributes the data to; a date or year qualifier
absent from the corpus; a count word with no document; a bridging step that was never a hop at all; a
negative or exclusion claim ("X is not Y") that no document states, since absence of evidence is not
evidence of absence; a unit of measure the documents never state.

**Step 3. GROUND every hop against FULL document text.** `grounded` true ONLY IF a document's own text
states that hop's fact: the SAME entity (a similarly-named or related entity fails), numbers and dates IN
the text, relations stated explicitly. Never inferred, never from your own knowledge, never keyword
overlap.

Write a FRESH verbatim snippet (at most 280 characters) copied exactly from the document for every hop you
pass, and keep in `doc_ids` only the documents that genuinely ground it; put the single best one in
`doc_id`. If elision is needed use ` ... ` between kept fragments.

If the judged documents fail a hop, you may run up to 4 rescue searches against the corpus; a genuine
replacement grounds the hop (note it). Otherwise `grounded` false and the index goes in `ungrounded_hops`.

**Step 4.** `keep` = `all_atoms_covered` AND `all_hops_grounded`. Write `stage1/<set>/extreme/<qid>.json`:

```json
{"qid": "<qid>", "all_atoms_covered": true, "all_hops_grounded": true, "keep": true,
 "atoms": [{"atom": "...", "hop_index": 1}],
 "hops": [{"index": 1, "clue": "...", "grounded": true, "is_bridge": true, "doc_id": "<doc_id>",
           "doc_ids": ["<doc_id>"], "snippet": "...", "note": "..."}],
 "uncovered_atoms": [], "ungrounded_hops": [], "reason": "clean pass"}
```

`reason`: one or two sentences naming exactly what failed, or "clean pass". Hops are numbered 1, 2, 3, ...;
a grounded hop has `doc_id`, `doc_ids` (including `doc_id`) and `snippet`.

## Gate 2: Uniqueness

You attack ONE HotpotQA question's answer. Every hop already passed individual grounding. You are testing
something else: do the question's constraints, AS THE DOCUMENTS STATE THEM, single out the gold answer?
Your job is to FIND A RIVAL the evidence does not exclude.

**Step 1.** Read the gate input and the grounded hops in `stage1/<set>/extreme/<qid>.json`.

**Step 2. SEARCH for a rival.** Run AT LEAST 6 and up to 15 distinct searches against the corpus, varying
the angle rather than paraphrasing one query: the question's descriptors without the gold entity; sibling
entities of the same type; the identifying description alone; the attribute value alone; competing
entities with the same name.

A rival counts only if you VERIFY from full document text that it satisfies every constraint the question
states. A candidate you cannot verify is not a finding - discard it. `answer_unique` = no verified rival
satisfies all the constraints.

**Step 3. CHECK THE GOLD ANSWER ITSELF.** Does the corpus actually support it? `gold_supported` false if
the corpus contradicts the gold answer, or names a different value for the same question - a benchmark
item where a correct, thorough searcher would be marked WRONG is broken regardless of anything else.
Record what the corpus says in `corpus_answer`.

**Step 4.** `keep` = `answer_unique` AND `gold_supported`. Report `searches_run` honestly; fewer than 6 does
not justify concluding uniqueness. Write `stage1/<set>/uniqueness/<qid>.json`:

```json
{"qid": "<qid>", "answer_unique": true, "gold_supported": true, "keep": true, "searches_run": 8,
 "rivals": [{"candidate": "...", "why_not_excluded": "...", "doc_id": "<doc_id>", "snippet": "..."}],
 "corpus_answer": "...", "reason": "..."}
```

## Gate 3: Coherence

You judge whether ONE HotpotQA question's hops COHERE into a justification of its answer. Every hop
already passed individual grounding and the answer survived a uniqueness attack. That is NOT what you are
testing. You are testing whether the hops, read TOGETHER, use consistent content and actually reach the
gold answer.

**Step 1.** Read the gate input and the grounded hops in `stage1/<set>/extreme/<qid>.json`.

**Step 2. CROSS-HOP CONSISTENCY.** Read the verified snippets together and check they describe the same
things:

- entity: do two hops silently refer to different people, works or places sharing a name, or to an entity
  and a related but distinct one (a band vs a band member, a film vs its remake, a city vs its metro area)?
- date / vintage: does one hop's fact hold at a different time than another's, where the question depends
  on them holding together?
- sense: is the same sense of a role, title or relation used throughout (founder vs owner, writer vs
  performer, starred in vs appeared in)?
- the bridge: does the entity the bridge hop identifies actually match the entity the later hops describe?

Any mismatch the question's logic depends on => `cross_hop_consistent` false.

**Step 3. JOINT DERIVATION.** Do the hops, as evidenced, lead to the gold answer with no step left to the
reader's own knowledge? If a step is missing, `jointly_derives_answer` false.

**Step 4.** Report `multi_document`: do the verified hops genuinely rest on two or more distinct
documents? Record it; it does not by itself decide `keep`.

**Step 5.** Record `failures` with a short pattern name (reuse where they fit: entity-drift,
bridge-mismatch, date-mismatch, sense-drift, derivation-gap, answer-restated) and a one-sentence detail.
Anything none of those fit goes in `new_pattern`. `keep` = `cross_hop_consistent` AND
`jointly_derives_answer`. Be strict: this decides a released benchmark. Write
`stage1/<set>/coherence/<qid>.json`:

```json
{"qid": "<qid>", "cross_hop_consistent": true, "jointly_derives_answer": true, "multi_document": true,
 "keep": true, "failures": [{"pattern": "entity-drift", "detail": "...", "hops": [1, 2]}],
 "new_pattern": "", "reason": "..."}
```

## Gate 4: Annotator

You are the human annotator for a corpus-grounded benchmark, reviewing ONE HotpotQA question end to end.
Read it the way a careful person would who has never seen it before, and who will not accept
hand-waving.

**Step 1.** Read the gate input and the grounded hops in `stage1/<set>/extreme/<qid>.json` (use those hops,
doc_ids and snippets - they are the verified ones).

**Step 2.** For EACH hop ask, in order:

- (a) Does the hop make sense as a step for THIS question - a real constraint the question states, phrased
  so a reader knows what to check? A clue that is vague, mislabels what it checks, or restates the answer
  is a problem.
- (b) Does the snippet plainly show the hop is true? A reader should see the entity, the relation, and any
  number, date or name in the passage itself. If they must infer, squint at a fragment, or already know
  the answer, it is inadequate.
- (c) If (a) or (b) fails, INVESTIGATE by reading the hop's verified documents in full.
  - Better passage in those documents => verdict "fix" with the improved snippet (verbatim,
    self-contained, starting at a sentence boundary, at most 300 characters) and its doc_id.
  - Clue WORDING is the problem but the evidence is sound => verdict "fix" with clearer wording. Never
    broaden or weaken what the hop asserts.
  - No passage in the verified documents genuinely establishes the hop => verdict "drop".
  - Otherwise verdict "keep", repeating the existing clue/doc_id/snippet unchanged.

**Step 3.** Step back and read the question with its hops as a whole. Record anything that would make you
hesitate in `issues` (a hop that only restates the answer; two hops that are really the same check; a
constraint of the question no hop tests; a snippet supporting a neighbouring fact; evidence technically
right but misleading; a question whose wording is ambiguous enough that a careful reader could answer
differently).

**Step 4.** `keep` = every hop ends at "keep" or "fix". If ANY hop is "drop", `keep` false. Write
`stage1/<set>/annotator/<qid>.json`; every hop index of the extreme audit must appear:

```json
{"qid": "<qid>", "keep": true,
 "hops": [{"index": 1, "verdict": "keep", "clue": "...", "doc_id": "<doc_id>", "snippet": "...",
           "problem": "", "fix_note": ""}],
 "issues": [{"kind": "...", "detail": "...", "hops": [1]}], "reason": "..."}
```

**The guard** (applied later by `build_inputs.py`, not by you): a "fix" is taken only when its `doc_id` is
one the extreme audit verified for that hop and its snippet occurs verbatim across those documents;
otherwise the hop keeps the extreme audit's evidence.

## After writing

Run `python3 <stage1>/hpqa_state.py check <set> <qid> <gate>` and fix every ERROR until it reports 0
errors. Warnings name snippets the check could not find verbatim in a cached document, and length limits.
