#!/usr/bin/env bash
# Install the leak-scan git hooks for this clone: pre-commit scans the staged files, pre-push scans
# every commit being pushed. This repository is public; see AGENTS.md.
set -euo pipefail
hooks=$(git rev-parse --git-path hooks)
mkdir -p "$hooks"

cat > "$hooks/pre-commit" <<'HOOK'
#!/usr/bin/env bash
exec python3 "$(git rev-parse --show-toplevel)/scripts/leak_scan.py" --staged
HOOK

cat > "$hooks/pre-push" <<'HOOK'
#!/usr/bin/env bash
# stdin lines: <local ref> <local sha> <remote ref> <remote sha>
root=$(git rev-parse --show-toplevel)
zero=0000000000000000000000000000000000000000
status=0
while read -r _lref lsha _rref rsha; do
  [ "$lsha" = "$zero" ] && continue                          # deleting a remote branch
  if [ "$rsha" = "$zero" ]; then                             # new branch: commits on no remote yet
    python3 "$root/scripts/leak_scan.py" --new-commits "$lsha" || status=1
  else
    python3 "$root/scripts/leak_scan.py" --commits "$rsha..$lsha" || status=1
  fi
done
exit $status
HOOK

chmod +x "$hooks/pre-commit" "$hooks/pre-push"
echo "leak-scan hooks installed in $hooks"
