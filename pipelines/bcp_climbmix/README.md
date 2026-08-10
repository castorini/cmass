# BrowseComp-plus -> ClimbMix projection pipeline

End-to-end pipeline that projects the 830 BrowseComp-plus (BCP) test questions onto the ClimbMix
corpus (`climbmix-400b`, Pyserini REST BM25) and keeps only questions whose full reasoning chain is
grounded in the corpus. Output: the 65-question all-hops-grounded set (54 from the BCP test split
plus 11 independently projected questions verified by the same step 3). A manual review of every
hop-document pair then produced the released 57-question benchmark.

The `.js` steps are agentic (Claude Code Workflow scripts): one agent per question, one output file
per question, so every step is resumable - a question whose output file exists is skipped. Retrieval
goes through `cm.py`; set `PYSERINI_API_TOKEN` in the environment or a repo-local `.env.local`. Path
constants at the top of each script must be pointed at your checkout; qids are passed to the `.js`
steps as Workflow args.

## Steps

| step | scripts | what happens |
|---|---|---|
| 0 data prep | `step0_build_tasks.py` | BCP test split -> one task file per question |
| 1 projection | `step1_project.js` | hop decomposition, each hop grounded on ClimbMix (agentic) |
| 2 answerability | `step2_collect_verdicts.py`, `step2_build_projected_list.py` | collect per-question verdicts (PROJECTABLE / PARTIAL / NOT); 830 -> 326 |
| 3 all-hops verification | `step3_build_inputs.py`, `step3_verify_all_hops.js` | one agent per question, two gates in one pass: every hop grounded by the full text of a ClimbMix document (verbatim snippet kept, retrieval-verified), and every content clue of the question - including numbers, counts, and intervals embedded in a phrase - represented by a hop. Redundant hops are marked but never dropped. Candidates from an independent projection go through the same script via the same input schema. |
| 4 final build | `step4_build_final.py` | deterministic rebuild from the committed step-3 outputs -> `projected_questions_final.jsonl` |

## Reproducibility

Steps 1 and 3 are agentic and not bit-for-bit reproducible; their per-question output files are the
canonical record, and step 4 regenerates the final list deterministically from them. Re-running the
agentic steps from scratch needs only the ClimbMix BM25 endpoint and reproduces an equivalent
(though not identical) set.

Provenance note: the released set was originally produced with this verification split across
sequential runs (two grounding passes whose evidence was unioned, plus a separate coverage audit);
`step3_verify_all_hops.js` applies the identical two gates in a single pass.
