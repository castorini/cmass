# Stage 3: qrels construction

Turns the questions Stage 2 accepted into relevance judgments: for each question, the union of the corpus documents
that support its reasoning hops, per hop and per question. Two phases, as in the paper's Stage 3 paragraph:

- **Phase 1, the precision filter** (`qrels.py`, workflow `phase1.js`): pool every document retrieved for the question
  during projection, audit and revision, read each in full, and keep it only for the hops it supports.
- **Phase 2, duplicate expansion** (`dups.py`, workflow `phase2.js`): add the exact duplicates of every kept document,
  and the near duplicates (5-gram Jaccard ≥ 0.7) that hold their parent's supporting quote verbatim (the
  [quote check](#near-duplicates-the-quote-check)) or that an agent has verified on their own text.

The judging standard every agent follows is [`QRELS.md`](QRELS.md). Everything is per question and resumable: each
unit of work writes one file, every write is atomic, and `qrels_state.py` reports what exists on disk so a workflow
resumes from disk rather than from agents' reports.

[`pipelines/bcp_climbmix/qrels/`](../../bcp_climbmix/qrels/) is the earlier, BrowseComp-Plus-only version of this
stage (reviewer workbook in, duplicate expansion by hand). This directory replaces it for CMASS runs; the two share
the judging discipline (standard before reading, full-text reading, verbatim quotes, exact duplicates inherited, near
duplicates never inherited on similarity alone).

## The paper's Stage 3, mapped to code

| paper | code | what happens |
|---|---|---|
| qrels are the union of documents supporting the reasoning hops | `qrels.py build` | `qrels.jsonl` (question level) is the union of `qrels_hops.jsonl` (per hop); `verify` checks every kept document supports a hop |
| computational hops: documents ground the factual inputs | `pool` (derivation hops), `check`, `verify` | derivation hops are not judged and cannot be cited; `verify` exempts them and checks that their listed `inputs` keep documents |
| candidate pooling from projection, audit and revision, each document mapped to its associated hops | `qrels.py pool`, `recall` | Stage 2 accepted evidence (fixed), Stage 1 cited documents per hop, every search-log result, every document in the audit, revision, adversarial and final-check files, and optional recall candidates; provenance per document |
| one full-document assessment checks all of a document's proposed support | `fetch`, `chunks`, `check` | each candidate is rendered in full once and judged against all live hops |
| an agent reads each candidate in full and decides which hops it supports; unsupported or uncertain associations discarded; kept if it supports at least one hop | `phase1.js` (Judge, Second judge, Adjudicate), `check`, `second`, `compare`, `adj-check`, `build` | tiered review (below); a document is kept with the hops its final verdict lists |
| documents accepted through Stage 2 audit and human review remain fixed | `pool` (`fixed`), `build`, `verify` | accepted evidence of live hops is never re-judged; `verify` fails if any is missing |
| add missing exact duplicates | `dups.py lookup`, `apply` | byte-identical copies (checked against the corpus text) inherit their parent's hops and support labels |
| near duplicates at 5-gram Jaccard ≥ 0.7 that repeat a supporting quote of the original, the rest verified by agents | `dups.py chunks`, `phase2.js`, `dups.py apply` | the [quote check](#near-duplicates-the-quote-check) first: a near duplicate holding its parent's quote for a hop verbatim inherits that hop; by default the rest are judged on their own text, with the parent's evidence shown, by the same tiered review |
| (closure) | `dups.py lookup --round N+1` | duplicates of newly added documents are looked up in turn until a round finds nothing new |

Stage 0's duplicate detection (exact groups by SHA-256, near pairs by MinHash then exact 5-gram Jaccard) produced the
duplicate map phase 2 reads: the `corpus_duplicates` config of the Hugging Face dataset `castorini/cmass`
([`corpus_analysis/release/DUPLICATES.md`](../../../corpus_analysis/release/DUPLICATES.md)).

## Inputs

All paths are relative to the run directory ([`../cmass/run.py`](../cmass/run.py)).

| input | from | used for |
|---|---|---|
| `final/<set>_final.jsonl` | Stage 2 ([`../stage2/SCHEMA.md`](../stage2/SCHEMA.md)) | one accepted question per line: `record_id`, `set`, `question`, `answer`, `answer_type`, `hops` (`id` "1".."n" or "A1"…, `clue`, `type` required / confirmatory / derivation, `role` inclusion / exclusion, `candidate`, `status`, `evidence` [{`doc_id`, `supports`, `quotes`}], optional derivation `inputs` [hop ids]), `dropped_hops`, `candidates`, `constraints`, `completeness` (its `doc_ids` join the pool) |
| `inputs/<set>.jsonl` | Stage 1 | each hop's cited `doc_id` / `supporting_doc_ids` (hop *i* of the Stage 1 record is hop "*i*" of the final record) |
| `work/<set>/<qid>.searchlog.jsonl` | Stages 1-2 (`tools/cm.py --set --qid`) | every search made for the question: `{query, hits, results: [doc ids]}` |
| `work/<set>/<qid>.*.json`, `revisions/`, `adversarial/`, `final_check/` | Stage 2 | every `doc_id` they name joins the pool |
| a local copy of `corpus_duplicates` | Hugging Face | phase 2 (`"duplicates"` in the config) |
| the corpus | `cmass.json` `"corpus"` | full text through `cmass.corpus` (cached under `cache/docs/`) |

A hop whose `status` is `dropped` is an **out hop**: its documents are judged like any candidate, but a document
supporting only an out hop is dropped. A record missing from the Stage 1 inputs (a rewritten question) simply has no
Stage 1 seeds.

## Configuration

Stage 3 settings live in `cmass.json`, as `"qrels"` at the top level (all sets) or inside a set's entry (that set).
Layers, later wins: the defaults below, the set's family, the top-level block, the set's block. `tiers` and
`phase2_tiers` merge key by key; `phase2_tiers` falls back to `tiers`.

| key | default | meaning |
|---|---|---|
| `recall` | `all` (`bcp`: `no_seed`) | which live factual hops get a recall pass: `all`, `no_seed` (hops Stage 1 cited nothing for, typically the audit's added hops), `none` |
| `recall_k` | 20 (`bcp`: 40) | candidates kept per recall hop (after the anchor filter) |
| `recall_hits`, `rrf_k`, `recall_min_queries` | 100, 60, 3 | hits per recall query; reciprocal rank fusion constant; queries per hop |
| `searchlog_top` | 0 | pool the top N results of each logged search (0: none, as the CMASS run did; `null`: all, which can be a thousand documents per question) |
| `keep_seed_docs` | `false` | keep a live hop's Stage 1 cited documents even when the judges drop them (labelled `partial`, source `seed`) |
| `chunk_chars`, `chunk_docs`, `part_chars` | 80,000, 15, 50,000 | one judge agent's chunk; one file an agent reads at once (a long document gets its own part) |
| `min_quote_chars` | 15 | shortest accepted quote |
| `trec_graded` | `false` | `qrels.trec` relevance 2 for a document with full support of some hop, else 1 |
| `judge_note` | `""` (`hotpotqa`: read hops as of the source snapshot) | a dataset rule `show` prints for the judges |
| `near_dup_mode` | `quote_first` | phase 2, after the [quote check](#near-duplicates-the-quote-check): `quote_first` (matches added, the rest to the agents), `agent` (no quote check, every near duplicate to the agents), `quote_only` (matches added, the rest dropped as `no_quote_match` without an agent) |
| `tiers` | see below | the second-judge policy of phase 1 |
| `phase2_tiers` | `{}` | overrides of `tiers` for near-duplicate verification |

```json
{"qrels": {"tiers": {"drop_rate": 0.2}},
 "sets": {"set_a": {"input": "inputs/set_a.jsonl", "family": "bcp", "qrels": {"recall_k": 40}}},
 "duplicates": {"path": "/data/cmass/corpus_duplicates", "threshold": 0.7, "id_pad": 5}}
```

`duplicates.path` is a directory of the released parquet files, one parquet file, or a `.json` / `.jsonl` file of the
same rows (`doc_id`, `exact_duplicates`, `near_duplicates` [{`doc_id`, `jaccard`}], `n_exact`, `n_near`), which needs
no pyarrow. `id_pad` (with `id_regex`, default `^(shard_\d+)_(\d+)$`) handles the two spellings of a ClimbMix id: the
map zero-pads the row to 5 digits, the corpus service does not. Lookups use the map's spelling and results are written
back in the corpus's; `null` uses ids as they are.

## Tiering policy

| tier | who (constants at the top of `phase1.js` / `phase2.js`) | what |
|---|---|---|
| prepare | `MODEL_PREPARE` (strong), one per question | hop standards (before any candidate is read), recall query families, `recall`, `chunks` |
| first judge | `MODEL_JUDGE` (cheaper), one per chunk | every candidate; a chunk whose verdicts still fail `check` is re-judged by `MODEL_RETRY` |
| second judge | `MODEL_SECOND`, blind, one per sample set | the round's sample (below) |
| adjudication | `MODEL_ADJUDICATE`, one per part | every document the two judges disagree on (keep vs drop), verdicts shown as A and B by hash |

A **policy round** samples, from the judged documents no earlier round considered: every partial-only keep (the first
judge's false keeps concentrate there), `keep_rate` of the full keeps (at least `min_keeps`), `drop_rate` of the drops
(at least `min_drops`; 1.0 re-judges every drop) and, with `all_seed_drops`, every dropped Stage 1 cited document. When
the policy rounds turn `escalate_at` or more sampled drops into keeps, a **drops-only round** re-judges every drop not yet
re-judged. A later policy round (a back-fill) covers documents added to the pool after round 1. Samples are deterministic
(by hash), so a re-render draws the same documents.

| parameter | default | `bcp` |
|---|---|---|
| `keep_rate` / `min_keeps` | 0.10 / 3 | same |
| `drop_rate` / `min_drops` | 0.20 / 3 | 1.0 (every drop) |
| `all_seed_drops` | true | same |
| `escalate_at` | 2 | same |

The final verdict for a document is the adjudicated one when there is one, else the first judge's. When the two judges
agree on keep, the first judge's hops stand (`compare` counts hop-level differences in `hop_differences`).

## Near duplicates: the quote check

Before any near duplicate is rendered for an agent, `dups.py chunks` checks it against its parent: for each live hop the
parent supports in the question's qrels, if one of the parent's recorded supporting quotes for that hop (at least
`min_quote_chars`, 15 characters) is verbatim in the near duplicate's text (`cmass.text.is_verbatim`, which ignores only
whitespace), the near duplicate inherits that hop with the parent's support label (`full` / `partial`), the matched
quote and `source` `near_dup_quote`. `near_dup_mode` decides the rest:

| `near_dup_mode` | matches at least one hop | matches no hop |
|---|---|---|
| `quote_first` (default) | added for exactly the matched hops, no agent | verified by the agents (`phase2.js`: first judge, second judge, adjudication) on its own text, against all live hops |
| `agent` | no quote check: verified by the agents | verified by the agents |
| `quote_only` | added for exactly the matched hops, no agent | dropped without an agent: `rejected_rN.jsonl`, reason `no_quote_match` |

A quote match shows that the copy still holds the sentence its parent was kept for. It does not look further: a hop the
copy supports only in its own words, or one its parent did not support, is found only by an agent. Only the parent the
lookup chose (exact first, then highest Jaccard) is checked. The route of every new near duplicate (`quote`, `agent`,
`no_quote_match`, or `not_in_corpus` under `quote_only`) is written to `phase2/quote_rN.jsonl`, and only the `agent` ones
are rendered; `dups.py chunks` prints the questions left for `phase2.js`, and a question with none left is complete
at once (`qrels_state.py --phase2 N`). The mode is read when `chunks` runs, so it can differ between rounds and sets.

**What the CMASS release used.** Near duplicates were first matched by their parent's verbatim supporting quotes. In the
first BrowseComp-Plus expansion round, the near duplicates that matched no quote were judged by an agent (`quote_first`);
in the other datasets and in later rounds, they were dropped without an agent check (`quote_only`).

## Commands, in order

Run inside the run directory; `Q=python3 <checkout>/pipelines/cmass/stage3/qrels.py --set <set>`,
`D=python3 <checkout>/pipelines/cmass/stage3/dups.py --set <set>`.

**Phase 1**

```bash
$Q pool                       # qrels/<set>/inventory.jsonl
$Q fetch                      # cache every pooled document (prints "ready <qid>" as each question completes)
# workflow phase1.js, args {"set": "<set>", "qids": [...]}: prepare -> judge -> second judge -> adjudicate
python3 <checkout>/pipelines/cmass/stage3/qrels_state.py --set <set> --all     # every question complete?
$Q build                      # qrels/<set>/phase1/
$Q verify                     # 0 problems before phase 2
```

By hand, per question: `show`, write `standards/<qid>.json`, `std-check`, write `recall/<qid>.queries.json`, `recall`,
`chunks`; judge each chunk, `check`; then while `qrels_state.py <qid>` reports a `next_round` or `pending_round`:
`second <qid> --round N [--drops-only]`, judge each set, `second-check`, `compare <qid> --round N`, adjudicate each
part, `adj-check`.

**Phase 2**

```bash
$D lookup --round 1           # phase2/pairs_r1.jsonl: exact and near duplicates of every kept document
$D fetch --round 1            # cache the new duplicates and their parents
$D chunks --round 1           # quote check (phase2/quote_r1.jsonl), render the rest; prints workflow_args
# workflow phase2.js, args {"set": "<set>", "round": 1, "qids": [...]} (skip it when "qids" is empty)
$D apply --round 1            # exact, quote-settled and verified near duplicates added: phase2/added_r1.jsonl
$D lookup --round 2           # duplicates of what round 1 added; repeat fetch/chunks/phase2.js/apply until "closure": true
$D build                      # qrels/<set>/phase2/ (refuses until closure)
$Q verify --phase phase2
```

## Outputs

`qrels/<set>/phase1/` and `qrels/<set>/phase2/` hold the same files (derived benchmark data: keep them in the run
directory, never in this repository):

| file | one line / entry per | fields |
|---|---|---|
| `qrels.jsonl` | question | `id`, `set`, `question`, `answer`, `answer_type`, `qrels` [doc ids] |
| `qrels_hops.jsonl` | question | `id`, `hops` [{`id`, `type`, `role`, `candidate`, `clue`, `derivation`, `inputs`, `docs`: {doc id: {`supports` full / partial, `source` stage2 / judged / adjudicated / seed / exact_dup / near_dup (agent verified) / near_dup_quote (quote check), `quotes`, (`parent`, `jaccard`)}}}] |
| `rejections.jsonl` | dropped (question, document) | `reason`, `note`, `provenance`, `adjudicated`, `second_judged` (phase 2 adds the near duplicates verification dropped and, under `quote_only`, those that matched no quote: reason `no_quote_match`) |
| `qrels.trec` | (question, document) | `<qid> 0 <doc id> <relevance>` |
| `stats.json` | set | totals, per question: pool, fixed, judged, kept and dropped by source, provenance and reason, second-judge and adjudication counts, uncovered hops, recall depth per hop |
| `expansion.jsonl`, `conflicts.jsonl` | added duplicate; exact duplicate not added | phase 2 only: parent, kind, Jaccard, round, `verdict_source` (`exact`, `quote` or `judged`, with `adjudicated`), hops; conflicts list exact duplicates that were judged and dropped or whose text differs from the parent's, for a reviewer |

Working files: `inventory.jsonl`, `standards/`, `recall/`, `work/<qid>/` (rendered chunks, `chunks.json`, sample
manifests `second*.json`, `adj_*.md`), `judged/<qid>/` (verdicts), and per expansion round `phase2/pairs_rN.jsonl`,
`phase2/quote_rN.jsonl`, `phase2/rN/{work,judged}/`, `added_rN.jsonl`, `rejected_rN.jsonl`, `conflicts_rN.jsonl`.

`verify` reads the Stage 2 records and the output files independently of the inventory and fails when: a question has
no qrels; an accepted evidence document of a live hop is missing from the question's qrels or from that hop's
documents; a live factual hop keeps no document; a derivation hop's input keeps none; a kept document supports no hop;
a recorded quote is not verbatim in its document; an exact duplicate's text differs from its parent's; `qrels.trec`
differs from `qrels.jsonl`; or (with `keep_seed_docs`) a live hop's Stage 1 document is missing.

## Demo and tests

[`../demo/test_stage3.py`](../demo/test_stage3.py) runs every deterministic step above on the invented demo corpus,
offline, with [`../demo/make_stage3_fixtures.py`](../demo/make_stage3_fixtures.py) standing in for the agents (the
fixture includes a deliberately over-generous second judge, so the escalation round runs; near duplicates settled by
their parent's quote, a re-punctuated copy that matches no quote and is judged, and a near duplicate that lost its
parent's sentence; and `near_dup_mode` `agent` and `quote_only` on copies of the phase-1 run):

```bash
python3 pipelines/cmass/demo/test_stage3.py      # ends with "all Stage 3 demo checks passed"
```

The workflows cannot run offline; check their syntax by wrapping a copy in `(async () => { ... })()` (with
`export const meta` made a plain `const`) and running `node --check` on it.

## Things that will bite

- **Two spellings of one document id.** The duplicate map pads the row of `shard_<shard>_<row>` to five digits; the
  corpus service and the qrels do not. An unnormalised join does not fail, it silently finds fewer duplicates. Keep
  `duplicates.id_pad` set for ClimbMix.
- **Do not materialise the duplicate map.** It has one list-of-struct row per document with any duplicate (219M rows
  for ClimbMix); `dups.py` filters on `doc_id` before converting rows to Python objects.
- **Recall queues host-wide.** The corpus service is paced across processes; one question's recall runs at a time
  (`qrels_recall.lock` in the corpus lock directory), so a waiting agent should block on the candidates file, not
  re-issue the command.
- **A pool that grows after round 1** (recall re-run, new search logs) leaves documents no second-judge round
  considered; `qrels_state.py` then reports a back-fill `next_round`, and `build` refuses the question until it is done.
- **Workflow resume replays by call order.** Resuming a workflow run with different arguments re-runs everything
  after the first change; pass the same `qids` list (`dups.py chunks` prints it for phase 2).
- **A truncated read is a wrong judgment.** Chunk parts are at most `part_chars`; a part too large for one Read is read
  with offset/limit, never judged from its first screen.
