# Writing corpus-scan rules (Stage 3)

The corpus scan (`scan.py`) reads every document of the corpus once and lists, for each hop, every document its
**match rules** accept. Those documents become the hop's judging candidates. You write the rules of one question:
one **group** of rules per hop that has qrels, so that

1. **coverage (hard requirement):** each hop's group matches **100%** of the hop's known qrels documents (every one of
   them, no exceptions), and
2. **specificity:** each group matches as few other documents as it can. The scan's matches are read by judges; a
   group matching hundreds of thousands of documents is useless. Each group must stay within the **breadth cap**
   (default 20,000 documents, estimated over the whole corpus), unless the hop's fact is genuinely stated in that
   many documents (`broad_ok`, below).

You are done when `rules.py state QID` prints `next: done`. The tool decides that from the files on disk, not from
what you report.

## Files and commands

`R` is the command line at the end of your packet (`python3 .../rules.py --config ... --set ...`).

| what | file / command |
|---|---|
| your packet: the question, each hop (clue, standard), each known document with an excerpt | `work/<qid>.md`, written by `$R packet QID` |
| **your rules (the only file you write)** | `<qid>.rules.jsonl` (path in the packet) |
| a document's text, or the text around phrases | `$R doc QID DOCID [--grep PHRASE ...]` |
| coverage: per hop matched/known, the missed ids and why | `$R check QID` (writes `work/<qid>.check.json`) |
| breadth: matches in a 24-shard sample and the corpus-wide estimate | `$R breadth QID` (writes `work/<qid>.breadth.json`) |
| what is left to do | `$R state QID` |

Never edit the packet, known, check or breadth files, and never touch another question's files. Documents are read
from the local corpus by the tool; do not use a search service.

## The loop

1. `$R packet QID`; read the packet in full.
2. For each hop, write a **general rule** (below) from the clue, the standard and the excerpts.
3. `$R check QID`. For each missed document: `$R doc QID DOCID --grep ...` to see how it names the subject and
   states the fact; then add a specific variant to the general rule, or give the document **a rule of its own**.
   Repeat until every hop passes at 100%.
4. `$R breadth QID`. For each group over the cap: narrow it (below). Narrowing can lose a known document, so check
   again, then breadth again.
5. `$R state QID` until `next: done`.

`state` prints one of: `packet` (run packet), `write` (no rules file, or it does not validate: the detail says why),
`check` (the rules changed since the last check), `fix_coverage` (missed ids per hop), `breadth` (no breadth report
for the current rules), `narrow` (a group over the cap), `done`. Changing a rule's `all`, `none`, `group` or
`rule_id` makes both reports stale; changing only its `meta` does not.

## How matching works

A rule is one JSON line:

```json
{"rule_id": "demo/demo_r1/1/varn_quay", "group": "demo/demo_r1/1", "all": [["Edda Varn"], ["customs quay"]], "none": ["hotel"], "meta": {"note": "..."}}
```

- **`group`** is the hop's group, exactly as the packet names it (`<set>/<qid>/<hop>`). **`rule_id`** starts with
  the group and a `/`, then a short name of your choice (letters, digits, `_.:-`). Every hop of the packet needs at
  least one rule; rules for other groups are refused.
- **`all`** is a list of **clauses**; each clause is a list of alternative **phrases**. A document matches when
  **every** clause has **at least one** of its phrases somewhere in the document, and no phrase of **`none`** occurs.
  The clauses need not be in the same sentence: the conjunction is over the whole document, so in a long page each
  clause can be satisfied by an unrelated mention. More clauses and longer phrases protect against that.
- A **group** matches the **union** of its rules: one loose rule makes the whole hop loose.
- **`meta`** is free-form and ignored by the matcher (`"for"`, `"note"`, and `"broad_ok"`, below).

Phrases are literal strings (no wildcards, no regular expressions), compared after normalisation:

