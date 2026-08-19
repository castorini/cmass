# Qrels construction

Builds the released relevance judgments for the projected benchmark from a
reviewer workbook, then expands them over the corpus duplicate graph.

The released qrels are **per question**: a set of relevant documents for each
question. The per-hop assignments this pipeline works with internally are used
to build and audit that set, and are not part of the release.

Everything is CLI-driven; no step has a path baked in. Set the three inputs once
and the rest is defaults:

```bash
export QRELS_WORKBOOK=/path/review.xlsm            # reviewer workbook
export QRELS_QUESTIONS=/path/verified.jsonl        # verified questions
export QRELS_DOC_CMD="python3 cm.py doc"           # retrieval CLI, `doc` half
export QRELS_NEAR_DUP_DIR=/path/near_dup/doc_duplicates_t70
export QRELS_EXACT_DUPS=/path/duplicate_groups.jsonl
export QRELS_WORK_DIR=work                         # everything is written here
```

`cm.py` is the same two-command retrieval CLI the rest of this pipeline uses
(see [../README.md](../README.md)); only `doc <docid>` is needed here.

## Steps

| step | script | what happens |
|---|---|---|
| 0 | `step0_build_candidates.py` | workbook × verified questions → one candidate pool per question |
| 1 | `step1_fetch_docs.py` | cache the full text of every candidate document |
| 2 | `step2_render_worksheet.py` | print what still needs a judgment, full text, one question at a time |
| 3 | `step3_record_verdicts.py` | rejection list → verdicts file |
| 4 | `step4_build_qrels.py` | candidates + verdicts → qrels JSONL |
| 5 | `step5_expand_duplicates.py` | expand over exact and near duplicates, to closure |
| 6 | `analysis/qrels_stats.py`, `analysis/qrels_near_dup.py` | distributions and paper figures |

```bash
python3 step0_build_candidates.py
python3 step1_fetch_docs.py --all
python3 step2_render_worksheet.py --qid 78          # read; decide
python3 step3_record_verdicts.py --all
python3 step4_build_qrels.py --out work/qrels.jsonl
```

## The judging loop (steps 2–3)

Steps 2 and 3 are the manual/agentic core, and the reason the pipeline is
resumable per question. For each question:

1. Write the standard for each hop **before** reading — what a document must
   state to support it, and what near miss it must reject. Deciding the bar
   while looking at a candidate is how a pool drifts.
2. Read every pending document **in full** from the step-1 cache. Never judge
   from the workbook excerpt or from a truncated render: the deciding sentence
   is often past the first screen, and phrasing varies between copies of the
   same page.
3. Record only the failures, in `work/rejections/<qid>.json`, each with the
   sentence that decides it and a contrast case. Everything unrecorded is
   accepted, which keeps the file small and the reasoning auditable.

Two rules the reviewer set, both encoded in step 0:

- A hop is skipped **only** when judged "No" *and* the note says it is not
  needed. A bare "No" is judged normally; if it ends with no support, step 4
  reports it in `needs_review` rather than dropping it.
- "Supports" / "Partial support" votes are guaranteed to reach the output.
  "Does not support" binds unless an explicit, conservative override is
  recorded.

## Duplicate expansion (step 5)

A judged document usually has twins in the corpus, and a retriever that returns
an unjudged copy of a relevant document should not be scored as wrong.

- **Exact duplicates** inherit the parent's hops with no reading.
- **Near duplicates are not inherited.** A copy at Jaccard 0.7 can drop the very
  sentence that grounded a hop, so each is read and judged on its own — and
  against *all* live hops of the query, not only the parent's, since a fuller
  copy can support more than its parent did.

Run to closure: accepting a document puts its own neighbours on the frontier.

```bash
python3 step5_expand_duplicates.py scan --qrels work/qrels.jsonl
python3 step1_fetch_docs.py --ids-file work/expand/fetch_ids.json
python3 step5_expand_duplicates.py render --qid 78 --qrels work/qrels.jsonl
python3 step5_expand_duplicates.py apply  --qid 78 --decisions /tmp/d78.json
python3 step5_expand_duplicates.py scan --qrels work/qrels.jsonl   # until empty
python3 step5_expand_duplicates.py export --qrels work/qrels.jsonl --out work/qrels_expanded.jsonl
```

`scan` reports `nothing to scan; closure reached` when no included document has
an unchecked neighbour left.

## Figures

```bash
python3 analysis/qrels_stats.py work/qrels_expanded.jsonl --outdir out --png
python3 analysis/qrels_near_dup.py work/qrels_expanded.jsonl --outdir out --png
```

Figures are authored at the width they will occupy on the page (`--figwidth`,
inches) and saved on a fixed canvas rather than a tight bounding box, so
`\includegraphics` never rescales them and the point sizes on the page are the
ones set here. See `analysis/figstyle.py`.

## Things that will bite

**Document ids have two spellings.** The corpus analysis artifacts zero-pad the
sequence (`shard_00045_06622`); the retrieval API and the qrels do not
(`shard_00045_6622`). Joining without normalising finds nothing for every id
whose sequence is under five digits — and it does not error, it just silently
returns fewer duplicates than exist. `step5_expand_duplicates._ana` / `._api`
normalise on every join; keep them.

**Do not `to_pylist()` a whole duplicate column.** The near-duplicate parquet has
one list-of-struct row per corpus document; materialising the column costs
minutes per shard and gigabytes. Filter to the rows you want with Arrow `.take()`
first.

**Percentage histograms need half-open buckets.** Inclusive integer ends
(`0–9`, `10–19`) silently drop every value between 9 and 10. The bucketing
asserts it accounted for every input.

**A truncated render is a wrong judgment.** Both render steps print a `TRUNCATED`
marker and the flag to raise. Re-render rather than judge what you can see.
