# Rules for agents and contributors

**This repository is public.** It holds CMASS code, documentation and aggregate statistics only.

Never commit, push, or open a pull request containing:

- **Benchmark content:** questions, answers, hop clues, evidence snippets or quotes, ClimbMix document
  text, per-question relevance judgments (qrels), review notes, or run logs and outputs that contain any
  of these. This applies to every source benchmark (BrowseComp-Plus, Total Recall QA, DeepSearchQA,
  HotpotQA and any later one), in any format: JSON, JSONL, Markdown, HTML, figures with text, notebooks.
- **Dataset or archive files:** `.jsonl`, `.parquet`, `.trec`, `.zip`, and similar.
- **Credentials:** API tokens, keys, `.env` files.

This includes short-lived branches. A branch pushed here is public the moment it is pushed, and a pull
request keeps its commits reachable even after the branch is deleted. That happened once (July 2026):
a branch with plaintext BrowseComp-Plus questions needed GitHub Support to remove.

Where things go instead:

| what | where |
|---|---|
| working data, projection outputs, audits, qrels work, logs | the private repository |
| released datasets | the obfuscated Hugging Face release (`castorini/cmass`), decoded with `scripts/deobfuscate.py` |
| examples in documentation | placeholders (`"..."`, `<question>`) or invented entities, never a real benchmark item or one of its qrel document ids |

Before your first commit, run `bash scripts/install_hooks.sh`. It installs a pre-commit hook (scans
staged files) and a pre-push hook (scans every commit being pushed). Every push and pull request also
runs the `leak-scan` GitHub check (`.github/workflows/leak-scan.yml`). If the scanner flags something,
remove it; do not work around the scanner. `.leak-scan-allow` exists only for aggregate statistics files
that trip the format, size or document-id rules, and for files of invented demo content tagged
`[synthetic]`; each entry needs a reason and is reviewed in the pull request. Details: `.claude/skills/leak-scan/SKILL.md`.
