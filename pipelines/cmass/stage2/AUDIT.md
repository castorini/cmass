# Audit guide (C2-C3)

You audit one projected question against the corpus. You decide which constraints the question
states, which hops establish each one, whether every hop is grounded in a corpus document read in
context, which other answers are plausible and why each fails, whether the answer is right, unique
and (for a set or aggregate) complete, and you record all of it in one JSON file:
`work/<set>/<qid>.audit.json`. Its format is in `SCHEMA.md` ("Audit"); `validate.py` enforces it.

The proposed hops are provisional. Treat the question as a claim to check, not a text to defend: a
false premise, a malformed or ambiguous wording, or a wrong or stale answer is a finding to report,
never something to read charitably in favour of the supplied answer.

Examples below use an invented island nation, Oskeria, and its invented documents (`demo_doc_NNN`).

## Tools

Run every command from the run directory (it holds `cmass.json`). `TOOLS` is `pipelines/cmass/tools`.

```
python3 TOOLS/packets.py SET QID                                    # (re)build your packet
python3 TOOLS/cm.py search  --set SET --qid QID "words" --hits 50   # BM25 search, logged for the question
python3 TOOLS/cm.py search  --set SET --qid QID "words" --hits 50 --only "phrase"   # deep sweep: hits containing a phrase
python3 TOOLS/cm.py doc     --set SET --qid QID DOCID --grep "phrase" ["other phrase"]  # read around phrases
python3 TOOLS/cm.py doc     --set SET --qid QID DOCID --max 200000                      # read a whole document
python3 TOOLS/cm.py quote   DOCID "first words of the span" "last words of the span"   # exact span as JSON
python3 TOOLS/cm.py verify  DOCID "quote"                                               # is it verbatim?
python3 TOOLS/cm.py history --set SET --qid QID                     # every query already run for the question
python3 TOOLS/validate.py SET QID                                    # check your audit file
```

- Always pass `--set` and `--qid` to `search` and `doc`: the search log becomes part of the record.
  `search` refuses a query too close to one already run for the question: change the words.
- BM25 takes keywords; quotes and Boolean operators do nothing. Reformulate: entity names with the
  distinctive relation words, then broader or narrower, synonyms, other spellings, other date formats.
- The corpus service is shared: run one command at a time. A failed request (an HTTP error, a timeout)
  says nothing about the corpus; wait and run it again. Only a search that returned results can show
  that something is missing.
- Never cite source-benchmark evidence (`hints`), your own knowledge or a web page as evidence. They
  may suggest what to search for; only corpus documents ground facts.

## Inputs

`packets/<set>/<qid>.md` gives the question and answer of the version to audit (with the source
wording beside a revised version), the set's dataset rules, the input check (C1), the revision history,
and every hop with its clue, snippet and cited documents. For each cited document it says whether each
snippet fragment occurs in it (`exact`, `ws` for whitespace differences, `ci` for case differences,
`MISSING`) and shows a context window. **MISSING does not mean unsupported**: snippets often differ by
punctuation, dashes or line breaks. Read the document (`cm.py doc --grep`) before rejecting it. Hops
without a snippet show the window with the most clue terms, as a hint only.

## The shared requirements

These hold at every step, for every version of the question, and decide the verdict.

**Grounding and consistency.** Every factual constraint and factual hop is supported by corpus
evidence read in its full-document context. The evidence must establish the intended entity, relation,
value and qualifiers; matching names alone do not establish identity. A date restriction needs
evidence for the specified period. A clause such as "according to X" needs attribution to X. Hops must
agree on entity identity, edition, date, unit and scope; a conflict that affects the answer stays
unresolved until the evidence settles it. Computations use grounded inputs.

**Answer validity, uniqueness and completeness.** A single answer is uniquely supported; a set
contains every qualifying member and no non-member; an aggregate also has correct values and a correct
computation. A requested ordering is verified. Every plausible candidate found is recorded as
qualifying, or excluded by a grounded fact showing which constraint it fails. Retrieval failure
establishes neither inclusion nor exclusion.

**Question quality.** No false premise, malformed wording, ambiguity, or incorrect or stale answer
passes. A question that one document answers on its own is contaminated (Step 5).

## Step 1: constraints

