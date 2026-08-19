# BrowseComp-Plus -> ClimbMix: Stage 1 Projection

The top-level scripts in this directory implement **Stage 1: Projection** of the paper pipeline. They
project the 830 BrowseComp-Plus (BCP) test questions onto the ClimbMix corpus (`climbmix-400b`, BM25)
and keep only questions whose full reasoning chains are grounded in the corpus. **Stage 2:
Independent agent validation with PIIKA** and **Stage 3: Human verification** are outside this
workflow. [`qrels/`](qrels/) implements **Stage 4: Qrels construction**.

**Stage 1: Projection** runs four operations in the order shown in the figure:

- **Hop/Clue decomposition:** derive the minimal atomic facts needed to reach the answer.
- **Grounding:** retrieve and verify ClimbMix evidence for every hop.
- **Answerability check:** retain questions whose answers are uniquely derivable from ClimbMix.
- **All-hops verification:** independently recheck grounding and full clue coverage.

Hop/Clue decomposition and Grounding are agent-driven. Answerability check and All-hops
verification filter the resulting candidates. Script names use the paper's stage and operation labels,
without a second numbering scheme. This directory also includes a convenience input builder
corresponding to **Task construction** in **Stage 0: Dataset and corpus preparation**.

The output is the 65-question all-hops-grounded set: 54 from the BCP test split plus 11 independently
projected questions evaluated by the same All-hops verification operation. Subsequent human review
produced the released 57-question benchmark.

The `.js` workflows are agentic (Claude Code Workflow scripts): one agent per question, one output
file per question, so each workflow is resumable - a question whose output file exists is skipped.

Retrieval is behind a two-command CLI you supply (the `CM` constant in each `.js` workflow): a thin
client over a BM25 index of ClimbMix that prints JSON:

```
python3 cm.py search "<query>" [hits] [preview_chars]   # {results:[{rank, docid, score, preview}]}
python3 cm.py doc <docid>                               # {docid, doc}  (full document text)
```

Path constants at the top of each script must be pointed at your checkout; qids are passed to the
`.js` workflows as Workflow args.

## Figure-to-Code Map

| Figure operation | Implementation files | What happens |
|---|---|---|
| **Task construction** (Stage 0) | `stage0_task_construction.py` | Build one task per BCP question from its question, gold answer, and hint documents. |
| **Hop/Clue decomposition** (Stage 1) | decomposition phase of `stage1_hop_clue_decomposition_and_grounding.js` | Derive the minimal chain of atomic facts leading to the answer. |
| **Grounding** (Stage 1) | grounding phase of `stage1_hop_clue_decomposition_and_grounding.js` | Search until every hop has ClimbMix evidence or conclude that the evidence is absent. |
| **Answerability check** (Stage 1) | verdict phase of `stage1_hop_clue_decomposition_and_grounding.js`, `stage1_answerability_check_collect_verdicts.py`, `stage1_answerability_check_build_projected_list.py` | Label each question `PROJECTABLE`, `PARTIAL`, or `NOT`, reducing 830 questions to 326 projectable questions. |
| **All-hops verification** (Stage 1) | `stage1_all_hops_verification_build_inputs.py`, `stage1_all_hops_verification.js` | Verify every hop against full document text and ensure every content clue, including numbers, counts, and intervals, is represented and grounded. Redundant hops are marked but never dropped, leaving 65 questions. |

After verification, `stage1_all_hops_verification_build_output.py` deterministically rebuilds the
65-question output as `projected_questions_final.jsonl`.

## Reproducibility

Hop/Clue decomposition and Grounding are agentic and not bit-for-bit reproducible; their per-question
output files are the canonical record, and `stage1_all_hops_verification_build_output.py` regenerates
the final list deterministically from them. Re-running the agentic work from scratch needs only the
ClimbMix BM25 endpoint and reproduces an equivalent, though not identical, set.

Provenance note: the released set was originally produced with grounding split across sequential
runs, two retrieval passes whose evidence was unioned plus a separate coverage audit.
`stage1_all_hops_verification.js` applies the same grounding and coverage checks in a single pass.
