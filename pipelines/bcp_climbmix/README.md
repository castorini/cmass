# BrowseComp-plus -> ClimbMix projection pipeline

End-to-end pipeline that projects the 830 BrowseComp-plus (BCP) test questions onto the ClimbMix
corpus (`climbmix-400b`, Pyserini REST BM25) and keeps only questions whose full reasoning chain is
grounded in the corpus. Output: the 65-question all-hops-grounded set (54 from steps 0-4 plus 11
independently projected questions admitted by step 5). A manual review of every hop-document pair
then produced the released 57-question benchmark.

The `.js` steps are agentic (Claude Code Workflow scripts): one agent per question, one output file
per question, so every step is resumable - a question whose output file exists is skipped. Retrieval
goes through `cm.py`; set `PYSERINI_API_TOKEN` in the environment or a repo-local `.env.local`. Path
constants at the top of each script must be pointed at your checkout.

## Steps

| step | script | in -> out |
|---|---|---|
| 0 | `step0_build_tasks.py` | BCP test split -> one task file per question |
| 1 | `step1_project.js` | task -> hop decomposition, each hop grounded on ClimbMix (agentic) |
| 1 | `step1_collect_verdicts.py`, `step1_build_projected_list.py` | per-question runs -> PROJECTABLE / PARTIAL / NOT; 830 -> 326 |
| 2 | `step2_build_inputs.py` | 326 -> per-question {question, answer, hops} inputs for grounding |
| 3 | `step3_ground_all_hops_pass1.js`, `step3_ground_all_hops_pass2.js` | two independent passes re-verify EVERY hop against full ClimbMix document text; a hop is grounded when either pass finds a verbatim document |
| 4 | `step4_coverage_audit.js` | drop questions where a content clue (especially an embedded number, count, or interval) never became a hop |
| 5 | `step5_audit_independent_set.js` | all-hop, retrieval-verified audit of an independently projected candidate set; survivors join the final set |
| 6 | `step6_build_final.py` | deterministic rebuild from the committed step 3-4 outputs -> `projected_questions_final.jsonl` |

## Reproducibility

Steps 1, 3, 4 and 5 are agentic and not bit-for-bit reproducible; their per-question output files are
the canonical record, and step 6 regenerates the final list deterministically from them. Re-running
the agentic steps from scratch needs only the ClimbMix BM25 endpoint and reproduces an equivalent
(though not identical) set.
