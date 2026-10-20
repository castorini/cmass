# Adversarial check guide (C5)

Every question whose audit passes is challenged before a human sees it. An independent **hunter**
tries to break it; a separate **verifier** reads the evidence behind each finding and rules whether
it invalidates the question. Neither edits the audit. Read `AUDIT.md` first: its tools, grounding rules
and shared requirements decide what counts. Examples use the invented island nation of Oskeria.

## Hunter

Your task names the version to challenge (its question, answer and revision number), the audit file,
your route and your output file: `adversarial/<set>/<qid>.<kind>[.r<N>].json` (format in `SCHEMA.md`,
"Hunter findings"). Your job is to find what the auditor missed. Read the audit (its candidates,
exclusion hops, completeness and notes) and `cm.py history` first, so you search differently.

Look for:

- **rival answers**: another entity or value that satisfies every constraint of the question as
  worded (a second keeper of the same lighthouse who also logged every ship);
- **missing members** of a set, and qualifying entities left out of an aggregate;
- **wrongly included members**: a member that fails a constraint;
- **conflicting values**: a document giving a different date, number or unit for a value the answer
  depends on, or a dated fact that changed within the question's period;
- **alternative readings**: a reasonable reading of the wording under which another answer
  qualifies ("until 1901" read as "at some time before 1901");
- **ungrounded hops**: a quote that, read in context, does not state the hop's fact.

How:

- Request `--hits 50` for every query and reformulate: list and category documents of the domain,
  every candidate you can think of searched by name (other spellings, predecessors, successors,
  renamed entities), and descriptions of the category in the question's terms. Read the documents,
  not only the previews (`cm.py doc --grep`).
- You may use web search to **find leads**: a name, a date, a list. A web page is never evidence;
  search the corpus for what it suggests.
- Your route, when you have one: `list` (two-hunter questions: lists, tables, rankings, catalogues and
  category pages, every listed entity checked against every constraint) or `named` (every known
  candidate searched by name, then each checked). Route `all` covers both.
- Report only real findings; an empty list is a valid result. For each finding give the `candidate`,
  the `claim` (which constraint it satisfies or breaks, and how), the corpus `evidence` with verbatim
  quotes from `cm.py quote` (`in_corpus: true`), or, for something you know only from outside the
  corpus, `in_corpus: false` with any `web_lead`. Say whether it would change the answer and how sure
  you are (`certain` only for well-established facts). If the audit already excludes it, name the hop
  in `excluded_by`; report it anyway when you doubt the exclusion.

Then run `python3 TOOLS/check_records.py SET QID --only hunt` and fix every ERROR.

## Verifier

Your task names the version and its hunter files; write `adversarial/<set>/<qid>.verify[.r<N>].json`
(format in `SCHEMA.md`, "Verifier rulings"). Rule on **every** finding, `refutes` or
`does_not_refute`, after reading the cited passages **in context** (`cm.py doc --grep`, whole
documents when needed) and the audit's hops and candidates.

A finding **refutes** the question when, under the shared requirements, it shows that:

- a rival satisfies every constraint and no grounded fact excludes it (an absence never excludes a
  candidate a document shows fitting the other constraints);
- a qualifying member is missing from a set or aggregate, or a member does not qualify;
- documents conflict on a value the answer depends on and the record does not resolve it;
- a reasonable reading of the wording admits another answer and the wording does not rule it out;
- a hop's quote, read in context, does not state the hop's fact and no other cited evidence does;
- a well-established fact from outside the corpus (`in_corpus: false`, `certain`) makes the answer
  wrong, not unique, or incomplete. Outside knowledge can block an answer; it never grounds one.

A finding **does not refute** when the passage, read in context, does not say what the hunter claims
(another entity of the same name, another period, another unit); when a grounded fact excludes the
candidate (cite the audit hop, or the passage you found: then add `record_exclusion` with the document
and a verbatim quote, so the record can carry it); or when the claimed reading is not a reasonable one.
Give the `reason` and `docs_read` for every ruling. `refuted` is true when any ruling refutes, and
`blocking` then lists each problem in one line a repair agent can act on.

A verified refutation sends the question to revision (C4) once; a second refutation, of any later
version, excludes it. Then run `python3 TOOLS/check_records.py SET QID --only verify` and fix every ERROR.

## Both roles

Touch no file other than your output (the search tool appends to the question's search log by
itself). Run corpus commands one at a time; a failed request is not evidence, so retry it. If a tool
call is refused or keeps failing so that you cannot finish, say so in your report: never claim a check
you did not complete.
