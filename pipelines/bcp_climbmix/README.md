# BrowseComp-plus -> ClimbMix projection pipeline

End-to-end pipeline that projects the 830 BrowseComp-plus (BCP) test questions onto the ClimbMix
corpus (`climbmix-400b`, Pyserini REST BM25) and keeps only questions whose full reasoning chain is
grounded in the corpus. Output: the 65-question all-hops-grounded set (54 from this pipeline plus 11
independently projected questions that pass the same verification). A manual review of every
hop-document pair then produced the released 57-question benchmark.

The `.js` steps are agentic (Claude Code Workflow scripts): one agent per question, one output file
per question, so every step is resumable - a question whose output file exists is skipped. Retrieval
goes through `cm.py`; set `PYSERINI_API_TOKEN` in the environment or a repo-local `.env.local`. Path
constants at the top of each script must be pointed at your checkout.

## Steps

| step | scripts | what happens |
|---|---|---|
| 0 data prep | `step0_build_tasks.py` | BCP test split -> one task file per question |
| 1 projection | `step1_project.js` | hop decomposition, each hop grounded on ClimbMix (agentic) |
| 2 answerability | `step2_collect_verdicts.py`, `step2_build_projected_list.py` | collect per-question verdicts (PROJECTABLE / PARTIAL / NOT); 830 -> 326 |
| 3 all-hops verification | `step3_build_inputs.py`, `step3_ground_all_hops_pass1.js`, `step3_ground_all_hops_pass2.js`, `step3_coverage_audit.js`, `step3_audit_independent_set.js` | one verification stage, three checks: (a) EVERY hop re-verified against full ClimbMix document text by two independent grounding passes (a hop is grounded when either finds a verbatim document); (b) coverage - drop questions where a content clue (especially an embedded number, count, or interval) never became a hop; (c) the same all-hop, retrieval-verified standard applied to an independently projected candidate set, whose survivors join the final set |
| 4 final build | `step4_build_final.py` | deterministic rebuild from the committed step-3 outputs -> `projected_questions_final.jsonl` (65) |

## Reproducibility

Steps 1 and 3 are agentic and not bit-for-bit reproducible; their per-question output files are the
canonical record, and step 4 regenerates the final list deterministically from them. Re-running the
agentic steps from scratch needs only the ClimbMix BM25 endpoint and reproduces an equivalent
(though not identical) set.
