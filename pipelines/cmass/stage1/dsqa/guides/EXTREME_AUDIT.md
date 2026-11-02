# Step 4, extreme-strictness audit: every atom covered, every hop grounded

You perform an EXTREME-STRICTNESS grounding audit of one DeepSearchQA question that passed the strict
audit. The bar: EVERY meaningful phrase and word of the question must be represented by a hop, and EVERY
hop must be grounded by the full text of a corpus document. You FILTER, never repair: an uncovered atom
or an ungrounded hop fails the question. Verify everything yourself; trust nothing recorded.
Tools: `TOOLS.md`.

(The cross-check step, `xcheck`, uses this same guide unchanged.)

## Input

The packet: `{qid, answer_type, problem, gold_answer, derived_answer, hops: [{index, clue, doc_ids, snippet}]}`.

## Steps

1. **Atomize** the problem text. List every information-bearing atom: every entity, relation, action,
   event, date, year, month, number, count, interval, threshold, quantity, unit, superlative, ordering,
   location, nationality, source attribution ("according to X"), and qualifying adjective. If a phrase
   carries two facts, split it. Only pure function words and pure timestamp qualifiers of the form
   "as of <year>" are exempt. Map each atom to the hop (by index) that represents it. An atom no hop
   represents gets `hop_index` 0 and goes in `uncovered_atoms`.

   Calibration: failures that human annotators found in an earlier projected benchmark. Treat every
   analogous case as a failure here (the originals' specific values are replaced by invented ones):
   - a periodical central to the question was never named by any document;
   - no document dated an event to the decade the question states;
   - a count word in the question ("three times") had no supporting document;
   - a person's birth year, the year a marriage ended and how long it lasted were not in the corpus;
   - a dated role qualifier such as "as of March 1899" (a specific dated role, not a bare timestamp) was
     ungrounded;
   - a distance range such as "3-5 kilometres" was ungrounded;
   - a chain link (the lead author connecting two papers) was never a hop at all: a missing bridge atom
     means `all_atoms_covered` false.

2. **Re-ground every hop from scratch.** For each hop, fetch its cited documents IN FULL
   (`cm.py doc ... --max 1000000`). `grounded` is true ONLY IF a document's own text states the hop's
   fact: the SAME entity (a similarly named or related entity fails), dates, years and numbers IN the
   text, relations stated explicitly: never inferred, never from your knowledge, never keyword overlap.
   Copy a fresh verbatim snippet (at most 280 characters) for every hop you pass and keep only the
   `doc_ids` that genuinely ground it. If every cited document fails a hop, you may run at most 4 rescue
   searches (`cm.py search ... --hits 40 --chars 300`): read promising hits IN FULL; a genuine
   replacement document may ground the hop (note it). Otherwise `grounded` is false and the index goes in
   `ungrounded_hops`. For Set Answer questions the established any-one-member rule holds: hops ground the
   `derived_answer` member against every question constraint; completeness of the gold list is NOT
   required and is not an atom.

3. **Classify** each hop: `required` true if removing it would leave some atom uncovered or would no
   longer force the answer (it carries unique coverage or discrimination); `required` false
   (confirmatory) if every atom it covers is also covered by other hops and the remaining hops still
   force the answer. Classification never rescues a question: even confirmatory hops must be grounded.

4. `keep` = `all_atoms_covered` AND `all_hops_grounded`. Write the output. `reason`: one or two sentences
   naming exactly what failed, or "clean pass".

## Output

```json
{"qid": "<qid>", "all_atoms_covered": true, "all_hops_grounded": true, "keep": true,
 "atoms": [{"atom": "<phrase of the question>", "hop_index": 1}],
 "hops": [{"index": 1, "grounded": true, "required": true, "doc_ids": ["<docid>"],
           "snippet": "<fresh verbatim passage>", "note": "..."}],
 "uncovered_atoms": [], "ungrounded_hops": [], "reason": "clean pass"}
```

Give every hop of the packet (indices 1..n).
