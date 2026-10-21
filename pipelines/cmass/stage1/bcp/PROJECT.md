# Projection guide (BrowseComp-Plus Stage 1, step `project`)

You are an inference-allowed **projector** for BrowseComp-Plus -> ClimbMix, for one question. Your goal: decide
whether THIS question's answer can be UNIQUELY reasoned out from the ClimbMix corpus, and record the grounded
reasoning. One agent per question; you write one file, `stage1/<set>/project/<qid>.json`.

This is the prompt of the original `project_chunk.js` (it ran on 729 of the 830 questions), with its corpus
commands mapped onto `pipelines/cmass/tools/cm.py`. `STAGE1` below is `pipelines/cmass/stage1/bcp`, `TOOLS` is
`pipelines/cmass/tools`; run every command from the run directory.

## Step 1: read the task

```
python3 STAGE1/bcp_state.py SET project QID --input      # writes the task to a file and prints its path
```

Read that file (the Read tool handles long files in parts). It holds `record_id`, `question`, `answer` and
`hints`: BrowseComp-Plus's own gold and evidence documents for this question (`provided_docs` = gold_docs and
evidence_docs). The hints show what the reasoning steps are. They are web pages from the BrowseComp-Plus
collection, not ClimbMix documents: they never count as grounding.

## Step 2: decompose

Decompose the question into the minimal HOPS (atomic facts) on the path from the question's constraints to the
answer. Use the hints to see what the steps are. Aim for 4-6 hops.

## Step 3: ground each hop on ClimbMix

For each hop, retrieve from ClimbMix and judge support. INFERENCE IS ALLOWED: a hop counts if it is stated in OR
soundly inferable from corpus documents (multi-document synthesis, unit, temporal or arithmetic conversion,
narrative deduction, entity linking).

```
python3 TOOLS/cm.py search  --stage 1 --step project --set SET --qid QID "<query naming the answer entity + the fact>" --hits 30 --chars 600 --center
python3 TOOLS/cm.py doc     --stage 1 --step project --set SET --qid QID DOCID --max 200000      # the full document
python3 TOOLS/cm.py doc     --stage 1 --step project --set SET --qid QID DOCID --grep "phrase"   # around a phrase
python3 TOOLS/cm.py quote   DOCID "first words of the span" "last words of the span"              # an exact span
python3 TOOLS/cm.py history --stage 1 --step project --set SET --qid QID                         # your queries so far
```

- Previews are CENTERED ON THE QUERY MATCH (`--center`: the densest window of query terms in each document).
- Always pass `--stage 1 --step project`: this step keeps its own search log for the question.
- Issue at least 3-6 varied queries per hop; escalate hits 30 -> 100 -> 200 for hard hops (`--hits 100 --force`
  reruns a query deeper: `search` refuses a query too close to one already in this step's log for the question).
- Copy a VERBATIM corpus snippet for each supported hop (`cm.py quote` prints an exact span).
- `--only "phrase" --hits 50` lists just the hits whose text contains a phrase.
- The corpus service is shared: run one command at a time. A failed request is not evidence; wait and rerun.

## Step 4: the verdict (the uniqueness test)

The answer may itself be reasoned or computed; it need not appear verbatim.

| verdict | when |
|---|---|
| `PROJECTABLE` | a corpus-only reasoning agent can UNIQUELY derive THIS answer: the corpus-supported hops together pin the answer and rule out other entities. The question may be over-constrained: a DISCRIMINATING SUBSET of hops that is corpus-supported is sufficient, and unsupported NON-discriminating hops do not disqualify. |
| `PARTIAL` | the answer is reachable but NOT uniquely pinned from the corpus (other entities could fit the supported facts), or a decisive identifying step is weak. |
| `NOT` | a decisive identifying fact is genuinely ABSENT from ClimbMix, or the corpus CONTRADICTS the answer. |

## Step 5: write the result

Write `stage1/<set>/project/<qid>.json` (the path `bcp_state.py SET project QID` prints as `output`):

```json
{"record_id": "<qid>", "question": "<q>", "answer": "<a>",
 "verdict": "PROJECTABLE | PARTIAL | NOT",
 "answer_uniquely_inferable": true,
 "discriminating_hops": [{"clue": "<hop>", "corpus_evidence": [{"doc_id": "<docid>", "snippet": "<verbatim>"}]}],
 "redundant_or_unsupported_hops": [{"clue": "<hop>", "status": "redundant | unsupported"}],
 "reasoning_path": "<how corpus facts -> the answer>",
 "rationale": "<concise>"}
```

`discriminating_hops` followed by `redundant_or_unsupported_hops` is the question's full decomposition: the
scrutiny passes re-check every one of them, in this order. Always write the file before you finish: it is the
progress record.

## Step 6: check

```
python3 STAGE1/bcp_state.py SET project QID --check      # must print OK
```

Fix every ERROR it prints. If you cannot finish (a tool refused, the corpus kept failing), say so in your report;
never report success for a file you did not write.
