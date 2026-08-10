# BrowseComp-plus -> ClimbMix projection pipeline

End-to-end pipeline that projects the 830 BrowseComp-plus (BCP) test questions onto the ClimbMix
corpus (`climbmix-400b`, Pyserini REST BM25) and keeps only questions whose full reasoning chain is
grounded in the corpus. The pipeline output is the 65-question all-hops-grounded set (54 from this
pipeline + 11 from an independent projection, audited by stage 5); a subsequent manual review of every
hop-document pair produced the released 57. See `methodology.tex` for the paper write-up.

The agentic stages (`.js` files) are Claude Code Workflow scripts: each spawns one agent per question,
writes one output file per question (so runs are resumable - every script skips a question whose
output file already exists), and retrieves from ClimbMix via `cm.py`. Set `PYSERINI_API_TOKEN` in the
environment or a repo-local `.env.local`. Path constants at the top of each script point at the
working directories and must be adjusted to your checkout.

## Stages

| stage | script | in -> out |
|---|---|---|
| 0 data prep | `build_all_tasks.py` | BCP test split -> one task file per question (question, gold answer, original docs as hints) |
| 1 projection | `project_chunk.js` | task -> hop decomposition + per-hop ClimbMix grounding (agentic, resumable) |
| 1 collect | `build_verdicts.py`, `build_projected_list.py` | per-question runs -> verdicts (PROJECTABLE / PARTIAL / NOT); 830 -> 326 answerable |
| 2 scrutiny inputs | `build_scrutiny_inputs.py` | 326 -> per-question {question, answer, hops} inputs |
| 3 all-hops grounding | `scrutinize_hops.js`, `scrutinize_hops_v2.js` | two independent passes re-verify EVERY hop against full ClimbMix document text; a hop counts as grounded when either pass finds a verbatim document |
| 4 coverage audit | `audit_coverage.js` | drop questions where a content clue (especially an embedded number/count/interval) never became a hop |
| 5 independent-set audit | `audit_sahel.js` | all-hop, retrieval-verified audit of an independent projection's candidates; survivors merge into the final set |
| final build | `build_coverage_final.py` | deterministic rebuild from the committed stage outputs -> `projected_questions_final.jsonl` (65) |
| validation | `piika_accuracy_v2.py` | accuracy of an independent deep-research agent (own retrieval) across the funnel |

## Reproducibility

Stages 1, 3, 4 and 5 are agentic and not bit-for-bit reproducible. The per-question outputs are the
canonical record; `build_coverage_final.py` regenerates the final list deterministically from them.
Re-running the agentic stages from scratch needs only the ClimbMix BM25 endpoint and reproduces an
equivalent (though not identical) set.
