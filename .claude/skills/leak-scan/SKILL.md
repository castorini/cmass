---
name: leak-scan
description: Keep benchmark content and credentials out of this public repository. Use before any commit, push or pull request, when adding data, examples, figures, logs or outputs, when the leak-scan check fails, or when asked to audit the repository for leaks. Triggers include "commit", "push", "open a PR", "add results", "add examples", "leak", "audit the repo", "leak-scan failed".
---

# Leak scan

This repository is public (see `AGENTS.md`). `scripts/leak_scan.py` blocks anything that could expose
CMASS benchmark content or credentials. It runs in three places:

| where | what it scans | how |
|---|---|---|
| pre-commit hook | files staged for commit | `bash scripts/install_hooks.sh` once per clone |
| pre-push hook | every commit being pushed (new branches: every commit on no remote yet) | same installer |
| GitHub check `leak-scan` | every commit a push or pull request adds, then the resulting tree | `.github/workflows/leak-scan.yml` |

Run it by hand:

```bash
python3 scripts/leak_scan.py --staged                 # before committing
python3 scripts/leak_scan.py --commits origin/main..HEAD
python3 scripts/leak_scan.py --tree HEAD              # the current tree
python3 scripts/leak_scan.py --history                # every file reachable from any ref (audit)
```

## Rules

| rule | flags |
|---|---|
| `secret` | API keys and tokens (Anthropic, OpenAI, Hugging Face, GitHub, AWS, Google, Slack), private keys, `NAME_TOKEN=value` style assignments, `.env` files (`.env.example` is allowed) |
| `data-format` | `.jsonl`, `.ndjson`, `.trec`, `.parquet`, `.pkl`, `.npy`, `.db`, `.zip`, `.gz`, `.tar`, `.bundle`, and similar |
| `size` | files over 2 MB (images and PDFs: 5 MB) |
| `data-field` | a `question`, `answer`, `clue`, `snippet`, `quote`, `evidence`, `excerpt`, `sub_question`, `passage`, ... field with a real value; placeholders such as `"..."`, `"<question>"`, `"example ..."` pass |
| `qa-pair` | a `Question: ...` line followed within six lines by an `Answer: ...` line |
| `doc-ids` | more than 15 distinct ClimbMix document ids (`shard_NNNNN_N`) in one file |
| `canary` | a benchmark canary string or a filled `canary` field |

## When the scan fails

1. Read each finding (`path:line: [rule] detail`).
2. Remove the content. For documentation, use placeholders or invented entities; for document ids in
   examples, use ids that are in no benchmark's qrels.
3. If the content is already in a commit, rewrite that commit before pushing (amend or interactive
   rebase on your machine). Once pushed, deleting it from the branch is not enough: tell the
   maintainers, because removing pushed content needs GitHub Support.
4. Only aggregate statistics files that trip `data-format`, `size` or `doc-ids` may go in
   `.leak-scan-allow` (glob, then the reason after `#`). The other rules still apply to them.

Data that should be public goes through the obfuscated Hugging Face release, never into this repository.
