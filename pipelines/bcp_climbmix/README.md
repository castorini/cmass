# BrowseComp-Plus -> ClimbMix: Stage 1 Projection

This directory implements **Stage 1 only** of the paper pipeline. It projects the 830 BrowseComp-Plus
(BCP) test questions onto the ClimbMix corpus (`climbmix-400b`, BM25) and keeps only questions whose
full reasoning chains are grounded in the corpus. **Stage 2: Independent agent validation with
PIIKA**, **Stage 3: Human verification**, and **Stage 4: Qrels construction** are outside this
directory.

Within **Stage 1: Projection**, the two agentic steps are:

1. **Hop/Clue decomposition:** derive the minimal atomic facts needed to reach the answer.
2. **Grounding:** retrieve and verify ClimbMix evidence for every hop.

They are followed by the figure's two filtering gates: **Answerability check** and **All-hops
verification**. The numeric prefixes in the filenames record execution order only; they are not paper
stage or step numbers. This directory also includes a convenience input builder corresponding to the
figure's **Task construction** box in **Stage 0: Dataset and corpus preparation**.

The output is the 65-question all-hops-grounded set: 54 from the BCP test split plus 11 independently
projected questions verified by the same grounding gate. Subsequent human review produced the
released 57-question benchmark.

The `.js` steps are agentic (Claude Code Workflow scripts): one agent per question, one output file
per question, so every step is resumable - a question whose output file exists is skipped.

Retrieval is behind a two-command CLI you supply (the `CM` constant in each `.js` step): a thin
client over a BM25 index of ClimbMix that prints JSON:

```
python3 cm.py search "<query>" [hits] [preview_chars]   # {results:[{rank, docid, score, preview}]}
python3 cm.py doc <docid>                               # {docid, doc}  (full document text)
```

Path constants at the top of each script must be pointed at your checkout; qids are passed to the
`.js` steps as Workflow args.

## Figure-to-Code Map

| Figure label | Implementation files | What happens |
|---|---|---|
| **Task construction** (Stage 0 helper) | `step0_build_tasks.py` | Build one task per BCP question from its question, gold answer, and hint documents. |
| **Hop/Clue decomposition** (Stage 1, Step 1) | decomposition phase of `step1_project.js` | Derive the minimal chain of atomic facts leading to the answer. |
| **Grounding** (Stage 1, Step 2) | grounding phase of `step1_project.js` | Search until every hop has ClimbMix evidence or conclude that the evidence is absent. |
| **Answerability check** (Stage 1 filtering gate) | verdict phase of `step1_project.js`, `step2_collect_verdicts.py`, `step2_build_projected_list.py` | Label each question `PROJECTABLE`, `PARTIAL`, or `NOT`, reducing 830 questions to 326 projectable questions. |
| **All-hops verification** (Stage 1 filtering gate) | `step3_build_inputs.py`, `step3_verify_all_hops.js` | Verify every hop against full document text and ensure every content clue, including numbers, counts, and intervals, is represented and grounded. Redundant hops are marked but never dropped, leaving 65 questions. |
| Stage 1 output materialization | `step4_build_final.py` | Deterministically rebuild the 65-question all-hops-grounded output as `projected_questions_final.jsonl`. |

## Reproducibility

Hop/Clue decomposition and Grounding are agentic and not bit-for-bit reproducible; their per-question
output files are the canonical record, and `step4_build_final.py` regenerates the final list
deterministically from them. Re-running the agentic work from scratch needs only the ClimbMix BM25
endpoint and reproduces an equivalent, though not identical, set.

Provenance note: the released set was originally produced with grounding split across sequential
runs, two retrieval passes whose evidence was unioned plus a separate coverage audit.
`step3_verify_all_hops.js` applies the same grounding gates in a single pass.
