# Contamination screen (HotpotQA, Stage 1)

You screen ONE HotpotQA question, projected onto the corpus, for **contamination**. This runs BEFORE
any quality filtering. You are not judging whether the question is well grounded - only whether it is
spoiled in one of two specific ways:

- **(a) Leakage**: a single document poses the question and gives the gold answer, so a system would be
  graded against a copy of the answer key.
- **(b) Single-document answerability**: one document alone carries every fact the question needs, so
  nothing multi-hop is being tested.

The workflow (`contamination.js`) gives you the run directory, the set, the question id, the input file
and the output file. This guide is the instruction the original screen's agents followed, unchanged
except for the file locations and the corpus commands.

## Step 1: the question and its documents

Read the input file `stage1/<set>/input/<qid>.json`: `{qid, question, gold_answers, doc_ids}`. The
documents are the question's candidate documents: NanoKnow's qrels for a NanoKnow-supported question,
the retrieved pool for a ranked one. Read them with the corpus CLI, from the run directory (`<set>` and
`<qid>` as given; the CLI caches every document, so reading again costs nothing):

| what | command |
|---|---|
| find a phrase in a document, with 300 characters either side | `python3 <tools>/cm.py doc --stage 1 --step contamination --set <set> --qid <qid> <doc_id> --grep "<answer or key phrase>" --window 300` |
| count a document's synthetic Q&A blocks (ClimbMix carries many) | `python3 <tools>/cm.py doc --stage 1 --step contamination --set <set> --qid <qid> <doc_id> --grep "Question:"` (prints the number of matches, then the first four) |
| read a document whole | `python3 <tools>/cm.py doc --stage 1 --step contamination --set <set> --qid <qid> <doc_id> --max 1000000` |
| take an exact quote | `python3 <tools>/cm.py quote <doc_id> "<first words>" "<last words>"` |
| check a quote | `python3 <tools>/cm.py verify <doc_id> "<quote>"` |

Documents are large. Work efficiently: grep first, then read the regions that matter. Read whole
documents when one looks decisive. Judge only what the text says.

(The original agents read cached text files with `grep -o -i -n '.\{0,300\}<phrase>.\{0,300\}'` and
`grep -c -i 'Question:'`; the commands above are their equivalents. `--grep` matches a literal phrase,
case-insensitively.)

## Step 2: leakage

Is there a SINGLE document that hands over the answer key? It counts as leakage when that document:

- (a) POSES this question (or a question equivalent to it) AND states the gold answer - an explicit
  `Question: ... Answer: ...` block, a quiz, a flashcard page, a trivia list, an exam key, or a paper
  reproducing benchmark items; or
- (b) packages the question's FINAL hop as a ready-made Q&A whose answer is the gold answer, so the only
  work left is the lookup the question was built to require.

It is NOT leakage when a document merely shares wording with the question. These questions were written
from articles like these, so heavy overlap is the grounding working correctly. An encyclopaedic article
stating the facts in its own prose is exactly what SHOULD support the question.

Set `leaked`, `leak_doc`, `leak_kind` and `leak_evidence` (a verbatim quote of the offending text).
`leak_kind` is one of `poses-question-and-answers-it`, `quiz-or-answer-key-page`,
`packaged-qa-for-final-hop`, or `none` when nothing leaked.

## Step 3: single-document answerability

Ignoring leakage, could a reader answer this question correctly using ONE of these documents alone -
that is, does one document state every fact the question needs, including the bridging entity?

HotpotQA questions are meant to require combining two or more documents. If one document carries the
whole chain, nothing multi-hop is being tested.

Be strict about what "alone" means: the document must establish the bridge itself, not merely mention
the entities. If the reader still needs a second document to learn which entity the question is
pointing at, it is NOT single-document answerable.

Set `single_doc_answerable`, `single_doc`, and `single_doc_evidence` (verbatim quotes showing the whole
chain in that one document).

## Step 4: counts

Report `hops_needed` (how many distinct facts the question genuinely requires) and
`distinct_docs_needed` (the smallest number of these documents that together carry them).

## Step 5: verdict and output

`clean` = NOT `leaked` AND NOT `single_doc_answerable`.

Write the output file `stage1/<set>/contamination/<qid>.json`:

```json
{"qid": "<qid>", "leaked": false, "leak_doc": "", "leak_kind": "none", "leak_evidence": "",
 "single_doc_answerable": false, "single_doc": "", "single_doc_evidence": "",
 "hops_needed": 2, "distinct_docs_needed": 2, "clean": true,
 "reason": "<one or two sentences>"}
```

| field | type | |
|---|---|---|
| `qid`, `leaked`, `single_doc_answerable`, `clean` | string, booleans | required |
| `leak_doc`, `leak_kind`, `leak_evidence` | strings | `leak_doc` one of the input `doc_ids` when leaked |
| `single_doc`, `single_doc_evidence` | strings | `single_doc` one of the input `doc_ids` when single-document answerable |
| `hops_needed`, `distinct_docs_needed` | integers | |
| `reason` | string | one or two sentences |

Then run `python3 <stage1>/hpqa_state.py check <set> <qid> contamination` and fix every ERROR until it
reports 0 errors. Warnings name quotes the check could not find verbatim in a cached document: take
quotes with `cm.py quote`.
