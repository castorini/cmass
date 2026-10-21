#!/usr/bin/env python3
"""Leak scanner for this public repository.

CMASS benchmark content must never be committed here: questions, answers, hop clues, evidence snippets,
ClimbMix document text, per-question relevance judgments, review notes or run logs that quote any of
them. The datasets are published only in obfuscated form on Hugging Face (castorini/cmass); the
working data lives in a private repository. Credentials must never be committed either.

  python3 scripts/leak_scan.py --staged                # pre-commit hook: files staged for commit
  python3 scripts/leak_scan.py --commits BASE..HEAD    # CI / pre-push: every file added or changed by each commit
  python3 scripts/leak_scan.py --new-commits SHA       # pre-push of a new branch: commits not on any remote
  python3 scripts/leak_scan.py --tree [REV]            # every file of a tree (default HEAD)
  python3 scripts/leak_scan.py --history               # every file reachable from any ref (audits)

Every commit is scanned, not only the final tree: a file added in one commit and deleted in the next is
still published. Exit status is 1 when anything is found.

Rules
  secret        credentials (API keys, tokens, private keys, .env files)
  data-format   dataset and archive formats (.jsonl, .parquet, .trec, .zip, ...)
  size          files over 2 MB (images and PDFs: 5 MB)
  data-field    a question / answer / clue / snippet / evidence field with a real value
                (placeholders such as "...", "<question>" or "example ..." are fine)
  qa-pair       a "Question: ..." line followed by an "Answer: ..." line
  doc-ids       more than 15 distinct ClimbMix document ids in one file (a qrels-like listing)
  canary        a benchmark canary string or a filled canary field

A path that legitimately needs a blocked format or size, or lists many document ids, goes in
.leak-scan-allow (one glob per line, with a reason after '#'); the secret, data-field, qa-pair and
canary rules still apply to it. A file of invented demo content (fictional entities only, never a real
benchmark item) is listed with the tag [synthetic] after the glob; that skips the data-field and
qa-pair rules for it, and nothing else. Never add benchmark content to the allowlist: publish it
obfuscated through the Hugging Face release instead.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW_FILE = ROOT / ".leak-scan-allow"

SECRET_PATTERNS = {
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "OpenAI-style API key": re.compile(r"(?<![A-Za-z0-9_\-])sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    "Hugging Face token": re.compile(r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{20,}"),
    "GitHub token": re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "AWS access key": re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # a quoted literal assigned to a key/token/secret/password name, or a .env / shell style NAME=value line
    "credential assignment": re.compile(
        r"(?i)\b\w*(?:API_?KEY|TOKEN|SECRET|PASSWORD)['\"]?\s*[:=]\s*['\"](?!<)(?!(?-i:[A-Z0-9_]+)['\"])[A-Za-z0-9_\-\.\/+=]{16,}['\"]"),
    "credential in env file": re.compile(
        r"^\s*(?:export\s+)?[A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*=(?![<$'\"{])[^\s#]{16,}"),
}
SECRET_FILES = re.compile(r"(^|/)(\.env(\.[A-Za-z0-9_]+)?|.*\.pem|id_(rsa|ed25519|ecdsa)[^/]*|credentials[^/]*\.json)$")
SECRET_FILE_OK = re.compile(r"(^|/)\.env\.(example|sample|template)$")

DATA_FORMATS = {".jsonl", ".ndjson", ".trec", ".qrels", ".parquet", ".arrow", ".feather", ".pkl", ".pickle",
                ".npy", ".npz", ".h5", ".hdf5", ".db", ".sqlite", ".sqlite3", ".zip", ".gz", ".tgz", ".tar",
                ".bz2", ".xz", ".7z", ".bundle"}
MEDIA = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf"}
MAX_BYTES, MAX_MEDIA_BYTES = 2_000_000, 5_000_000

FIELDS = ("question", "questions", "problem", "answer", "answers", "gold_answer", "gold_answers",
          "predicted_answer", "clue", "clues", "snippet", "snippets", "quote", "quotes", "evidence", "excerpt",
          "sub_question", "sub_answer", "doc_text", "passage")
FIELD_RE = re.compile(r'"(' + "|".join(FIELDS) + r')"\s*:\s*(?:\[\s*)?"((?:[^"\\]|\\.)*)"')
PLACEHOLDER_RE = re.compile(r"^(?:.{0,40}(?:\.{3}|…)|\.{2,}|…|<[^>]*>|\{[^}]*\}|\$\{?[A-Za-z_]+\}?|example\b.*|string|str|text|"
                            r"todo|tbd|n/a|none|null|true|false|-?\d+(?:\.\d+)?)$", re.I)
QUESTION_LINE = re.compile(r"^\W{0,4}(?:Q|Question)\s*\d*\s*[:.)]\s*\S.{25,}")
ANSWER_LINE = re.compile(r"^\W{0,4}(?:A|Answer)\s*[:.)]\s*\S")
DOC_ID = re.compile(r"shard_\d{5}_\d+")
MAX_DOC_IDS = 15
CANARY = (re.compile(r"(?i)canary\s+GUID"), re.compile(r"(?i)BENCHMARK\s+DATA\s+SHOULD\s+NEVER\s+APPEAR"),
          re.compile(r'"canary"\s*:\s*"(?!\.\.\.|<|")[^"]{8,}"'))


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    detail: str
    where: str = ""

    def __str__(self):
        loc = f"{self.path}:{self.line}" if self.line else self.path
        return f"{loc}: [{self.rule}] {self.detail}" + (f"  ({self.where})" if self.where else "")


def git(*args: str, input: bytes | None = None) -> bytes:
    r = subprocess.run(["git", "-C", str(ROOT), *args], input=input, capture_output=True)
    if r.returncode:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def allowlist() -> list[tuple[str, set[str]]]:
    """(glob, tags) per entry of .leak-scan-allow; tags such as [synthetic] follow the glob."""
    if not ALLOW_FILE.exists():
        return []
    out = []
    for line in ALLOW_FILE.read_text().splitlines():
        parts = line.split("#", 1)[0].split()
        if parts:
            out.append((parts[0], {t.strip("[]").lower() for t in parts[1:] if t.startswith("[")}))
    return out


def is_allowed(path: str, globs, tag: str | None = None) -> bool:
    for g in globs:
        glob, tags = (g, set()) if isinstance(g, str) else g
        if fnmatch.fnmatch(path, glob) and (tag is None or tag in tags):
            return True
    return False


def placeholder(value: str) -> bool:
    v = value.strip()
    return not v or bool(PLACEHOLDER_RE.match(v))


def scan_blob(path: str, data: bytes, globs: list[str], where: str = "") -> list[Finding]:
    found: list[Finding] = []
    allowed = is_allowed(path, globs)
    synthetic = is_allowed(path, globs, "synthetic")
    ext = Path(path).suffix.lower()
    if SECRET_FILES.search(path) and not SECRET_FILE_OK.search(path):
        found.append(Finding(path, 0, "secret", "credential file; never commit it", where))
    if ext in DATA_FORMATS and not allowed:
        found.append(Finding(path, 0, "data-format", f"{ext} files hold datasets or archives; publish data "
                                                     "through the obfuscated Hugging Face release", where))
    limit = MAX_MEDIA_BYTES if ext in MEDIA else MAX_BYTES
    if len(data) > limit and not allowed:
        found.append(Finding(path, 0, "size", f"{len(data):,} bytes (limit {limit:,})", where))
    if b"\0" in data[:8192] or ext in MEDIA:
        return found                                   # binary: format and size rules only
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        for name, pat in SECRET_PATTERNS.items():
            if pat.search(line):
                found.append(Finding(path, i, "secret", name, where))
        for m in ([] if synthetic else FIELD_RE.finditer(line)):
            if not placeholder(m.group(2)):
                found.append(Finding(path, i, "data-field", f'"{m.group(1)}" has a real value: '
                                                            f'{m.group(2)[:60]!r}', where))
        for pat in CANARY:
            if pat.search(line):
                found.append(Finding(path, i, "canary", "benchmark canary string", where))
        if not synthetic and QUESTION_LINE.match(line) and any(ANSWER_LINE.match(n) for n in lines[i:i + 6]):
            found.append(Finding(path, i, "qa-pair", f"question followed by an answer: {line.strip()[:60]!r}", where))
    ids = set(DOC_ID.findall(text))
    if len(ids) > MAX_DOC_IDS and not allowed:
        found.append(Finding(path, 0, "doc-ids", f"{len(ids)} distinct ClimbMix document ids "
                                                 f"(limit {MAX_DOC_IDS}): looks like qrels or evidence", where))
    return found


class Blobs:
    """Read many blobs through one `git cat-file --batch` process."""

    def __init__(self):
        self.p = subprocess.Popen(["git", "-C", str(ROOT), "cat-file", "--batch"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, sha: str) -> bytes:
        self.p.stdin.write(sha.encode() + b"\n")
        self.p.stdin.flush()
        header = self.p.stdout.readline().split()
        if len(header) < 3 or header[1] != b"blob":
            return b""
        data = self.p.stdout.read(int(header[2]))
        self.p.stdout.read(1)
        return data

    def close(self):
        self.p.stdin.close()
        self.p.wait()


def changed_blobs(commit: str) -> list[tuple[str, str]]:
    """(path, blob sha) for every file a commit adds or modifies."""
    out = git("diff-tree", "-r", "--root", "--no-commit-id", "--diff-filter=ACMRT", "-z", commit).split(b"\0")
    res, i = [], 0
    while i < len(out) - 1:
        meta = out[i].decode()
        if not meta.startswith(":"):
            i += 1
            continue
        parts = meta[1:].split()
        status = parts[4]
        if status[0] in "RC":                       # renames and copies carry two paths
            res.append((out[i + 2].decode(), parts[3]))
            i += 3
        else:
            res.append((out[i + 1].decode(), parts[3]))
            i += 2
    return [(p, s) for p, s in res if s != "0" * 40]


def tree_blobs(rev: str) -> list[tuple[str, str]]:
    res = []
    for entry in git("ls-tree", "-r", "-z", rev).split(b"\0"):
        if entry:
            meta, path = entry.decode().split("\t", 1)
            mode, kind, sha = meta.split()
            if kind == "blob":
                res.append((path, sha))
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged", action="store_true")
    g.add_argument("--commits", metavar="RANGE")
    g.add_argument("--new-commits", metavar="SHA")
    g.add_argument("--tree", nargs="?", const="HEAD", metavar="REV")
    g.add_argument("--history", action="store_true")
    g.add_argument("--files", nargs="+", metavar="PATH", help="working-tree files or directories, without git")
    a = ap.parse_args()
    globs = allowlist()
    blobs, findings, seen = Blobs(), [], set()

    def check(path, sha, where=""):
        if (path, sha) in seen:
            return
        seen.add((path, sha))
        findings.extend(scan_blob(path, blobs.read(sha), globs, where))

    if a.staged:
        staged =[p for p in git("diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z").decode().split("\0") if p]
        for p in staged:
            sha = git("rev-parse", f":{p}").decode().strip()
            check(p, sha, "staged")
    elif a.commits or a.new_commits:
        revs = (git("rev-list", a.commits) if a.commits else git("rev-list", a.new_commits, "--not", "--remotes"))
        for c in revs.decode().split():
            for p, sha in changed_blobs(c):
                check(p, sha, f"commit {c[:8]}")
    elif a.history:
        for line in git("rev-list", "--all", "--objects").decode().splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2 and git("cat-file", "-t", parts[0]).strip() == b"blob":
                check(parts[1], parts[0], "history")
    elif a.files:
        root = Path(git("rev-parse", "--show-toplevel").decode().strip())
        for arg in a.files:
            base = Path(arg).resolve()
            for f in sorted([base] if base.is_file() else base.rglob("*")):
                if not f.is_file() or ".git" in f.parts or "__pycache__" in f.parts:
                    continue
                rel = f.relative_to(root).as_posix() if f.is_relative_to(root) else str(f)
                if (rel, "file") not in seen:
                    seen.add((rel, "file"))
                    findings.extend(scan_blob(rel, f.read_bytes(), globs, "working tree"))
    else:
        for p, sha in tree_blobs(a.tree):
            check(p, sha, a.tree)
    blobs.close()
    for f in findings:
        print(f)
    if findings:
        print(f"\nleak_scan: {len(findings)} finding(s). This repository is public: remove the content, or "
              "publish data through the obfuscated Hugging Face release. See AGENTS.md.", file=sys.stderr)
        return 1
    print(f"leak_scan: clean ({len(seen)} file(s) scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
