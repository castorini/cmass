# Stage 3: qrels construction

Turns the questions Stage 2 accepted into relevance judgments: for each question, the union of the corpus documents
that support its reasoning hops, per hop and per question. Three phases, as in the paper's Stage 3 paragraph:

- **Phase 1, the precision filter** (`qrels.py`, workflow `phase1.js`): pool every document retrieved for the question
  during projection, audit and revision, read each in full, and keep it only for the hops it supports.
- **Phase 2, duplicate expansion** (`dups.py`, workflow `phase2.js`): add the exact duplicates of every kept document,
  and the near duplicates (5-gram Jaccard ≥ 0.7) that hold their parent's supporting quote verbatim (the
  [quote check](#near-duplicates-the-quote-check)) or that an agent has verified on their own text.
- **Phase 3, system pooling** (`pool.py`; workflows `phase1.js` with `pool: true`, then `phase2.js` with
  `layer: phase3_pool_dups`): once retrieval systems have been evaluated, pool the documents they surfaced or opened,
  as TREC pools participants' runs; judge the ones never judged whose text matches a hop rule ([`RULES.md`](RULES.md)),
  with the same tiered review; add the kept ones to a new qrels version and expand their duplicates as in phase 2.
  See [System pooling](#phase-3-system-pooling-poolpy).

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
| documents retrieved by the evaluated agents, pooled as in TREC | `pool.py candidates` | the system pool (one row per question and document any system surfaced or opened), minus the pairs already qrels or judged before |
| judged only if its text matches one of the question's hop rules | `rules.py` ([`RULES.md`](RULES.md)), `pool.py candidates` | per hop, literal phrase rules that match every established qrel of the hop; a pooled document matching no rule of its question is not judged |
| the same tiered judging, blind to the systems | `phase1.js` (`pool: true`), `qrels.py --pool`, `pool.py build` | the judges read each candidate against all live hops with the phase-1 standards; neither the systems nor the matched rule is shown |
| duplicates of the additions | `dups.py --layer phase3_pool_dups` | phase 2's procedure on the documents phase 3 added |
| which systems the new qrels came from | `pool.py stats` | every added qrel credited to the systems that retrieved it; a duplicate to those of the pooled document it copies |

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
| `phase2_tiers` | `{}` | overrides of `tiers` for near-duplicate verification (both duplicate expansions) |
| `pool_tiers` | `{}` | overrides of `tiers` for the system pool's candidates (phase 3) |

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

**What the CMASS release used.** Near duplicates were first matched by their parent's verbatim supporting quotes. The
near duplicates that matched no quote were judged by agents (`quote_first`) in the first BrowseComp-Plus round, for
HotpotQA (whose expansion was redone after a re-judge) and in the expansion of the system pool's additions (phase 3,
every dataset); in the other phase-2 rounds of BrowseComp-Plus, TRQA and DeepSearchQA they were dropped without an
agent check (`quote_only`). In that run, the near duplicates of phase 3 and of HotpotQA's redone phase 2 were rendered
for the agents before the quote check ran, so 54 copies (32 and 22) were both settled by quote and judged (and dropped)
by the agents; a blind adjudicator re-read each
copy and its parent against the hop and decided both, and the decisions were applied to the qrels. `dups.py chunks`
runs the quote check before rendering, so a copy here is either settled by quote or judged, never both.

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

**Phase 3** (after the systems are evaluated; `P=python3 <checkout>/pipelines/cmass/stage3/pool.py --set <set>`): see
[below](#phase-3-system-pooling-poolpy).

## Phase 3: system pooling (`pool.py`)

Phases 1 and 2 judge what the pipeline's own agents found. Once retrieval systems have been evaluated on the questions,
the documents they surfaced or opened and the qrels never judged are a second source of evidence, the pool TREC builds
from participants' runs ([`evaluation/SYSTEM_RUNS.md`](../../../evaluation/SYSTEM_RUNS.md) exports it). Judging every
pooled document is costly and mostly wasted on off-topic pages, so a pooled document is judged only if its text
matches one of its question's **hop rules**: per hop, conjunctions of literal phrases written by `rules.py`'s agent
under [`RULES.md`](RULES.md)'s coverage contract (the rules of a hop match every document already established for it).
The judges then read each candidate against all live hops with the phase-1 standards, by the same tiered review,
without being told which systems retrieved it or which rule it matched. The kept documents are added to the phase-2
qrels as a new version (`phase3_pool/`), and their duplicates are expanded as in phase 2 (`phase3_pool_dups/`, the
final qrels).

```bash
$P candidates --pool pools/<dataset>.jsonl --rules passing.jsonl [--corpus /data/climbmix]
                              # phase3_pool/pool_pairs.jsonl (every pooled pair and its status),
                              # candidates.jsonl (never judged, matching a hop rule), candidates_summary.json
$P fetch                      # cache the candidates' text (already cached when --corpus read them)
$P chunks                     # phase3_pool/work/QID/chunk_NN_*.md; prints workflow_args
# workflow phase1.js, args {"set": "<set>", "qids": [...], "pool": true}: judge -> second judge -> adjudicate
#   (by hand: qrels.py --pool show|check|second|second-check|compare|adj-check; state: qrels_state.py --pool)
$P status                     # every question complete?
$P build                      # phase3_pool/: phase 2 plus the kept candidates
$Q verify --phase phase3_pool
D3="$D --layer phase3_pool_dups"
$D3 lookup --round 1          # duplicates of the documents phase 3 added (phase 2 expanded the rest)
$D3 fetch --round 1 && $D3 chunks --round 1
# workflow phase2.js, args {"set": "<set>", "round": 1, "qids": [...], "layer": "phase3_pool_dups"}
$D3 apply --round 1           # then lookup --round 2 ... until "closure": true
$D3 build                     # phase3_pool_dups/: the final qrels
$Q verify --phase phase3_pool_dups
$P stats                      # phase3_pool/systems_phase3_pool_dups.json: funnel and credit per system combination
```

- **Inputs.** The pool is JSONL, one row per question and document: `query_id`, `doc_id`, `systems` as
  `{name: {"surfaced": bool, "opened": bool}}` (the export's format) or a list of names. The rules are `rules.py
  collect`'s output, groups `<set>/<qid>/<hop>`; a question without rules contributes no candidate, and a rule naming
  a hop that is not live is an error. `--corpus` reads the texts from a local copy of the corpus, as `scan.py` does,
  and stores them in the run's document cache, so the judges read exactly the text the rules matched. Without it the
  texts come from the corpus service.
- **Statuses** of a pooled pair:
  - `already_qrel`: a phase-2 qrel.
  - `judged_before`: a verdict in phase 1 or a phase-2 round, or a phase-2 rejection.
  - `no_rules`, `not_in_corpus`, `unmatched`, `candidate`.
  `candidates` refuses to replace a list that judging has started on.
- **Tiers.** `pool_tiers`, which falls back to `tiers`, so a family keeps its phase-1 policy (BrowseComp-Plus
  re-judges every drop).
- **Credit** (`stats`). Every qrel the final layer adds over phase 2 is credited to the combination of systems that
  retrieved it. A duplicate is credited to the systems of the pooled document it copies, traced through earlier rounds
  and kept even when that original was later dropped. `unattributed` counts the rest (0 when every addition traces
  back).

**What the CMASS release ran.** The pools were those of the three agents the paper evaluates (GPT-6.1 Sol, Gemma 4 31B
and Qwen 3.5 9B over BM25):
- **Pool:** 49,885 question-document pairs, of which 41,312 were neither qrels nor judged before.
- **Rules:** 6,036 rules over 2,693 hops, with a breadth cap of 20,000 estimated documents.
- **Judged:** 4,403 rule-matched candidates.
- **Kept:** 3,102 judged candidates, plus 354 of their duplicates.

The released qrels, `v2026.10.25` of `castorini/cmass`, are `phase3_pool_dups`. The pool favours the three pooled
systems: their finds were judged, while a new system's unjudged finds still count as misses.

## Outputs

`qrels/<set>/phase1/`, `phase2/`, `phase3_pool/` and `phase3_pool_dups/` hold the same files, each layer a superset of
the one before (derived benchmark data: keep them in the run directory, never in this repository):

| file | one line / entry per | fields |
|---|---|---|
| `qrels.jsonl` | question | `id`, `set`, `question`, `answer`, `answer_type`, `qrels` [doc ids] |
| `qrels_hops.jsonl` | question | `id`, `hops` [{`id`, `type`, `role`, `candidate`, `clue`, `derivation`, `inputs`, `docs`: {doc id: {`supports` full / partial, `source` stage2 / judged / adjudicated / seed / exact_dup / near_dup (agent verified) / near_dup_quote (quote check), `quotes`, (`parent`, `jaccard`)}}}] |
| `rejections.jsonl` | dropped (question, document) | `reason`, `note`, `provenance`, `adjudicated`, `second_judged` (phase 2 adds the near duplicates verification dropped and, under `quote_only`, those that matched no quote: reason `no_quote_match`) |
| `qrels.trec` | (question, document) | `<qid> 0 <doc id> <relevance>` |
| `stats.json` | set | totals, per question: pool, fixed, judged, kept and dropped by source, provenance and reason, second-judge and adjudication counts, uncovered hops, recall depth per hop |
| `expansion.jsonl`, `conflicts.jsonl` | added duplicate or pooled document; exact duplicate not added | phase 2 and phase3_pool_dups: parent, kind, Jaccard, round, `verdict_source` (`exact`, `quote` or `judged`, with `adjudicated`), hops; phase3_pool: `source` `system_pool`, `systems`, `surfaced`, `opened`, the matched rule `groups`, `verdict_source` (`judged` / `adjudicated`), hops with support and quote. Conflicts list exact duplicates that were judged and dropped or whose text differs from the parent's, for a reviewer, and are carried into later layers |

A hop map entry of a pooled document has `source` `system_pool` and its `systems`; its rejections carry `phase` 3 and
`layer`.

Working files: `inventory.jsonl`, `standards/`, `recall/`, `work/<qid>/` (rendered chunks, `chunks.json`, sample
manifests `second*.json`, `adj_*.md`), `judged/<qid>/` (verdicts), and per expansion round `phase2/pairs_rN.jsonl`,
`phase2/quote_rN.jsonl`, `phase2/rN/{work,judged}/`, `added_rN.jsonl`, `rejected_rN.jsonl`, `conflicts_rN.jsonl`
(the same under `phase3_pool_dups/` for the pool's duplicates). Phase 3 adds `phase3_pool/pool_pairs.jsonl`,
`candidates.jsonl`, `candidates_summary.json`, `work/`, `judged/` and `systems_<layer>.json`.

`verify` reads the Stage 2 records and the output files independently of the inventory and fails when: a question has
no qrels; an accepted evidence document of a live hop is missing from the question's qrels or from that hop's
documents; a live factual hop keeps no document; a derivation hop's input keeps none; a kept document supports no hop;
a recorded quote is not verbatim in its document; an exact duplicate's text differs from its parent's; `qrels.trec`
differs from `qrels.jsonl`; or (with `keep_seed_docs`) a live hop's Stage 1 document is missing.

## Corpus scan: complete candidates for broad hops (`scan.py`)

Recall takes the BM25 top `recall_k` per hop, an arbitrary slice of a broad hop with many supporting documents.
`scan.py` reads every document of the corpus once and returns, for each hop, every document its match rules accept, so
the hop's candidate set is complete with respect to its rules. One pass serves all hops of all questions. The format,
matching semantics and outputs are in the module docstring.

The same rules filter the system pool in phase 3 (`pool.py candidates` matches them against the pooled documents
only). In the CMASS release the full scan served to measure and check the rules: every pooled document a rule matched
was also among the scan's matches. Its candidates outside the system pool (2.64 M hop-document pairs) were **not**
judged, so the broad hops' qrels are complete only for what the pipeline's agents and the three systems retrieved.

- **A rule** is a JSON line `{"rule_id", "group", "all": [[alt, ...], ...], "none": [alt, ...]}`. A document matches
  when every clause of `all` has one of its phrases and no phrase of `none` occurs. Phrases are literal and
  case-insensitive, matched on word boundaries after whitespace normalisation, so `IUU` does not match inside
  `Taliuuna` or `IUU_2`.
- **A group is one hop** (e.g. `<set>/<qid>/<hop>`), and it matches the union of its rules. A hop may have as many
  rules as it needs.
- **The coverage contract.** A hop's rules must match 100% of the hop's established qrels. They are validated against
  those qrels before the scan, and a hop is not scanned until its group passes. Several rules per hop make this always
  achievable: a known document that no rule matches gets a rule of its own kind, not a looser rule for everyone.

```bash
S=python3 <checkout>/pipelines/cmass/stage3/scan.py
$S check --rules rules.jsonl --known hop_qrels.jsonl --corpus /data/climbmix --write-passing passing.jsonl --json check.json
#   hop_qrels.jsonl: {"group", "doc_id"} per established qrel; exit 1 while any group is below 100%;
#   per group: matched/known, missed ids, the rules carrying each document (--docs), why a rule missed (--why)
$S scan --corpus /data/climbmix --rules passing.jsonl --out scan_out [--workers 32] [--resume] [--shards A:B]
$S merge --out scan_out       # counts.json (per group and per rule), matches/<group>.txt, rule_matches/<rule_id>.txt
```

`--max-per-rule N` and `--max-per-group N` stop recording ids after N per shard. The counts stay exact. Each shard
writes `shards/<shard>.{matches,groups}.jsonl`, then `done.json`, all atomically. `--resume` skips the done shards,
and an output directory refuses a scan with other rules. For ClimbMix, document ids are `shard_<NNNNN>_<row>` with
the row unpadded, as the corpus service spells it. `check` also accepts the padded spelling the duplicate map uses.

**Throughput.** Measured on one ClimbMix shard (86,016 documents, 254 M characters) on the 96-core host, with
invented rules (`../demo/scan_bench.py`, which prints timings only). Per worker it includes reading, normalisation,
matching and writing; peak RSS is 167 MB per worker.

| matcher | rules (distinct phrases) | docs/s per worker | 32 workers running at once | full pass, 563 M docs, 32 workers |
|---|---|---|---|---|
| `aho` (pyahocorasick 2.3.1) | 300 (287) | 10,100 | 8,500 per worker | 0.6 h |
| `aho` | 3,000 in 1,000 groups (299) | 6,900 | not measured | about 0.8 to 0.9 h |
| `token` (stdlib fallback) | 300 (287) | 4,500 | not measured | 1.1 h or more |

The automaton scans about 45 M characters/s whatever the number of phrases (tested to 30,000 phrases). The `token`
fallback slows as phrases sharing a common word multiply: 14 M characters/s at 300 phrases, 7 M at 3,000, 1 M at 30,000.
Install pyahocorasick for a full pass. Disk reads were not measured, because only one shard was read: the full pass
reads 559 GB of compressed parquet, about 270 MB/s sustained at 0.6 h. Two other designs were rejected:
- **One Python regex alternation:** 5 M characters/s at 300 phrases and 0.3 M at 3,000.
- **An RE2 alternation through pyarrow:** fast at 300 phrases, but it collapses to about 1 M characters/s past
  1,000 phrases, once its DFA memory budget runs out.

[`../demo/test_scan.py`](../demo/test_scan.py) runs both matchers against a brute-force reference, and every command
above, on invented documents (`../demo/scan_corpus/`):

```bash
python3 pipelines/cmass/demo/test_scan.py        # ends with "all scan demo checks passed"
```

### Writing the rules (`rules.py`, workflow `rules.js`)

The rules are written per question by an agent following [`RULES.md`](RULES.md): for every hop with qrels, a group of
rules (`<set>/<qid>/<hop>`) that matches **100%** of the hop's established qrels documents and stays as specific as it
can, at most `max_est` documents estimated over the corpus (default 20,000) unless the rules record why the fact is
genuinely that common (`"meta": {"broad_ok": "<reason>"}`). A good rule has a clause of the subject's names and
aliases and one or more clauses of the fact's key terms; a known document the general rule cannot reach gets a narrower
rule of its own instead of a looser general rule. `rules.py` (stdlib + pyarrow; it imports `scan.py`'s loader, matcher,
shard scanner and `check`) prepares the agent's work, checks it, measures it and decides the state from disk; the
format details are in its docstring.

| option (flag, or key of the `--config` JSON) | what |
|---|---|
| `qrels_hops` | `qrels_hops.jsonl` (phase 1 or 2 output): per question `id`, `hops` [{`id`, `kind` or `type`, `clue`, `docs`: {doc id: `full` / `partial` / `audit`, or {`supports`, `quotes`, ...}}}]; derivation hops, dropped hops and hops without documents get no rules |
| `qrels`, `standards`, `quotes` | optional: `qrels.jsonl` (question and answer for the packet), `standards/` (hop standards), a JSONL of `{id, hop, doc_id, quote}` for formats whose hops carry no quotes |
| `corpus`, `id_scheme` | the corpus as `scan.py` reads it (ClimbMix parquet shards, or JSONL); every text is read locally |
| `set`, `out` | groups are `<set>/<qid>/<hop>`; files live under `<out>/<set>/` |
| `max_est`, `breadth_sample`, `breadth_shards` | the breadth cap per group (20,000) and the standard sample (24 shards spread evenly over the corpus, or a shard range) |
| `breadth_workers`, `breadth_parallel`, `matcher` | worker processes per breadth run (24), breadth runs at once under `out` (2; others wait for a slot), `scan.py` matcher |

```json
{"qrels_hops": "qrels/set_a/phase2/qrels_hops.jsonl", "qrels": "qrels/set_a/phase2/qrels.jsonl",
 "standards": "qrels/set_a/standards", "corpus": "/data/climbmix", "set": "set_a", "out": "scan_rules"}
```

Relative paths in the config file are relative to the file. Per question, under `<out>/<set>/`:
`<qid>.rules.jsonl` (the agent's rules, the only file it writes), `<qid>.known.jsonl` (`{group, doc_id}` per known
document: `scan.py check --known`), `work/<qid>.md` (the packet), `work/<qid>.check.json`, `work/<qid>.breadth.json`
(and `breadth_custom.json` for a non-standard sample); `collect/summary.json` for the set.

```bash
R="python3 <checkout>/pipelines/cmass/stage3/rules.py --config rules_config.json"
$R state --todo                  # per question: the next action (below)
$R packet QID                    # work/QID.md: each hop (clue, standard) and each known document with an excerpt
                                 #   (its recorded quotes in context, else the window around the clue's rarest terms,
                                 #   else the start), plus QID.known.jsonl
$R doc QID DOCID --grep PHRASE   # a document's text, or the normalised text around phrases, with boundary counts
$R check QID                     # scan.py check of the question's groups: per hop matched/known, missed ids, why
$R breadth QID                   # the question's rules on the shard sample: matches and corpus-wide estimates
# workflow rules.js, args {"run": "<run dir>", "tools": "<checkout>/pipelines/cmass/stage3", "set": "<set>",
#                          "qids": [...], "config": "rules_config.json"}: one agent per question until done
$R collect --out-rules passing.jsonl   # the done questions' rules, re-checked together: the --rules of scan.py scan
```

**State** (`rules.py state [QID ...] [--json] [--brief] [--todo]`), from disk only and deterministic. The check and
breadth reports carry the `scan.py` rules digest (rule ids, groups, `all`, `none`; not `meta`) and the known documents'
digest, so any change to a rule makes them stale, and adding a `broad_ok` reason does not.

| next | when | then |
|---|---|---|
| `packet` | no packet, or the known documents changed since it was written | `packet` |
| `write` | no rules file, or it does not validate: `scan.py`'s format, a group that is not one of the question's hops, a `rule_id` not under its group, a hop without a rule, a `broad_ok` reason under 10 characters | write or fix the rules |
| `check` | no check report for these rules and these known documents | `check` |
| `fix_coverage` | a hop's group misses known documents (or one cannot be read) | add a variant or a rule of its own |
| `breadth` | no standard-sample breadth report for these rules | `breadth` |
| `narrow` | a group's estimate is over `max_est`, and not every over-cap rule of it carries `broad_ok` | narrow the group |
| `done` | rules valid, every hop at 100%, every group within the cap or `broad_ok` | `collect` |
| `skip` / `error` | no hop with qrels documents / the question is unknown or its ids cannot name a group | nothing / fix the inputs |

**The workflow** (`rules.js`) runs one agent per question (a pipeline over the questions, the session's model), each
reading `RULES.md` and its packet, writing the rules and running `check` and `breadth` until `state` says done; at most
three agents per question, and the result lists every question that is not done with the misses and estimates the state
reports. Every decision comes from `rules.py state`, read by a mechanical agent, never from an agent's report.

**Breadth cost.** On ClimbMix the standard sample is every 272nd of the 6,543 shards: 24 shards, 2,027,520 documents,
so an estimate is the sample count x 272.6 (one sample match is worth about 273 documents; the 20,000 cap is about 73).
Measured on the 96-core host with pyahocorasick, 24 workers and two invented rules: 5.9 s to scan the sample (6.8 s
wall with start-up), 6.8 s on a repeat; another 25-shard sample took 9.6 s. `collect` re-reads every known document
once, as `scan.py check` does.

[`../demo/test_rules.py`](../demo/test_rules.py) runs packet, doc, check, breadth (on a two-shard parquet corpus built
in a temporary directory), every state transition and collect on invented questions over invented documents
(`../demo/rules_corpus/`): hops whose known documents need two rules or an alias, a hop that starts too broad, a
genuinely broad hop with `broad_ok`, and invalid rule files:

```bash
python3 pipelines/cmass/demo/test_rules.py       # ends with "all rules demo checks passed"
```

## Releasing the qrels (`release.py`)

`release.py` writes a layer's qrels in the obfuscated format of the Hugging Face dataset, the one
[`scripts/deobfuscate.py`](../../../scripts/deobfuscate.py) decodes: `qrels.jsonl`, `qrel_roles.jsonl`,
`qrels_inclusion.jsonl` and `qrels_exclusion.jsonl`. The document ids and question ids are XOR-encoded with the
repeated SHA-256 of the split's canary. A document is `exclusion` only if every hop it supports is an exclusion hop,
so the two views partition the qrels. It decodes what it wrote and checks it against the layer before exiting.

```bash
python3 <checkout>/pipelines/cmass/stage3/release.py --set <set> --layer phase3_pool_dups --out /private/release/<split> \
    --queries <released split>/queries.jsonl        # the canary and the question order of the released queries
#   or --canary "<text>" for a new split; --hop-roles ROLES.jsonl ({id, hop, role}) overrides the hops' roles
```

`v2026.10.25` was written this way, with the hop roles of the paper's role analysis. Applied to the phase-2 qrels,
the same procedure reproduces every qrel file of `v2026.10.20` byte for byte. The output is benchmark data: write it
outside this repository.

## Demo and tests

[`../demo/test_pool.py`](../demo/test_pool.py) runs phase 3 end to end on top of the demo run that `test_stage3.py`
builds. It uses three invented systems' pool and invented phase-3 documents, served by a wrapper around the demo corpus
so the shared corpus is unchanged. The pool covers pairs already in the qrels, judged before, of a question without
rules, matching no rule, and matching a hop rule, read once through the corpus service and once from a local JSONL
corpus. Judging includes a disagreement settled by the adjudicator. The pool's duplicates include an exact copy, a
near copy settled by quote, and one that lost the quoted sentence. The test ends with the per-system credit and
`release.py`, decoded with `scripts/deobfuscate.py`:

```bash
python3 pipelines/cmass/demo/test_pool.py        # ends with "all system-pool demo checks passed"
```

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
