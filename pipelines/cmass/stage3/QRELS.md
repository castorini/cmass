# Qrels judging standard (Stage 3)

For every question Stage 2 accepted, the qrels are the corpus documents that state a fact one of its **live hops**
needs: the union, over the hops, of the documents supporting each hop. Phase 1 (the precision filter) reads every
pooled candidate in full and keeps it only for the hops it supports. Phase 2 (duplicate expansion) adds the exact
duplicates of what phase 1 kept, and the near duplicates that still hold their parent's supporting quote verbatim or
that a judge has verified on their own text.

Every command below is `python3 <pipeline>/stage3/qrels.py --set <set> <command>`, run in the run directory; the
workflow prompts give the exact prefix. All examples here are invented (the island nation of Oskeria and its demo
documents).

## What you judge

- **Live hops**: every hop of the accepted record that was not dropped in revision. That includes required and
  confirmatory hops, inclusion and exclusion hops, the hops the audit added (`A1`, `A2`, …) and the answer hop.
  `show` lists them with their type, role, the constraint they cover and the accepted evidence quotes.
- **Out hops** (`## OUT hop` in `show`): hops of clues removed in revision. A document that supports only an out hop is
  dropped with `out_hop_only`.
- **Derivation hops** (computational hops: a difference of two years, a count, a maximum): never judged and never
  cited. Their documents are those of the hops whose facts they compute over; cite those hops.
- **Accepted evidence**: the documents Stage 2 accepted for a hop, through its audit and human review, are fixed. They
  are in the qrels already and never appear in your chunks.
- Judge every candidate against **all** live hops of the question, not only the hop that brought it into the pool.

| file (under `qrels/<set>/`) | what |
|---|---|
| `inventory.jsonl` | per question: live hops, out hops, accepted evidence (fixed), Stage 1 cited documents, the pool with provenance |
| `standards/<qid>.json` | the hop standards, written **before** reading any candidate |
| `recall/<qid>.queries.json` | query families for the hops `show` marks "(needs recall)" |
| `work/<qid>/chunk_NN_a.md`, `_b.md` … | the candidates to judge, full text (corpus text: never commit) |
| `judged/<qid>/chunk_NN.json` | the verdicts, one file per chunk |

## Step 1: the hop standard (once per question, before reading any candidate)

Write `standards/<qid>.json`, one entry per live factual hop:

```json
{"qid": "demo-3", "hops": {
  "2": {"subject": "Ilsa Renvaar, composer; the Velmaran Conservatory",
        "full": "Renvaar founded the Velmaran Conservatory in 1874",
        "partial": ["Renvaar founded the Velmaran Conservatory, year not stated",
                    "the Velmaran Conservatory was founded in 1874, founder not named"],
        "reject": ["the Port Ilse Conservatory (founded 1880 by Davin Kesh): another conservatory",
                   "guides that list the Velmaran Conservatory without its founder or year",
                   "the Renvaar Prize and other things named after her"]}}}
```

- `subject`: the entity the hop is about, with the names and spellings documents use for it.
- `full`: what a document must state to support the whole hop.
- `partial`: the parts of a compound hop that count on their own (the entity with only the year, the event without
  the place). A document that states one of them is **kept** with `supports: "partial"`. A hop with one indivisible
  fact has `"partial": []`.
- `reject`: the near misses you expect: same name and a different entity, a related but different fact, the topic
  without the fact, another candidate's facts that belong to its own hop.

Base it on the clue, the constraint the hop covers and the accepted evidence quotes (`show`). Then `std-check <qid>`
must print 0 errors. Do not change a standard to fit a document you have read; if one turns out wrong, say so in your
note.

## Step 2: recall (only for hops `show` marks "(needs recall)")

Write a query family per such hop to `recall/<qid>.queries.json`:

```json
{"hops": {"A1": {"queries": ["Selm river course", "Selm Mount Corran Hadley", "Selm joins Ardune below Port Ilse"],
                 "anchors": ["Selm"]}}}
```

- 4 to 8 queries (at least 3): the clue's key fact, the subject's aliases and spellings, paraphrases, the fact phrased
  the way a web page would state it. Short BM25 queries of 3 to 8 content words work best.
- `anchors`: strings at least one of which **any** supporting document must contain (case-insensitive): the subject's
  names and spellings. Too narrow loses documents; when unsure, add the variant or leave the list empty.

`recall <qid>` runs each query deep (100 hits by default), fuses each hop's lists with reciprocal rank fusion
(k = 60), drops documents with no anchor and keeps the top `recall_k` per hop as candidates.

## Step 3: judge

Read every part of your chunk in full. Read each whole document before deciding: the supporting sentence is often
far from the first screen, and a fact may be split over two sentences (the entity in one, the date in the next).

**Keep** a document when it states, about the hop's subject, a fact the hop needs: the whole hop (`full`) or one part
of a compound hop listed in the standard (`partial`). A fact counts when it is stated, or follows from what is stated
by one certain step (date arithmetic, a range check, a unit). It does not count when it needs outside knowledge the
document lacks.

**Drop** it otherwise, with one reason:

| reason | when |
|---|---|
| `no_hop_fact` | on the topic, but states no fact any live hop needs |
| `wrong_entity` | the fact is about a different entity (same or similar name, another person, another year's event) |
| `mention_only` | names the subject but states none of the hop's facts |
| `contradicts` | states the hop's fact with a different value (another year, another place) and nothing else a live hop needs |
| `out_hop_only` | supports only an out hop |
| `unusable` | the fact cannot be read from the text: garbled, or spun so the sentence no longer states it, or machine-generated text that gets the hop's facts wrong |

- **Attribution is the common failure.** On a page about several entities, the sentence that would support the hop
  often belongs to another subject. A guide that says "the Port Ilse Conservatory was founded in 1880" does not
  support "the Velmaran Conservatory was founded in 1874", however close the two sentences sit.
- A messy page (SEO spam, a scraped list, a forum, ads around the text) is **not** `unusable` when the sentence that
  states the fact is coherent: keep it on that sentence.
- When unsure, re-read the passage; if it still does not state the fact, drop it and say what is missing. Uncertain
  support is no support. Do not keep a document because it is on the topic, because an earlier pool listed it, or
  because the answer is well known.

Write `judged/<qid>/chunk_NN.json`:

```json
{"qid": "demo-3", "chunk": "01", "verdicts": [
  {"doc_id": "demo_doc_103", "keep": true,
   "hops": [{"hop": "2", "supports": "full", "quote": "The Velmaran Conservatory was founded in 1874 by the composer Ilsa Renvaar"},
            {"hop": "1", "supports": "full", "quote": "the composer Ilsa Renvaar, whose opera Lanterns of Morrowfen"}]},
  {"doc_id": "demo_doc_011", "keep": true,
   "hops": [{"hop": "2", "supports": "partial", "quote": "visit the conservatory founded by Ilsa Renvaar"}]},
  {"doc_id": "demo_doc_008", "keep": false, "reason": "mention_only",
   "note": "Names the Velmaran Conservatory without its founder or year; the founding it gives (1880, Davin Kesh) is the Port Ilse Conservatory's. Would keep if it named Renvaar as the founder."}]}
```

- One verdict per document in the chunk.
- `hops`: every live hop the document supports, each once, with one quote copied exactly from the document, at least
  15 characters. Line breaks in the chunk file are wrapping and do not matter; letters, case and punctuation do.
- `note` (drops): the deciding sentence (quoted) or what is missing, and the contrast: what would have made it relevant.

Then `check <qid> NN` must print 0 errors; fix and re-run until it does.

## Exclusion hops

An exclusion hop records why a candidate the wording could admit fails a constraint ("The Selm does not pass through
Velmaran", candidate Selm). Keep a document for it when it states the excluding fact **about that candidate**: its
course avoids the city, its date falls outside the range, it is a different kind of thing. A document that merely names
the candidate, or states what made it look plausible without the excluding fact, is `mention_only` or `no_hop_fact` for
that hop. A rival's facts belong to the rival's exclusion hop, never to the answer's inclusion hops.

## Set and aggregate questions

The answer is a list of members, a count, a maximum and so on, computed over a set. The live hops are of three kinds,
all judged the same way:

- **member hops**: one member's membership or its value for the attribute ("The Varrow rises in the Morrowfen
  hills"). A document counts only if it states that fact **about that member**; a document about another member
  supports that member's hop, not this one.
- **exclusion hops**: why a candidate is not counted ("The Selm rises on Mount Corran").
- **scope hops**: a document that fixes the set itself ("The island has three rivers longer than twenty kilometres").

In the standard, give each member or candidate the names documents use for it, and list as `reject` the other members
(their facts belong to their own hops), list pages naming the member without the fact, and namesakes. A document that
states facts for several members is kept for each hop it supports, with one quote per hop. The value must be the one the
hop needs (the figure for the year the question names, not a later estimate), unless the standard lists the other as
`partial`.

## Derivation hops

A derivation hop computes over other hops ("1880 - 1874 = 6 years" over the two founding years). No document is
judged for it and none may cite it (`check` rejects that): keep the documents that state its inputs for the input
hops. `verify` exempts derivation hops from needing a document, and checks that each input hop they list keeps one.

## Dataset rules

A set may carry a dataset-specific rule (for example, that hops are read as of the source's snapshot date). `show`
prints it as "Dataset rule: …"; it overrides the general rules above where they conflict.

## Second judge and adjudication

A stronger second judge re-judges a sample **blind**: never open the first judge's `chunk_*.json`, any
`second*_compare.json` or any adjudicated file. The sample (set by the run config) is every keep that rests only on
partial support, a share of the full keeps, a share of the drops (or all of them) and every dropped document Stage 1
cited; when the second judge turns `escalate_at` or more sampled drops into keeps, every remaining drop of the question
is re-judged in a further round. Write `judged/<qid>/second_NN.json` (round 1) or `secondN_NN.json` (round N) in the
verdict format, and run `second-check <qid> NN --round N`.

The adjudicator reads each disputed document in full and both verdicts, shown as `A` and `B` in an order that says
nothing about which judge wrote which, and decides under this standard; neither judge is privileged. It writes
`judged/<qid>/adjudicated_<part>.json` and runs `adj-check <qid> <part>`. Adjudicated verdicts replace the first
judge's.

## Phase 2: near duplicates

Exact duplicates of a kept document (byte-identical) are added without reading, with their parent's hops. Near
duplicates (5-gram word-shingle Jaccard ≥ 0.7) that hold one of their parent's quotes for a hop verbatim inherit that
hop without a judge (the quote check, `near_dup_mode` in [`README.md`](README.md)); the others are verified by a judge,
with every command run with `--phase2 N` and the files under `qrels/<set>/phase2/rN/`. Each document's header names
its parent, the hops the parent is kept for, the parent's quotes and whether each is verbatim in the copy. Use it to
check whether the copy gives the same supporting evidence, but judge the copy on **its own text**, against all live
hops, with the same verdict format, reasons and quote rules:

- A copy that lost the sentence its parent was kept for (a truncated mirror, a different edition) is dropped
  (`no_hop_fact`), however similar it is.
- A copy can support a hop its parent did not; keep it for that hop with its own quote.
- Every quote is copied from the copy, not from the parent.