List the question's constraints as **verbatim, non-overlapping spans of the question**, in order.
A constraint is any information-bearing phrase that narrows the answer:

- entity descriptions and relations ("the capital of the island nation of Oskeria", "runs through");
- dates, ranges and temporal qualifiers ("in the 1890s", "until 1901", "as of 1904");
- quantities, units and comparisons ("longer than twenty kilometres", "the oldest");
- exclusions ("other than the Ardune", "never", "the only");
- source restrictions ("according to the Velmaran Geographical Society");
- the requested attribute itself ("In what year", "Which river").

Filler ("Can you name", "Please answer briefly") is not a constraint. When one constraint is split
across the sentence, join its parts with ` ... ` in one `text` ("In what year ... found"); each part
must be a verbatim span.

Set `covered_by` to the hop indices and added-hop ids that establish the constraint, and `status`:

- `covered`: the hops jointly establish it, **including every qualifier**;
- `partially_covered`: a part or a qualifier is not established;
- `not_covered`: no hop establishes it.

Several hops may establish one constraint and one hop may serve several. A detail the documents
contradict or never state is `partially_covered` (the question says the atlas appeared in 1903; the
only document gives 1904). A constraint asserting an exclusion ("the only river", "the first keeper")
is covered only when the exclusion itself is established, not just the positive case.

**Dated facts.** A date qualifier needs a document for that period only when the fact can change over
time: a role, an office, a membership, a location of something movable, a ranking, a status. A fact
that cannot change (where a river rises, who composed an opera, a founding year) is covered by an
undated statement; say so in the constraint's `note`. A document that states the fact for a period
covering the date establishes it ("kept the lighthouse from 1890 to 1921" covers "in the 1890s").
Documents dated before and after the date, each stating the fact, do not establish it at the date.

## Step 2: hops

For each record hop (index = its 1-based position):

