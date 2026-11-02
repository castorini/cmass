# Stage 0 task construction and Stage 1 projection, per dataset

Stage 1 turns each source question into a candidate for Stage 2. Agents decompose the question into hops,
ground the hops in ClimbMix and judge whether the answer can be derived; filters then keep the candidates
and set aside near misses. All four datasets share that core, but the labels and filters differ by answer
type. Each directory below holds the procedure **as it was run** for one dataset: its Claude Code Workflow
scripts, the agent guides, the deterministic builders, the manual decisions, and an offline demo on invented
data. No question, answer, hop, document or benchmark identifier is included.

| dataset | answer | directory | answerability label | filters after projection | Stage 2 input |
|---|---|---|---|---|---|
| BrowseComp-Plus | single | [`bcp/`](bcp/) | PROJECTABLE / PARTIAL / NOT | two hop-scrutiny passes (union), a coverage audit without tools, an author review; plus an audit of questions projected independently with GPT-5.5, and near-miss repairs | 65 candidates + 55 near misses (8 also candidates) |
| Total Recall QA | aggregate | [`trqa/`](trqa/) | SUPPORTED / PARTIAL / NOT | a strict audit, then four agent gates: extreme, completeness, coherence, annotator | 50 candidates + 37 near misses; 172 expansion candidates |
| DeepSearchQA | set | [`dsqa/`](dsqa/) | SUPPORTED / PARTIAL / NOT (a set passes on one grounded member) | a consistency check, a strict audit, an extreme audit (required hops only), coherence and annotator gates, an operator's flags with adjudication; snippets re-chosen and repaired along the way | 64 candidates + 20 near misses; 57 expansion candidates |
| HotpotQA | single | [`hotpotqa/`](hotpotqa/) | none | a contamination screen, bridge questions only, then four agent gates: extreme, uniqueness, coherence, annotator | 157 candidates + 103 near misses |

Counts are the original run's. Each directory's README gives every step with its counts, the model that ran
it, where it departs from the paper's description of Stage 1, and the private source paths it was ported from.

## Shared conventions

- **Run directory.** Stage 0 writes `tasks/<set>.jsonl`; Stage 1 writes each agent's output to
  `stage1/<set>/<step>/<qid>.json` and its lists to `stage1/<set>/*.jsonl`, and ends with the Stage 2 inputs
  `inputs/<set>.jsonl` and `inputs/<set>_near_misses.jsonl` (format in [`../cmass/records.py`](../cmass/records.py)).
- **Corpus access.** Agents use [`../tools/cm.py`](../tools/cm.py) with `--stage 1 --step <step>`: each step
  keeps its own search log (`stage1/<set>/<step>/<qid>.searchlog.jsonl`), so independent passes and the Stage 2
  auditor never share a query history. `search --center` shows previews around the query terms, as the original
  clients did.
- **Workflows** resume from disk: a per-dataset state tool decides what is pending and validates every output.
  `node ../tools/check_workflows.js .` checks their syntax.
- **Demos.** `python3 <dataset>/demo/make_demo.py` builds a run directory on the invented corpus
  (`<dataset>/demo/corpus.json`, merged into [`../demo/demo_cm.py`](../demo/demo_cm.py)) and runs every
  deterministic step, ending with Stage 2 inputs that `../tools/packets.py` reads.

Stage 1 of BrowseComp-Plus was first released in [`../../bcp_climbmix/`](../../bcp_climbmix/); its task
construction, projection and answerability scripts are the ones that ran, and [`bcp/`](bcp/) adds everything
after them.
