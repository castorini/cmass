# Revision guide (C4)

You make one repair attempt for one question that Stage 2 sent back, and record it in one file:
`revisions/<set>/<qid>.r<N>.json` (format in `SCHEMA.md`, "Revision attempt"). The attempt number N,
the trigger and the set's permitted revisions come from your task. You do **not** edit the audit: the
repaired version is audited again from the start (C1-C3), challenged again (C5), and reviewed again by
a human. An approval of an earlier version never carries over.

Read `AUDIT.md` first: its tools, grounding rules and shared requirements apply to everything you do
here. Examples use the invented island nation of Oskeria.

## What sends a question here

| trigger | what you receive |
|---|---|
| `audit` | the source version failed its audit: the audit's `fail_reasons`, `summary` and notes |
| `adversarial` | the verifier upheld a hunter's finding: the verify file's `blocking` problems and rulings |
| `human` | the reviewer chose "accept with fixes": the reviewer's comments, on the question, constraints and hops |
| `final_check` | the final check failed: its `gaps` and `blocking` problems; when every gap is an evidence gap, only an evidence-only repair is allowed |

Each trigger gets this one attempt. An unsuccessful attempt excludes the question; so does a failed
re-audit of your version, and a second verified refutation.

## Step 1: retry the evidence first

Even if earlier agents searched for the missing fact, search again and reread the relevant documents
before considering any edit. Start with `cm.py history --set SET --qid QID` and do not repeat its
queries; find the fact by a different route:

- **deep result lists**: `cm.py search ... --hits 50 --only "phrase"` with distinctive phrases (names,
  numbers, rare words);
- **rephrase the fact** as a page would state it: a biography sentence, a news lede, an obituary, a
  list entry, a catalogue line;
- **variants**: other names and spellings, titles, dates as "1874", "in the 1870s", month names,
  units in both systems;
- **pivot** through related entities (colleagues, institutions, places, events) whose pages may state
  the fact;
- **mine documents already cited**: long pages often state it further down (`cm.py doc --grep`);
- **short rare-term queries** (2 to 4 words) after long ones fail;
- **dated facts**: a page dated to the period (an annual report, a dated news item).

Record the queries in `evidence_retry.queries`. If evidence alone resolves every problem, the outcome
is `evidence_only`: the question and answer stay exactly as they were. List in `hop_changes` what the
re-audit should find (`evidence_added`, `added`, `regrounded`, ...), for example an exclusion hop for
the rival the final check named, with its document.

## Step 2: otherwise, the least extensive permitted edit

Only when evidence cannot resolve the problem, make the smallest change the set's family permits, in
this order of preference:

1. change the wording and keep the answer;
2. recompute the answer and keep the wording (only families whose answer may change);
3. change both.

Permitted edits by family (`cmass/config.py`; a set may override them; your task lists the set's):

| family | permitted | answer |
|---|---|---|
| hotpotqa | correct malformed wording (`correct_wording`) or add one grounded qualifier (`add_qualifier`) | preserved |
| bcp | evidence-only repairs for questions Stage 1 retained; near misses may also get `correct_clue`, `remove_clue`, `adjust_range` or one `add_qualifier` | preserved, except `correct_answer_typo` |
| trqa | `add_qualifier`, or `change_attribute_if_trivial` when the wording makes the answer trivial; `recompute_answer` under the completeness standard | may change |
| dsqa | `remove_source_clause`, `add_qualifier`, `adjust_year_or_threshold`; `recompute_answer` under the completeness standard | may change |
| generic | evidence only, `add_qualifier` or `correct_wording` | preserved |

The edit kinds:

- `correct_wording`: fix malformed or garbled wording (a typo, a broken clause) without changing
  what is asked.
- `add_qualifier`: add one natural qualifier, grounded for the answer, that excludes the rivals
  ("Which keeper of the Cape Vardequist lighthouse in the 1890s ..." when a later keeper also fits).
  Every rival it excludes must be excluded by a stated fact, and the qualifier must not give the answer
  away or name it.
- `correct_clue`: replace a detail the corpus **contradicts** with the value the corpus states. A
  detail the corpus is merely silent about is not a correction.
- `remove_clue`: drop a clue the corpus cannot support and that does not narrow the answer: the
  remaining grounded constraints, shown from the corpus, still leave the answer unique. Never drop the
  requested attribute, and never a clue that removes an ambiguity.
- `adjust_range`: turn an exact value into a range or a cut-off the documents support ("in 1874" ->
  "in the 1870s"), when the exact value cannot be grounded but the range can and still narrows.
- `remove_source_clause`: drop "according to X" when the corpus cannot reproduce source X, and X does
  not define membership (a source's own category or threshold defines the set; dropping it can open it).
- `adjust_year_or_threshold`: move a year or a numeric threshold to one the corpus grounds.
- `change_attribute_if_trivial`: when the wording fixes the answer (the earliest founding year among
  conservatories founded in 1874), ask for another attribute of the same members that the corpus
  grounds for every one and that varies across them; set `trivial_as_written: true` and recompute.
- `recompute_answer`: change a set or aggregate answer to what the corpus supports. Only under the
  completeness standard: the set is closed (a document gives its list or size, or the scope is a fixed,
  grounded domain); missing members were hunted three ways at depth 50; a member you know qualifies in
  reality blocks the new answer unless the wording excludes it; every member's value agrees across
  documents; and the argument is written in `completeness_note`. Never an answer computed over a subset
  of the qualifying members, and never invented members.
- `correct_answer_typo`: fix a misspelling in the answer (a small character-level change).

Every edit: copy the rest of the question verbatim; keep the question natural and nontrivial; keep at
least two members in a set or aggregate unless the source had one; record `from` (the replaced or
removed span of the previous question) and `to` (the new span), with a `note`. Explain in
`edit_rationale` why this edit is the least extensive one that works and why the answer stays correct
and unique (or the set complete). Ground every new or changed clue before you write the file: search
for it, and note the documents in `hop_changes` for the re-audit.

## Step 3: or record that it cannot be repaired

When neither evidence nor a permitted edit resolves the problem (the corpus contradicts an answer the
family preserves; the only fix is a larger rewrite), write `outcome: "unsuccessful"` with a `blocker`
saying what is missing or contradicted and what you searched and tried. The question is excluded.

## The file

```json
{"record_id": "<id>", "set": "<set>", "attempt": 1,
 "trigger": {"stage": "adversarial", "ref": "<from your task>", "reasons": ["<from your task>"], "evidence_only": false},
 "based_on_revision": 0,
 "evidence_retry": {"queries": ["<query>"], "resolved": false, "note": "..."},
 "outcome": "revised",
 "question": "<the full new question>", "answer": "<the answer>",
 "edits": [{"kind": "add_qualifier", "from": "", "to": "<the added words>", "note": "..."}],
 "edit_rationale": "...",
 "hop_changes": [{"hop": "A1", "change": "added", "note": "<the exclusion and its document>"}],
 "completeness_note": "", "trivial_as_written": false, "blocker": "", "note": ""}
```

`question` and `answer` are the full new version (for `evidence_only`, the version you repaired,
unchanged). Then run `python3 TOOLS/check_records.py SET QID --only revision` and fix every ERROR.
Touch no other file. If a tool call is refused or keeps failing so that you cannot finish, say so in
your report: never claim a repair you did not complete.
