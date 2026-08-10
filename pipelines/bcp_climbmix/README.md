# BrowseComp-plus -> ClimbMix projection pipeline

End-to-end pipeline that projects the 830 BrowseComp-plus (BCP) test questions onto the ClimbMix
corpus (`climbmix-400b`, Pyserini REST BM25) and keeps only questions whose full reasoning chain is
grounded in the corpus. Output: the 65-question all-hops-grounded set (54 from stages 0-4 plus 11
independently projected questions admitted by stage 5). A manual review of every hop-document pair
then produced the released 57-question benchmark.

The `.js` stages are agentic (Claude Code Workflow scripts): one agent per question, one output file
per question, so every stage is resumable - a question whose output file exists is skipped. Retrieval
goes through `cm.py`; set `PYSERINI_API_TOKEN` in the environment or a repo-local `.env.local`. Path
constants at the top of each script must be pointed at your checkout.

## Stages

| stage | script | in -> out |
|---|---|---|
| 0 | `stage0_build_tasks.py` | BCP test split -> one task file per question |
| 1 | `stage1_project.js` | task -> hop decomposition, each hop grounded on ClimbMix (agentic) |
| 1 | `stage1_collect_verdicts.py`, `stage1_build_projected_list.py` | per-question runs -> PROJECTABLE / PARTIAL / NOT; 830 -> 326 |
| 2 | `stage2_build_inputs.py` | 326 -> per-question {question, answer, hops} inputs for grounding |
| 3 | `stage3_ground_all_hops_pass1.js`, `stage3_ground_all_hops_pass2.js` | two independent passes re-verify EVERY hop against full ClimbMix document text; a hop is grounded when either pass finds a verbatim document |
| 4 | `stage4_coverage_audit.js` | drop questions where a content clue (especially an embedded number, count, or interval) never became a hop |
| 5 | `stage5_audit_independent_set.js` | all-hop, retrieval-verified audit of an independently projected candidate set; survivors join the final set |
| 6 | `stage6_build_final.py` | deterministic rebuild from the committed stage 3-4 outputs -> `projected_questions_final.jsonl` |

## Reproducibility

Stages 1, 3, 4 and 5 are agentic and not bit-for-bit reproducible; their per-question output files are
the canonical record, and stage 6 regenerates the final list deterministically from them. Re-running
the agentic stages from scratch needs only the ClimbMix BM25 endpoint and reproduces an equivalent
(though not identical) set.
