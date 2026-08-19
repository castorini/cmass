---
name: qrels-construction
description: Build and audit per-question relevance judgments for a projected benchmark - join a reviewer workbook with verified questions, judge candidate documents against hop standards, expand the pool over exact and near duplicates, and produce the distribution figures. Use when asked to build qrels, finalize relevance judgments, expand judgments over duplicates, check how redundant a judged pool is, or make qrels figures. Triggers include "qrels", "relevance judgments", "judge documents", "duplicate expansion", "near duplicate qrels", "how many relevant documents per question".
---

# Qrels construction

Runs over `pipelines/bcp_climbmix/qrels/` in this repository. Every step is
CLI-driven, takes `--work-dir`, and is resumable per question.

## Pick the entry point by what is being asked

| ask | run |
|---|---|
| "build the qrels" | `step0` → `step1` → judging loop → `step4` |
| "what still needs judging for question X" | `step2_render_worksheet.py --qid X` |
| "expand over duplicates" | `step5_expand_duplicates.py scan` then the read/apply loop |
| "how far along is the expansion" | `step5_expand_duplicates.py status` |
| "how many relevant docs per question / per hop" | `analysis/qrels_stats.py` |
| "how redundant is the pool" | `analysis/qrels_near_dup.py` |

Inputs are environment variables (`QRELS_WORKBOOK`, `QRELS_QUESTIONS`,
`QRELS_DOC_CMD`, `QRELS_NEAR_DUP_DIR`, `QRELS_EXACT_DUPS`) or the matching
flags. Nothing is hardcoded; a missing input fails with the flag and variable
to set, not a stack trace.

## What is released and what is not

The release is **per-question** relevance judgments. The hop decomposition and
the per-hop assignments are internal: they drive judging and auditing, and they
stay out of the release. Do not commit `work/`, `out/`, or any JSONL the
pipeline produces — `.gitignore` covers them, so check `git status` before
adding files rather than adding a directory wholesale.

## Judging discipline

This is the part that decides quality, and it is not automatable.

- **Write the hop standard before reading.** What must a document state, and what
  near miss must it reject? A bar set while looking at a candidate drifts toward
  whatever the candidate happens to say.
- **Read every document in full.** Not the workbook excerpt, not a truncated
  render. The deciding sentence is frequently past the first screen, and two
  copies of the same page often differ exactly there.
- **Record only rejections**, each with the sentence that decides it *and* a
  contrast case — a document that does pass, and why. A rejection with no
  contrast is the one most likely to be wrong.
- **Attribution is the common failure.** On a page covering several entities, the
  sentence that would support the hop often belongs to a different subject. Check
  whose sentence it is before accepting.
- **Precision over recall**, and never relax a standard to make a hop non-empty.
  A kept hop with no support is reported by step 4 for review; that is the
  correct outcome, not a reason to lower the bar.

## Two views of duplication — do not conflate them

- **Affected**: documents that *have* a near-duplicate partner in the same
  query's qrels. The right number for review scope: if a judged document has a
  twin, both need looking at.
- **Removable**: copies beyond one representative per cluster. The right number
  for characterising redundancy.

`analysis/qrels_near_dup.py` reports both (`n_near_dup` and `n_redundant`).
Reporting "affected" as a redundancy figure overstates it.

## Things that will bite

**Two document-id spellings.** Corpus artifacts zero-pad the sequence
(`shard_00045_06622`), the retrieval API and qrels do not (`shard_00045_6622`).
An unnormalised join silently returns fewer duplicates than exist — no error,
just a quietly incomplete answer. Normalise on every join.

**`to_pylist()` on a duplicate column** materialises one list-of-struct row per
corpus document. Use Arrow `.take()` to filter first.

**Inclusive integer buckets drop continuous values.** `0–9` then `10–19` loses
everything between 9 and 10. Use half-open `[lo, hi)` and assert the buckets
account for every input.

**Figures are authored at final size.** They are saved on a fixed canvas, not a
tight bounding box, so LaTeX does not rescale them and the point sizes hold. A
tight bbox grows the canvas to fit the labels, which silently changes the scale
factor and shrinks the text on the page.