- **Case** does not matter, and every run of whitespace (newlines, tabs, non-breaking spaces) counts as one space.
- **Nothing else is folded.** No stemming, no accent folding, no punctuation folding: `café` does not match `cafe`,
  `harbour-master` does not match `harbour master` or `harbourmaster`, a straight `'` does not match a curly `’`.
- **Word boundaries.** A phrase that starts (ends) with a letter, digit or `_` must not be preceded (followed) by one.
  So `flood` does **not** match `floods`, `flooded` or `flooding`; `fisher` does not match `fishery`; `1871` does not
  match `18710` or `1870s`. Punctuation is not a word character: `varn` matches `Varn's`, `Varn-led` and `(Varn)`;
  `1871` matches `1871-72`, `(1871)` and `1871.`; `IUU` matches `IUU-listed`.

Use `doc --grep` to test a phrase on a document: it reports the count on word boundaries and as a bare substring,
and shows the normalised text (what rules see) around each hit.

## Writing a good rule

A good general rule usually has **one clause of the hop's subject** and **one or two clauses of the fact's key
terms**:

- **The subject clause:** every name the documents use for the hop's subject: the full name; the surname alone or a
  short form when it is distinctive; spelling variants, transliterations, older spellings and other languages;
  abbreviations and acronyms with their expansion. Take them from the clue, the standard's `subject`, the excerpts and
  the answer. A punctuation-free part of a name avoids apostrophe variants (`brien` matches `O'Brien` and `O’Brien`).
- **The fact clause(s):** the words that state the hop's fact, with the variants the documents use: inflections
  (`flood`, `floods`, `flooded`, `flooding`), synonyms (`founded`, `founder`, `established`), hyphenation
  (`harbour master`, `harbour-master`, `harbourmaster`), British and American spellings.
- **Numbers written several ways:** a year (`1934`) and its short and spelled forms where documents use them (`'34`,
  `nineteen thirty-four`); counts with and without separators (`12,500`, `12 500`, `12500`, `12.5 thousand`); dates in
  several orders (`3 March 1871`, `March 3, 1871`); ordinals (`3rd`, `third`). A year or an exact figure is a strong
  clause; a small number alone (`3`) matches almost everything, so pair it with its unit (`3 km`, `3 kilometres`).
- **Prefer multi-word phrases** that carry the fact (`customs quay`, `first lit`, `mouth of the Ardune`) over single
  common words (`quay`, `lit`, `river`).
- **`none` sparingly**, for a systematic confounder with a distinctive marker (a namesake: a ship or hotel named after
  the subject; a different entity sharing the name). A `none` phrase removes every document that contains it,
  including a known document that mentions it in passing; `check` will tell you.

**A document the general rule cannot reach gets a rule of its own.** When a known document states the fact in its
own words (a nickname, a paraphrase, the subject named only by surname), write a second rule for it from its own
specific words (`doc --grep` them), with the same shape (subject clause + fact clause), and say which document it is
for: `"meta": {"for": ["<doc id>"]}`. A hop may have as many rules as it needs. Add a variant to the general rule only
when the variant is itself specific (an alias spelling such as `Vardekvist`); never loosen the general rule to reach
one document.

**Never loosen a rule into a single common word.** Dropping a clause, or replacing a phrase by a common word (`varn`
alone, `lighthouse`, `port`, a year alone), reaches the missed document and every other document that mentions the
word: the group's union becomes the whole topic. If a document can only be reached that way, its rule needs another
clause from its own rare words.

## Breadth

`breadth` runs the scanner on the question's rules over a fixed sample (by default 24 shards spread evenly over the
corpus: every 272nd of ClimbMix's 6,543, about 2 million documents, roughly 10 seconds) and reports, per rule and per
group, the matches in the sample and the estimate over the corpus (sample matches x 272.6). One match in the sample is
worth about 273 documents, so a group with no sample match is estimated at 0 (it may still match a few hundred), and
the default cap of 20,000 is about 73 sample matches. Near the cap the estimate is noisy (about +/-12%).

When a group is over the cap:

- Look at the over-cap rule's **examples** (sampled doc ids it matches) with `doc ... --grep <its phrases>`: are they
  about the subject? Do they state the fact?