1. **The clue is the fact to verify**, not the snippet.
2. **Type** (the paper's definitions): `required` if removing the hop leaves the answer ambiguous or
   underdetermined; `confirmatory` if it corroborates a constraint without further narrowing the
   answer; `derivation` if it only computes from other hops (a count, a comparison, a sum, "1874 is
   earlier than 1880"). A derivation that smuggles in a world fact ("no conservatory is older") is a
   required factual hop. A confirmatory hop must still be grounded.
3. **Role**: `inclusion` (shows why the answer or a member satisfies a constraint) or `exclusion`
   (shows why a rival fails one; name it in `candidate`).
4. **Ground it.** Check the hop's own cited documents first (primary first), then documents cited by
   other hops (`source: "other_hop"`), then search (`source: "search"`), 2 to 6 reformulated queries
   at `--hits 50`, reading promising hits. Stop at the first document that fully supports the hop.
   A document supports the hop (`supports: "full"`) when, read in context, it states the clue's fact
   about the same entity with every qualifier the question needs:
   - **Same entity.** A pronoun or an implicit reference is fine when the surrounding text fixes the
     referent; quote enough to show it. An identity across documents ("the conservatory founded by
     Ilsa Renvaar" in travel notes, "the Velmaran Conservatory" elsewhere) holds when matching
     descriptors are stated; a shared name alone is `partial` (a "Hale" in a shipping register is not
     shown to be Tomas Hale the keeper).
   - **Qualifiers.** A different number, date, unit, edition or scope is not support.
   - **Dated facts** as in Step 1.
   - **Source clauses.** "According to X" is grounded only by a passage attributed to X.
   - **Conflicting values.** When documents disagree on a value the answer depends on, record both,
     mark the hop `partially_grounded`, and say in the `note` which evidence would settle it.
   - **Small inferences** a careful reader draws from the document alone count as `full`: arithmetic,
     a comparison (a 30 km river is longer than twenty kilometres), a unit conversion, the document's
     own dateline. Say so in the `note`. A fact that needs outside knowledge is not `full`.
   - **Conjunctive hops** ("the Ardune and the Varrow rise in the Morrowfen hills") may be grounded
     jointly by several documents, each `full` for its part; say so in the `note`.
5. **Quotes**: 1 to 3 per evidence entry, each the minimal sentence(s) that establish the fact, usually
   under 400 characters, including the sentence that names the entity when the supporting one uses a
   pronoun. Take every quote with `cm.py quote` and paste the printed string.
6. **Status**: `grounded` (at least one `supports: "full"`), `partially_grounded` (only partial
   support; the `note` says exactly what is missing), `ungrounded` (nothing found; the `note` says what
   was searched), `derivation_ok` / `derivation_wrong` (recompute it; the `note` gives the operation
   and its grounded inputs).
7. **rejected_docs**: cited documents you read and rejected, with the reason ("names the
   conservatory but not its founder", "not in the corpus").
8. **clue_issue**: one sentence when the clue itself is wrong, overstates its evidence, gives the
   answer away, or does not say what must be checked; else empty.

## Step 3: added hops

When a constraint is not covered because a hop is **missing** (not because an existing hop is
ungrounded), add it to `added_hops` with `id` `A1`, `A2`, ..., a `clue`, `type`, `role`,
`constraints`, a `note` on why it is needed, and ground it as in Step 2. Every exclusion of a rival
(Step 4) is an added hop with `role: "exclusion"` and `candidate` set, unless an existing hop already
states the excluding fact. Add the new ids to the constraints' `covered_by`.

## Step 4: candidates and completeness

Search for plausible alternatives and for set members by **three routes, each query read to depth 50**
(`--hits 50`; use `--only` to sweep for a phrase):

1. **list documents**: lists, tables, rankings, catalogues and category pages of the domain ("Rivers of
   Oskeria"), every listed entity checked against every constraint;
2. **named candidates**: every entity you know of, or have met in the documents, that could satisfy
   the question, searched by name (other spellings, renamed or merged entities, predecessors and
   successors, other editions or dates);
3. **category descriptions**: queries that describe the category in the question's terms ("river
   rises Morrowfen hills", "keeper Cape Vardequist lighthouse").

Record every plausible candidate in `candidates` with `role` (`answer`, `member` of a set answer, or
`rival`) and `status`:

- `qualifies`: grounded hops show it satisfies every constraint (a qualifying rival makes a single
  answer non-unique; a qualifying entity outside a set makes it incomplete);
- `excluded`: a grounded exclusion hop shows which constraint it fails;
- `unresolved`: neither is established (the `note` says what is missing). An unresolved candidate
  blocks a passing verdict: retrieval failure supports neither inclusion nor exclusion.

Your own knowledge, the source benchmark and the web may only **find** candidates and **block** an
answer (a well-established member the corpus never mentions still makes a set incomplete); they never
ground a fact. An absence cannot be quoted: a candidate a document shows fitting the other
constraints needs a stated excluding fact, never "no document says so".

`completeness` (required for sets and aggregates; optional for a single answer):

- `list_document`: a corpus document lists every member or gives the set size (`doc_ids`);
- `fixed_domain`: the question fixes a domain whose members are all checked (the seven provinces
  of a country, the three rivers of a list);
- `three_way_search`: no document closes the set; the three routes above found no further qualifying
  member and left no plausible candidate unresolved (`queries` for `list`, `named`, `category`). This
  is a recorded best-effort judgment, not proof that the whole corpus was checked;
- `unique_by_constraints`: for a single answer whose constraints leave one candidate.

For a set, ground every member against every constraint (membership in each filter, the question's
date, any value the answer states, with `aliases` for other names the documents use and `value` for a
stated value). For an aggregate, ground every counted member and its value, recompute the result in
`answer_check.note`, and check that the values agree across documents. Verify any requested ordering.

## Step 5: contamination and triviality

Set `contamination.leak` when a document reproduces the question with its answer, and
`contamination.single_document` when one document supplies every fact needed to answer it (one
sentence stating that Davin Kesh founded the Port Ilse Conservatory in 1880 answers "In what year did
Davin Kesh found the Port Ilse Conservatory?"). Either one fails the audit with `contamination`, and
the question is excluded rather than revised. List the documents in `contamination.doc_ids`.

## Step 6: answer check and verdict

`answer_check.status`: `derives_gold` when the grounded hops lead to the answer (for a set, to every
member; for an aggregate, to the value), `partial`, or `does_not_derive`; the `note` gives the chain.

`verdict`:

- `pass`: every constraint covered by the original hops, every original hop grounded by one of its own
  cited documents (or `derivation_ok`), the answer derived, unique or complete, no unresolved
  candidate, no contamination, and no added hops;
- `pass_with_fixes`: the same holds, but only thanks to the audit (added hops, or evidence from
  documents the hop did not cite). Make every such adjustment before writing the verdict;
- `fail`: anything else, confirmatory hops included, with `fail_reasons`:

| fail reason | condition |
|---|---|
| `ungrounded` | required evidence is absent from the retrieved corpus material, and further audit retrieval does not establish the fact |
| `incorrect_question` | the wording contains a false premise, a malformed constraint, or an ambiguity that prevents a supported interpretation |
| `incorrect_answer` | corpus evidence contradicts the answer or shows it does not match the question's temporal scope |
| `unresolved_alternative` | a plausible rival can be neither grounded as qualifying nor excluded by a grounded fact (or it qualifies) |
| `incomplete_set` | additional qualifying members are found, or a plausible missing member remains unresolved |
| `unsupported_member` | a listed member, required value, or computation lacks support or conflicts with the answer |
| `inconsistent_evidence` | hops disagree on entity identity, date, edition, unit or scope, or a cited passage does not support the claimed fact |
| `contamination` | a document reproduces the question and answer, or one document supplies every fact needed |

Failing to retrieve a fact does not prove it is absent from the corpus; it leaves the requirement
ungrounded. `summary`: one to three sentences a reviewer reads first: what is wrong, or what the audit
fixed. A failed audit goes to revision (C4), except contamination.

## Auditing a revised version

When the packet says "revision N", audit **that** version: write `"revision": N` and its `question` and
`answer` in the audit, take constraint spans from its question, and keep one entry for every record
hop. A hop that served only a constraint the revision removed gets `status: "dropped"` with a `note`
naming that constraint; its evidence is not needed. When an edit changes the fact a hop must establish,
drop the old hop and add a hop with the new clue. A new qualifier must be grounded for the answer, and
every rival it is meant to exclude needs its exclusion hop. Reuse what holds from the archived audits
the packet names, but reread every document you cite.

## Reasoning steps reviewers rejected

Auditors have accepted steps like these; human review rejected them. Do not.

- **An unsupported date.** "Tomas Hale was keeper in 1925": the document gives 1890 to 1921, and a
  second document about a keeper "in the 1920s" never names him.
- **A missing link between two steps.** Travel notes mention "the conservatory founded by Ilsa
  Renvaar" without naming it; using them to tie Renvaar to the Velmaran Conservatory skips the fact
  that connects the steps. Find a document that names both.
- **A quote for the neighbouring fact.** "The Ardune rises in the Morrowfen hills" quoted for "the
  Ardune runs through Velmaran".
- **Evidence on either side of a date.** One page has Oren Vasse directing the conservatory in 1905,
  another in 1925; neither establishes "in 1915" for a role that can change. (A page saying he
  directed it "until 1930" after taking over in 1901 does.)
- **A source clause ignored.** "According to the Velmaran Geographical Society" grounded by a page
  that does not attribute the statement to the society.
- **An exclusion by absence.** "The Selm is not in the Morrowfen hills because no page says it rises
  there", when a page says where it rises (Mount Corran) and should be quoted.

## Example (invented)

```json
{"record_id": "demo-2", "set": "demo", "revision": 0,
 "question": "Which rivers of Oskeria longer than twenty kilometres rise in the Morrowfen hills?",
 "answer": ["Ardune", "Varrow"],
 "constraints": [
  {"id": "C1", "text": "Which rivers of Oskeria", "covered_by": [1, 2, "A1"], "status": "covered", "note": ""},
  {"id": "C2", "text": "longer than twenty kilometres", "covered_by": ["A1"], "status": "covered",
   "note": "The list document gives the length class."},
  {"id": "C3", "text": "rise in the Morrowfen hills", "covered_by": [1, 2, "A2"], "status": "covered", "note": ""}],
 "hops": [
  {"index": 1, "type": "required", "role": "inclusion", "constraints": ["C1", "C3"], "status": "grounded",
   "evidence": [{"doc_id": "demo_doc_002", "source": "original", "supports": "full",
                 "quotes": ["The Ardune rises in the Morrowfen hills"]}],
   "rejected_docs": [], "clue_issue": "", "note": ""},
  {"index": 2, "type": "required", "role": "inclusion", "constraints": ["C1", "C3"], "status": "grounded",
   "evidence": [{"doc_id": "demo_doc_004", "source": "original", "supports": "full",
                 "quotes": ["The Varrow rises in the Morrowfen hills"]}],
   "rejected_docs": [], "clue_issue": "", "note": ""}],
 "added_hops": [
  {"id": "A1", "clue": "The Ardune and the Varrow are among Oskeria's rivers longer than twenty kilometres.",
   "type": "required", "role": "inclusion", "constraints": ["C1", "C2"], "status": "grounded",
   "evidence": [{"doc_id": "demo_doc_005", "source": "search", "supports": "full",
                 "quotes": ["The island has three rivers longer than twenty kilometres: the Ardune, the Selm and the Varrow."]}],
   "rejected_docs": [], "note": "No hop established the length constraint."},
  {"id": "A2", "clue": "The Selm rises on Mount Corran, not in the Morrowfen hills.", "type": "required",
   "role": "exclusion", "candidate": "Selm", "constraints": ["C3"], "status": "grounded",
   "evidence": [{"doc_id": "demo_doc_003", "source": "search", "supports": "full",
                 "quotes": ["The Selm rises on the slopes of Mount Corran"]}],
   "rejected_docs": [], "note": "Excludes the third river of the list."}],
 "candidates": [
  {"name": "Ardune", "role": "member", "status": "qualifies", "hops": [1, "A1"], "note": ""},
  {"name": "Varrow", "role": "member", "status": "qualifies", "hops": [2, "A1"], "note": ""},
  {"name": "Selm", "role": "rival", "status": "excluded", "hops": ["A2"], "note": "Rises on Mount Corran."}],
 "completeness": {"basis": "list_document", "doc_ids": ["demo_doc_005", "demo_doc_012"],
                  "note": "demo_doc_005 lists the three long rivers; demo_doc_012 names the two that rise in the hills."},
 "contamination": {"leak": false, "single_document": false, "doc_ids": [], "note": ""},
 "answer_check": {"status": "derives_gold", "note": "Long rivers {Ardune, Selm, Varrow} minus the Selm = {Ardune, Varrow}."},
 "verdict": "pass_with_fixes", "fail_reasons": [],
 "summary": "Both members grounded; the audit added the length hop and the Selm's exclusion."}
```

## Second reader (C3)

A second, independent agent rereads every finished audit before it advances. If you are the second
reader:

1. Read the question yourself. Is an information-bearing phrase (entity, relation, date, number,
   qualifier, exclusion, source) missing from the constraints?
2. For every `covered` constraint, do the hops establish it with every qualifier? Downgrade when the
   documents contradict or never state a detail, when an exclusion is asserted but not shown, or when
   coverage rests on reading a garbled question charitably.
3. For every `grounded` hop, open the cited document and read each quote in context: same entity, the
   clue's fact (not a neighbouring one), the same number, date, unit and edition, the named source.
4. For every candidate, is the decision grounded? Is any plausible candidate missing (run up to 4
   searches of your own at `--hits 50`)? Does the completeness basis hold?
5. **Fix before you fail.** When something falls short, first search for the missing evidence and add
   it (quotes via `cm.py quote`); only then downgrade a status and explain the gap in the `note`.
6. Recompute the verdict from the statuses with the rules above and rewrite `summary` for the current
   state.

Do not relitigate sound calls (confirmatory typing, small inferences from the document, identities
with several matching descriptors). Edit the audit in place and add:

```json
"verification": {"result": "confirmed | revised",
                 "changes": ["H2: grounded -> partially_grounded (the page names a different keeper)"],
                 "note": "one or two sentences for the reviewer"}
```

`changes` lists every status, evidence, constraint, candidate or verdict change, one line each; it is
empty when the result is `confirmed`.

## Finishing

Run `python3 TOOLS/validate.py SET QID` and fix every ERROR; the audit is done only at 0 errors.
Touch no file other than your audit (the search tool appends to the question's search log by
itself). If a tool call is refused or keeps failing so that you cannot finish, say so in your report:
never claim an audit you did not complete.