- If they are about the subject but not the fact: add or sharpen a fact clause.
- If they are about something else with the same name: sharpen the subject clause (full name, a qualifier), or add a
  `none` for the confounder's marker.
- If one alternative of a clause is the culprit (a short form, an acronym that is also a common word): drop that
  alternative from the general rule and move the documents that need it to a rule of their own with an extra clause.
- Per-document rules are often the broad ones (the document phrased the fact generically): give them a further clause
  from the document's own rare words (a co-mentioned place, date or name).
- **Narrow on meaning, not on the sample.** A `none` that only excludes the quirks of the sampled examples lowers the
  estimate without lowering the true count; the scan reads the whole corpus.

`--sample N` or `--shards A:B` runs a different sample for a quick look (written to `breadth_custom.json`); only the
standard sample counts for `state`.

### `broad_ok`: when the fact really is in that many documents

Some facts are stated on a great many pages (a capital city, a famous person's birth year, a town's location on a
river). Then even the narrowest rule that still covers the known documents is over the cap, because many documents
really state the fact, not because the rule is loose. Only then:

- first make the rule as narrow as the fact allows (subject clause + the fact's own phrasing);
- check the examples: most of them must state the fact. If they do not, narrow instead;
- add `"meta": {"broad_ok": "<reason>"}` to **each rule of the group that is itself over the cap** (and to at least one
  rule when only their union is over), with a reason of at least 10 characters saying why the fact is that common and
  what the sampled matches showed.

`collect` lists every `broad_ok` group with its estimate for a reviewer. `broad_ok` on a loose rule is a wrong answer,
not a way out: if you cannot narrow a group and its fact is not genuinely common, stop and report it.

## Worked examples (the invented Oskeria corpus)

These run on the demo corpus `../demo/rules_corpus/` (50 invented documents; `../demo/test_rules.py` runs them), with
a cap of 10 documents instead of 20,000. Ids are the demo's own (`demo_doc_250` ...).

### 1. Two rules for one hop (demo_r1, hop 1)

The hop asks for the Port Ilse harbour master who closed the old customs quay in 1934 (Edda Varn). The packet shows
three known documents; two use her full name and "customs quay":

> Edda Varn, harbour master of Port Ilse from 1929 to 1941, **ordered the old customs quay closed in 1934** after the
> winter storms undermined its piles.

The general rule:

```json
{"rule_id": "demo/demo_r1/1/varn_quay", "group": "demo/demo_r1/1", "all": [["Edda Varn"], ["customs quay"]]}
```

`check` misses one document, and says why (each clause of the rule found no phrase):

```
demo/demo_r1/1       2/3          0.667  FAIL
                  missed (1): demo_doc_251
                  why demo_doc_251 / demo/demo_r1/1/varn_quay: all[0] ['edda varn']; all[1] ['customs quay']
```

`doc demo_r1 demo_doc_251 --grep "edda varn" varn "customs quay" "customs wharf"` shows it says *Mrs Varn* and
*customs wharf*:

```
--- 'edda varn': 0 on word boundaries, 0 as a bare substring
--- 'varn': 1 on word boundaries, 1 as a bare substring
  [9] when mrs varn shut the customs wharf in the spring of '34, the fish merchants of port ilse moved ...
--- 'customs wharf': 1 on word boundaries, 1 as a bare substring
```

Adding `varn` to the general rule's subject clause would match every Varn in the corpus together with "customs quay";
instead the document gets a rule of its own, as specific as the general one:

```json
{"rule_id": "demo/demo_r1/1/varn_wharf", "group": "demo/demo_r1/1", "all": [["Varn"], ["customs wharf"]], "meta": {"for": ["demo_doc_251"]}}
```

Now the hop passes 3/3, and `breadth` estimates the group at 4 documents (the three known ones and a page on Edda
Varn's granddaughter's cafe on the old customs quay, a fair candidate for the judges).

### 2. An alias and a number in words (demo_r1, hop 2)

The hop asks for the year the Cape Vardequist lighthouse was first lit. A first rule,
`[["Cape Vardequist"], ["lighthouse"], ["1871"]]`, misses `demo_doc_254`, and `check` reports that all three clauses
fail on it:

> Vardekvist fyr, as the old Oskerian charts spell the cape, shone for the first time in eighteen seventy-one; ...

The alias `Vardekvist` and the Oskerian word `fyr` are specific, so they belong in the general rule, with the year in
words:

```json
{"rule_id": "demo/demo_r1/2/light_1871", "group": "demo/demo_r1/2", "all": [["Vardequist", "Vardekvist"], ["lighthouse", "light", "fyr"], ["1871", "eighteen seventy-one"]]}
```

`light` is a common word, but only inside a document that also names the cape and the year. A page on the cape's
automatic light today (no year) does not match.

### 3. A hop that starts too broad (demo_r2, hop 1)

The hop asks for the river through Velmaran that floods most springs (the Ardune). `[["Ardune"]]` passes `check`, and
`breadth` flags it:

```
group demo/demo_r2/1: 36 in the sample, est 36  OVER CAP (est 36 > cap 10)
  rule demo/demo_r2/1/ardune: 36 in the sample, est 36  over cap
```

The examples are pages that mention the Ardune for barges, trout and regattas: about the subject, not the fact. The
fact clause, with its inflections (`flood` alone would miss "floods" and "flooding"):

```json
{"rule_id": "demo/demo_r2/1/ardune_flood", "group": "demo/demo_r2/1", "all": [["Ardune"], ["flood", "floods", "flooded", "flooding"]]}
```

`check` still passes 2/2 and the estimate drops to 3 (the two known documents and a page on flood insurance by the
Ardune bank).

### 4. A fact stated everywhere: `broad_ok` (demo_r2, hop 2)

The hop asks for the town at the mouth of the Ardune (Port Ilse). `[["Port Ilse"], ["mouth of the Ardune"]]` is
already the fact's own phrasing, yet `breadth` estimates 14, over the demo cap of 10. The examples all say that Port
Ilse lies at the mouth of the Ardune: the demo's gazetteer pages repeat it. Nothing narrower would still state the
fact, so the rule records why:

```json
{"rule_id": "demo/demo_r2/2/mouth", "group": "demo/demo_r2/2", "all": [["Port Ilse"], ["mouth of the Ardune"]], "meta": {"broad_ok": "the town's place at the river mouth is stated on most pages about Port Ilse; each match states the fact itself"}}
```

`state` then says `done` without a new check or breadth run (only `meta` changed):

```
demo_r2: done  (2/2 hops at 100%)  every hop at 100%, every group within the cap or broad_ok: hop 2 est 14 (broad_ok)
```

Had the examples been pages that merely mention Port Ilse and the Ardune separately, the answer would have been a
narrower rule, not `broad_ok`.

### 5. A document whose excerpt says nothing (demo_r3)

The hop asks for the composer who founded the Velmaran Conservatory (Ilsa Renvaar). For `demo_doc_294` the packet
falls back to the document's start, because none of the clue's terms occur in it:

> Programme note. Renvaar's academy in the capital, established 1874, trained most of Oskeria's organists.

It names neither the conservatory nor Velmaran, so the general rule (`[["Renvaar"], ["Velmaran Conservatory",
"Conservatory at Velmaran"], ["founded", "founder", "founding"]]`) cannot reach it. Its own rule uses its own words:
`[["Renvaar"], ["academy"], ["1874"]]`, with `"meta": {"for": ["demo_doc_294"]}`. Not `[["Renvaar"]]`: that would
add every page that names the composer.

## When you cannot finish

Stop and report what remains (the missed ids, the groups over the cap with their estimates) when:

- a known document is **unavailable** (not in the corpus): no rule can match it;
- a group stays over the cap and its fact is not genuinely common;
- after six rounds of check and breadth the state is still not `done`.

Never claim `done`: the workflow reads `rules.py state` afterwards, and so does `collect`.
