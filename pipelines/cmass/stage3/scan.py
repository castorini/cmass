#!/usr/bin/env python3
"""Stage 3 corpus scanner: read every document of a corpus once and list, for each hop's match rules, every document
that satisfies them. A hop's candidate set is then complete with respect to its rules, where the BM25 top 40 is an
arbitrary slice of a broad hop. One pass serves any number of rules (all hops of all questions).

  scan.py check --rules RULES.jsonl --known KNOWN.jsonl (--corpus PATH | --texts TEXTS.jsonl)
                [--write-passing PASSING.jsonl] [--docs] [--why] [--json REPORT.json]
  scan.py scan  --corpus PATH --rules PASSING.jsonl --out DIR [--workers 32] [--shards A:B] [--resume]
                [--max-per-rule N] [--max-per-group N] [--matcher auto|aho|token] [--id-scheme climbmix]
  scan.py merge --out DIR [--allow-partial]

Rules: JSON lines, one rule per line.

  {"rule_id": "set_a/q12/h3/r1", "group": "set_a/q12/h3",
   "all": [["IUU", "illegal, unreported and unregulated"], ["fishing", "fisheries"]], "none": ["video game"]}

  A document matches a rule when, for every clause (inner list) of "all", it contains at least one of the clause's
  phrases, and it contains none of the phrases of "none" (optional). A group, one hop, is the union of its rules: a
  document matches the group when it matches any rule whose "group" names it. "group" is optional and defaults to the
  rule_id (a group of one rule). "meta" (optional) is carried for the caller and ignored; any other key is an error,
  so a misspelt key cannot silently change a rule. rule_id (unique) and group: at most 255 characters, "/"-separated
  parts of [A-Za-z0-9_.:-] that each start with a letter or digit; they name rule_matches/<rule_id>.txt and
  matches/<group>.txt.

  Phrases are literal strings, not regular expressions. A document contains a phrase when, after both are normalised
  (lower-cased with Arrow's utf8_lower; every run of whitespace, any Unicode whitespace, collapsed to one space; the
  phrase also stripped), the phrase occurs in the text on word boundaries: a phrase that starts with a word character
  (letter, digit or underscore, Unicode-aware, as Python's \\w) must not be preceded by one, and a phrase that ends
  with one must not be followed by one. So "IUU" matches "IUU-listed", "(iuu)" and "IUU.", but not "IUU_2" or
  "Taliuuna"; "fisher" does not match "fishery"; "new york" matches "New\\n  York"; a boundary is only required on a
  side where the phrase has a word character, so "c++" matches "c++x".

The coverage contract. Rules are written per hop, and a hop's rules must match 100% of the hop's established qrels.
Before the scan, `check --known` (the hop's qrels as {"group", "doc_id"} lines) validates every group against them; a
hop whose group is below 100% is not scanned. Because a hop may have as many rules as it needs (each a precise
conjunction, the group their union), the contract is always achievable: a known document no rule matches gets a rule
of its own kind, not a looser rule for everyone. `check` exits 1 while any group fails, and `--write-passing FILE`
writes exactly the rules of the passing groups: that file is the --rules of the scan.

check: per group, matched/known, the known doc ids that no rule of the group matches (ids only), and which rule(s)
match each known document (in --json; on the terminal with --docs), with how many documents each rule carries alone.
--why explains each missed document per rule: the clauses of "all" with no phrase found, the "none" phrases found.
A group fails when a known document is unmatched or its text cannot be read, or when the group has no rule; groups
of the rules file with no known document are listed as unchecked and never pass. Texts come from --texts (JSONL with
"docid" or "doc_id", and "text") or from --corpus: for parquet, the id scheme maps each id to its shard file and row,
each shard file is opened once (--threads at a time) and only the row groups holding needed rows are read; a
zero-padded row such as shard_00012_00034 is read as row 34. check prints doc ids and rule phrases, never document
text.

Corpus (--corpus):
  - a directory of parquet shards with a "text" column and no id column, like ClimbMix (shard_00000.parquet, ...).
    A document's id comes from the id scheme (--id-scheme, ID_SCHEMES below) applied to the shard file's stem and the
    0-based row index. The default, "climbmix", is shard_<NNNNN>_<row> with the row unpadded, as the corpus service
    spells it: shard_00012_34567, shard_00012_7.
  - a .jsonl file, or a directory of .jsonl files, with {"docid", "text"} lines ("doc_id" is accepted too); each file
    is one shard and the ids are the docid fields.
  A shard's number, used by --shards A:B (half-open, as a Python slice; either end may be left out), is the trailing
  number of its file stem (shard_00012 -> 12), or its position in sorted order when the stems carry none.

Output (--out DIR):
  scan.json                         the scan's identity: corpus, id scheme, rules digest, rule ids and groups, caps.
                                    A later scan into DIR (another --shards range, or --resume) must match it.
  shards/<shard>.matches.jsonl      {"rule_id", "doc_id"} per (rule, document) match, in row order
  shards/<shard>.groups.jsonl       {"group", "doc_id"} per (group, document) match: the union of the group's rules,
                                    each document once
  shards/<shard>.done.json          written last, so its presence marks the shard done: docs, seconds, exact counts
                                    per rule and per group, and "recorded" where a cap cut one short
  counts.json                       from `merge`: exact matches per group and per rule, recorded ids where capped,
                                    and each group's rules
  matches/<group>.txt               from `merge`: each group's recorded doc ids, one per line, in corpus order
  rule_matches/<rule_id>.txt        the same per rule

  Every file is written to a temporary name and renamed. --resume skips shards whose done.json exists; without it a
  scan refuses to redo a done shard. Each worker process takes one shard at a time and streams it in record batches
  (--batch-rows), never holding more than one batch of one shard. --max-per-rule N and --max-per-group N (default 0,
  unlimited) stop recording a rule's, or a group's, ids after N per shard; the counts stay exact. Only one scan or
  merge may write to an output directory at a time (scan.lock).

Matchers (--matcher; results are identical, demo/test_scan.py checks them against a brute-force reference):
  aho    pyahocorasick (pip install pyahocorasick): one automaton over every phrase of every rule, word boundaries
         checked at each occurrence; its speed does not depend on the number of phrases. Use it for a full pass.
  token  standard library: a set of each document's word tokens selects the candidate phrases (a phrase's word runs
         are always whole tokens of a matching text), and a regex confirms the multi-token ones. Several times slower,
         and slower still as phrases sharing a common word multiply: for check, demos and small rule sets.
  auto   aho when pyahocorasick is importable, else token (the default)
  Measured throughput is in the Stage 3 README.

Dependencies: Python 3.9+, pyarrow (parquet reading and the normalisation kernels); pyahocorasick optional.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import multiprocessing as mp
import os
import re
import shutil
import socket
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

SEMANTICS = 1        # bump when matching semantics change; an output directory of another version is refused

# Python's str.isspace() set: Unicode general category Zs, or bidirectional class WS, B or S.
_SPACES = (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x85, 0xA0, 0x1680, *range(0x2000, 0x200B),
           0x2028, 0x2029, 0x202F, 0x205F, 0x3000)
# Replace only what changes (runs of 2+ whitespace, and single whitespace other than ' '): one RE2 pass per batch.
_WS_RE2 = "[{}]{{2,}}|[{}]".format("".join(f"\\x{{{c:x}}}" for c in _SPACES),
                                   "".join(f"\\x{{{c:x}}}" for c in _SPACES if c != 0x20))
WORD = re.compile(r"\w+")
_PART = r"[A-Za-z0-9][A-Za-z0-9_.:-]*"
NAME = re.compile(rf"{_PART}(?:/{_PART})*")
RULE_KEYS = {"rule_id", "group", "all", "none", "meta"}


def is_word(c: str) -> bool:
    return c.isalnum() or c == "_"            # exactly Python's \w for str patterns


def normalize(texts) -> list[str]:
    """Lower-case and collapse whitespace runs to one space: the one definition used for documents and phrases.
    `texts`: a pyarrow string array or a list of str (None becomes "")."""
    import pyarrow as pa
    import pyarrow.compute as pc
    arr = texts if isinstance(texts, (pa.Array, pa.ChunkedArray)) else pa.array(list(texts), type=pa.string())
    if arr.null_count:
        arr = pc.fill_null(arr, "")
    return pc.replace_substring_regex(pc.utf8_lower(arr), _WS_RE2, " ").to_pylist()


# ---------------------------------------------------------------------------------------------------------- rules
@dataclass(frozen=True)
class Rule:
    rule_id: str
    group: str
    all: tuple            # clauses, each a tuple of normalised phrases
    none: tuple           # normalised phrases
    raw: dict = field(compare=False, repr=False)      # the rule as written (for --write-passing)


def _name_ok(s) -> bool:
    return isinstance(s, str) and len(s) <= 255 and bool(NAME.fullmatch(s))


def _path_clash(names: Iterable[str]) -> str | None:
    """A name whose file (<name>.txt) would sit where another name needs a directory."""
    files = {n + ".txt" for n in names}
    for n in names:
        parts = n.split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in files:
                return n
    return None


def load_rules(path: str) -> list[Rule]:
    raw = []
    if not Path(path).is_file():
        raise SystemExit(f"--rules {path}: no such file")
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{n}: not a JSON line ({e})")
            raw.append((n, obj))
    if not raw:
        raise SystemExit(f"{path}: no rules")
    strings, seen = [], set()
    for n, obj in raw:
        where = f"{path}:{n}"
        if not isinstance(obj, dict):
            raise SystemExit(f"{where}: a rule is a JSON object")
        extra = set(obj) - RULE_KEYS
        if extra:
            raise SystemExit(f"{where}: unknown key(s) {sorted(extra)}; a rule has rule_id, group, all, none, meta")
        rid = obj.get("rule_id")
        for key, val in (("rule_id", rid), ("group", obj.get("group", rid))):
            if not _name_ok(val):
                raise SystemExit(f"{where}: {key} must be at most 255 characters, '/'-separated parts of "
                                 f"[A-Za-z0-9_.:-] each starting with a letter or digit (got {val!r})")
        if rid in seen:
            raise SystemExit(f"{where}: duplicate rule_id {rid!r}")
        seen.add(rid)
        clauses = obj.get("all")
        if not isinstance(clauses, list) or not clauses or not all(isinstance(c, list) and c for c in clauses):
            raise SystemExit(f"{where}: \"all\" must be a non-empty list of non-empty lists of phrases")
        none = obj.get("none", [])
        if not isinstance(none, list):
            raise SystemExit(f"{where}: \"none\" must be a list of phrases")
        for p in [p for c in clauses for p in c] + none:
            if not isinstance(p, str):
                raise SystemExit(f"{where}: phrases are strings (got {p!r})")
            if "\0" in p:
                raise SystemExit(f"{where}: a phrase may not contain NUL")
            strings.append(p)
    norm = iter(s.strip() for s in normalize(strings))
    rules = []
    for n, obj in raw:
        clauses = tuple(tuple(dict.fromkeys(next(norm) for _ in c)) for c in obj["all"])
        none = tuple(dict.fromkeys(next(norm) for _ in obj.get("none", [])))
        if any(p == "" for c in clauses for p in c) or "" in none:
            raise SystemExit(f"{path}:{n}: a phrase is empty after whitespace normalisation")
        rules.append(Rule(obj["rule_id"], obj.get("group", obj["rule_id"]), clauses, none, obj))
    for kind, names in (("rule_id", [r.rule_id for r in rules]), ("group", {r.group for r in rules})):
        bad = _path_clash(names)
        if bad:
            raise SystemExit(f"{path}: {kind} {bad!r} needs a directory where another {kind} names a file")
    return rules


def rules_digest(rules: list[Rule]) -> str:
    blob = json.dumps([SEMANTICS] + [[r.rule_id, r.group, r.all, r.none] for r in rules], ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


class Engine:
    """The distinct phrases of all rules (a phrase id per normalised phrase), rule evaluation on the set of phrase ids
    a document contains, and the rule -> group map."""

    def __init__(self, rules: list[Rule]):
        self.rules = rules
        index: dict[str, int] = {}
        post = defaultdict(list)                    # phrase id -> [(rule index, clause bit)]
        self.full, self.none = [], []
        for r, rule in enumerate(rules):
            for c, clause in enumerate(rule.all):
                for p in clause:
                    post[index.setdefault(p, len(index))].append((r, 1 << c))
            self.full.append((1 << len(rule.all)) - 1)
            self.none.append(frozenset(index.setdefault(p, len(index)) for p in rule.none))
        self.phrases = list(index)                  # position = phrase id
        self.index = index
        self.post = [tuple(post.get(i, ())) for i in range(len(index))]
        self.groups = list(dict.fromkeys(r.group for r in rules))
        gpos = {g: i for i, g in enumerate(self.groups)}
        self.rule_group = [gpos[r.group] for r in rules]
        self.group_rules = defaultdict(list)
        for r, rule in enumerate(rules):
            self.group_rules[rule.group].append(r)

    def evaluate(self, found: set) -> list[int]:
        """Indices of the rules a document with these phrase ids matches."""
        masks: dict[int, int] = {}
        for pid in found:
            for r, bit in self.post[pid]:
                masks[r] = masks.get(r, 0) | bit
        none = self.none
        return sorted(r for r, m in masks.items() if m == self.full[r] and (not none[r] or none[r].isdisjoint(found)))

    def explain(self, r: int, found: set) -> tuple[list[int], list[str]]:
        """Why rule r does not match: its "all" clauses with no phrase found (indices), its "none" phrases found."""
        rule = self.rules[r]
        failed = [c for c, clause in enumerate(rule.all) if not any(self.index[p] in found for p in clause)]
        return failed, [p for p in rule.none if self.index[p] in found]


# ------------------------------------------------------------------------------------------------------- matchers
class AhoMatcher:
    """pyahocorasick: every occurrence of every phrase in one pass, word boundaries checked per occurrence."""
    name = "aho"

    def __init__(self, phrases: list[str]):
        import ahocorasick
        a = ahocorasick.Automaton()
        for pid, p in enumerate(phrases):
            a.add_word(p, (pid, len(p) - 1, is_word(p[0]), is_word(p[-1])))
        a.make_automaton()
        self._iter = a.iter

    def find(self, t: str) -> set:
        found = set()
        last = len(t) - 1
        for end, (pid, back, left, right) in self._iter(t):
            if pid in found:
                continue
            if left:
                s = end - back
                if s and (t[s - 1].isalnum() or t[s - 1] == "_"):
                    continue
            if right and end < last and (t[end + 1].isalnum() or t[end + 1] == "_"):
                continue
            found.add(pid)
        return found


class TokenMatcher:
    """Standard library. Every maximal word run of a phrase is a whole word token of any text that contains the
    phrase (the boundary rule), so the phrase's longest run, looked up in the document's token set, selects the
    candidates; a single-run phrase is then matched, a longer one is confirmed with a boundary regex. A phrase with
    no word character is a plain substring test (it has no boundary)."""
    name = "token"

    def __init__(self, phrases: list[str]):
        self.by_key: dict[str, list] = defaultdict(list)
        self.bare = []
        self._rx: dict[int, re.Pattern] = {}
        self._phrases = phrases
        for pid, p in enumerate(phrases):
            runs = WORD.findall(p)
            if not runs:
                self.bare.append((pid, p))
                continue
            key = max(runs, key=len)
            simple = len(runs) == 1 and runs[0] == p
            self.by_key[key].append((pid, None if simple else tuple(set(runs) - {key})))
        self.keys = frozenset(self.by_key)

    def _regex(self, pid: int) -> re.Pattern:
        rx = self._rx.get(pid)
        if rx is None:
            p = self._phrases[pid]
            rx = self._rx[pid] = re.compile(("(?<!\\w)" if is_word(p[0]) else "") + re.escape(p)
                                            + ("(?!\\w)" if is_word(p[-1]) else ""))
        return rx

    def find(self, t: str) -> set:
        found = set()
        toks = set(WORD.findall(t))
        for key in self.keys.intersection(toks):
            for pid, others in self.by_key[key]:
                if others is None or (all(o in toks for o in others) and self._regex(pid).search(t)):
                    found.add(pid)
        for pid, p in self.bare:
            if p in t:
                found.add(pid)
        return found


MATCHERS = {"aho": AhoMatcher, "token": TokenMatcher}


def resolve_matcher(name: str) -> str:
    if name == "token":
        return name
    try:
        import ahocorasick  # noqa: F401  (checked here, in the parent, before any worker starts)
        return "aho"
    except ImportError:
        if name == "aho":
            raise SystemExit("--matcher aho needs pyahocorasick (pip install pyahocorasick)")
        print("scan: pyahocorasick is not importable; using the slower token matcher (pip install pyahocorasick)",
              file=sys.stderr)
        return "token"


# --------------------------------------------------------------------------------------------------------- corpus
@dataclass(frozen=True)
class IdScheme:
    """How a parquet corpus without an id column names its documents. make(shard file stem, 0-based row) -> doc id;
    parse(doc id) -> (shard file stem, row) or None, used by `check` to read a document by id; stem: a regex every
    shard file stem must match (checked before a scan starts)."""
    make: Callable[[str, int], str]
    parse: Callable[[str], "tuple[str, int] | None"]
    stem: str = r".+"


def _climbmix_parse(doc_id: str):
    m = re.fullmatch(r"shard_(\d+)_(\d+)", doc_id)
    return (f"shard_{int(m.group(1)):05d}", int(m.group(2))) if m else None


def _stem_row_parse(doc_id: str):
    stem, _, row = doc_id.rpartition("_")
    return (stem, int(row)) if stem and row.isdigit() else None


ID_SCHEMES = {
    # ClimbMix: shard_00012.parquet row 7 -> shard_00012_7 (the corpus service's spelling; never zero-padded)
    "climbmix": IdScheme(make=lambda stem, row: f"{stem}_{row}", parse=_climbmix_parse, stem=r"shard_\d{5}"),
    # any other stem: <stem>_<row>
    "stem_row": IdScheme(make=lambda stem, row: f"{stem}_{row}", parse=_stem_row_parse),
}


@dataclass(frozen=True)
class Shard:
    key: int
    name: str             # file stem
    path: str
    kind: str             # parquet | jsonl


def list_shards(corpus: str) -> list[Shard]:
    p = Path(corpus)
    if p.is_dir():
        files = sorted(p.glob("*.parquet")) or sorted(p.glob("*.jsonl"))
        if not files:
            raise SystemExit(f"--corpus {corpus}: no .parquet or .jsonl files")
    elif p.is_file():
        files = [p]
    else:
        raise SystemExit(f"--corpus {corpus}: no such file or directory")
    kinds = {f.suffix for f in files}
    if not kinds <= {".parquet", ".jsonl"} or len(kinds) > 1:
        raise SystemExit(f"--corpus {corpus}: expected .parquet or .jsonl files, got {sorted(kinds)}")
    nums = [re.search(r"(\d+)$", f.stem) for f in files]
    keys = [int(m.group(1)) for m in nums] if all(nums) else []
    if len(set(keys)) != len(files):
        keys = list(range(len(files)))
    kind = "parquet" if ".parquet" in kinds else "jsonl"
    return sorted((Shard(k, f.stem, str(f.resolve()), kind) for k, f in zip(keys, files)), key=lambda s: s.key)


def select_shards(shards: list[Shard], spec: str | None) -> list[Shard]:
    if not spec:
        return shards
    m = re.fullmatch(r"\s*(\d*)\s*:\s*(\d*)\s*", spec)
    if not m:
        raise SystemExit(f"--shards {spec!r}: expected A:B, A: or :B (shard numbers, B exclusive)")
    lo = int(m.group(1)) if m.group(1) else None
    hi = int(m.group(2)) if m.group(2) else None
    sel = [s for s in shards if (lo is None or s.key >= lo) and (hi is None or s.key < hi)]
    if not sel:
        raise SystemExit(f"--shards {spec}: no shard in range")
    return sel


def _doc_fields(obj: dict, where: str) -> tuple[str, str]:
    doc_id = obj.get("docid", obj.get("doc_id"))
    if not isinstance(doc_id, str):
        raise ValueError(f"{where}: no \"docid\"")
    text = obj.get("text")
    return doc_id, text if isinstance(text, str) else ""


def iter_shard(shard: Shard, scheme: IdScheme, batch_rows: int) -> Iterator[tuple[list, object]]:
    """(doc ids, texts) per record batch; texts is a pyarrow array (parquet) or a list of str (jsonl)."""
    if shard.kind == "parquet":
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(shard.path)
        if "text" not in pf.schema_arrow.names:
            raise ValueError(f"{shard.path}: no \"text\" column")
        row, make = 0, scheme.make
        for batch in pf.iter_batches(batch_size=batch_rows, columns=["text"], use_threads=False):
            n = batch.num_rows
            yield [make(shard.name, i) for i in range(row, row + n)], batch.column(0)
            row += n
        return
    ids, texts = [], []
    with open(shard.path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            d, t = _doc_fields(json.loads(line), f"{shard.path}:{n}")
            ids.append(d)
            texts.append(t)
            if len(ids) >= batch_rows:
                yield ids, texts
                ids, texts = [], []
    if ids:
        yield ids, texts


# ----------------------------------------------------------------------------------------------------------- scan
_W: dict = {}


def _init_worker(rules: list[Rule], matcher: str, scheme: str, out: str, rcap: int, gcap: int, batch_rows: int,
                 digest: str) -> None:
    import pyarrow as pa
    pa.set_cpu_count(1)                       # one core per worker process: --workers is the parallelism
    pa.set_io_thread_count(1)
    engine = Engine(rules)
    _W.update(engine=engine, matcher=MATCHERS[matcher](engine.phrases), matcher_name=matcher,
              scheme=ID_SCHEMES[scheme], out=Path(out), rcap=rcap, gcap=gcap, batch_rows=batch_rows, digest=digest,
              rid_json=[json.dumps(r.rule_id) for r in rules], gid_json=[json.dumps(g) for g in engine.groups])


def write_json_atomic(path: Path, obj) -> None:
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _scan_shard(shard: Shard) -> dict:
    w = _W
    engine = w["engine"]
    find, evaluate, rule_group = w["matcher"].find, engine.evaluate, engine.rule_group
    rid_json, gid_json, rcap, gcap = w["rid_json"], w["gid_json"], w["rcap"], w["gcap"]
    counts, recorded = [0] * len(rid_json), [0] * len(rid_json)
    gcounts, grecorded = [0] * len(gid_json), [0] * len(gid_json)
    sdir = w["out"] / "shards"
    final = {k: sdir / f"{shard.name}.{k}.jsonl" for k in ("matches", "groups")}
    tmp = {k: p.with_name(f".{p.name}.tmp{os.getpid()}") for k, p in final.items()}
    t0, docs = time.perf_counter(), 0
    try:
        with open(tmp["matches"], "w", encoding="utf-8", buffering=1 << 20) as fr, \
                open(tmp["groups"], "w", encoding="utf-8", buffering=1 << 20) as fg:
            for ids, texts in iter_shard(shard, w["scheme"], w["batch_rows"]):
                docs += len(ids)
                for doc_id, t in zip(ids, normalize(texts)):
                    found = find(t)
                    if not found:
                        continue
                    rules = evaluate(found)
                    if not rules:
                        continue
                    jd = json.dumps(doc_id)
                    groups = set()
                    for r in rules:
                        counts[r] += 1
                        if not rcap or recorded[r] < rcap:
                            recorded[r] += 1
                            fr.write(f'{{"rule_id": {rid_json[r]}, "doc_id": {jd}}}\n')
                        groups.add(rule_group[r])
                    for g in sorted(groups):
                        gcounts[g] += 1
                        if not gcap or grecorded[g] < gcap:
                            grecorded[g] += 1
                            fg.write(f'{{"group": {gid_json[g]}, "doc_id": {jd}}}\n')
            for f in (fr, fg):
                f.flush()
                os.fsync(f.fileno())
        for k in final:
            os.replace(tmp[k], final[k])
        secs = time.perf_counter() - t0
        rids, gids = [r.rule_id for r in engine.rules], engine.groups
        write_json_atomic(sdir / f"{shard.name}.done.json", {
            "shard": shard.name, "path": shard.path, "docs": docs, "seconds": round(secs, 3),
            "matcher": w["matcher_name"], "rules_digest": w["digest"], "semantics": SEMANTICS,
            "max_per_rule": rcap, "max_per_group": gcap, "host": socket.gethostname(),
            "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "counts": {rids[r]: n for r, n in enumerate(counts) if n},
            "recorded": {rids[r]: recorded[r] for r, n in enumerate(counts) if recorded[r] != n},
            "group_counts": {gids[g]: n for g, n in enumerate(gcounts) if n},
            "group_recorded": {gids[g]: grecorded[g] for g, n in enumerate(gcounts) if grecorded[g] != n}})
    except Exception as e:                     # one bad shard must not stop the others; it stays not done
        for p in tmp.values():
            p.unlink(missing_ok=True)
        return {"shard": shard.name, "error": f"{type(e).__name__}: {e}"}
    return {"shard": shard.name, "docs": docs, "seconds": secs, "matches": sum(gcounts)}


def _lock(out: Path):
    f = open(out / "scan.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(f"{out}: another scan or merge is writing to this directory (scan.lock)")
    return f


def _hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def cmd_scan(a) -> int:
    rules = load_rules(a.rules)
    digest = rules_digest(rules)
    all_shards = list_shards(a.corpus)
    shards = select_shards(all_shards, a.shards)
    scheme = ID_SCHEMES[a.id_scheme]
    if shards[0].kind == "parquet":
        bad = [s.name for s in all_shards if not re.fullmatch(scheme.stem, s.name)]
        if bad:
            raise SystemExit(f"--id-scheme {a.id_scheme} needs shard files named like {scheme.stem!r}; "
                             f"{len(bad)} are not (first: {bad[0]}). Choose another --id-scheme.")
    matcher = resolve_matcher(a.matcher)
    out = Path(a.out)
    sdir = out / "shards"
    sdir.mkdir(parents=True, exist_ok=True)
    lock = _lock(out)
    man = {"semantics": SEMANTICS, "corpus": str(Path(a.corpus).resolve()), "corpus_kind": shards[0].kind,
           "id_scheme": a.id_scheme if shards[0].kind == "parquet" else None, "shards_total": len(all_shards),
           "rules": str(Path(a.rules).resolve()), "rules_digest": digest, "max_per_rule": a.max_per_rule,
           "max_per_group": a.max_per_group, "n_rules": len(rules),
           "rules_groups": [[r.rule_id, r.group] for r in rules]}
    mpath = out / "scan.json"
    if mpath.exists():
        old = json.loads(mpath.read_text())
        keys = ("semantics", "corpus", "id_scheme", "rules_digest", "max_per_rule", "max_per_group")
        diff = [k for k in keys if old.get(k) != man[k]]
        if diff:
            raise SystemExit(f"{out} holds a scan with different {', '.join(diff)} (scan.json); use a new --out")
    else:
        write_json_atomic(mpath, man)
    for stale in sdir.glob(".*.tmp*"):         # left by an interrupted scan; the lock says none is running
        stale.unlink()
    done = {p.name[:-len(".done.json")] for p in sdir.glob("*.done.json")}
    todo = [s for s in shards if s.name not in done]
    skipped = len(shards) - len(todo)
    if skipped and not a.resume:
        raise SystemExit(f"{skipped} of the {len(shards)} selected shards are already done in {out}; "
                         "pass --resume to skip them")
    workers = max(1, min(a.workers, len(todo)))
    engine = Engine(rules)
    print(f"scan: {len(rules)} rules in {len(engine.groups)} groups ({len(engine.phrases)} distinct phrases), "
          f"matcher {matcher}; {len(todo)} shards to scan" + (f", {skipped} already done" if skipped else "")
          + f"; {workers} workers", file=sys.stderr, flush=True)
    if not todo:
        lock.close()
        return 0
    init = (rules, matcher, a.id_scheme, str(out), a.max_per_rule, a.max_per_group, a.batch_rows, digest)
    t0, ndone, docs, busy, errors = time.time(), 0, 0, 0.0, []
    if workers == 1:
        _init_worker(*init)
        results: Iterable[dict] = map(_scan_shard, todo)
        pool = None
    else:
        pool = mp.get_context("spawn").Pool(workers, initializer=_init_worker, initargs=init)
        results = pool.imap_unordered(_scan_shard, todo, chunksize=1)
    try:
        for res in results:
            ndone += 1
            if "error" in res:
                errors.append(res)
                print(f"[{ndone}/{len(todo)}] {res['shard']} FAILED: {res['error']}", file=sys.stderr, flush=True)
                continue
            docs += res["docs"]
            busy += res["seconds"]
            el = time.time() - t0
            eta = el / ndone * (len(todo) - ndone)
            print(f"[{ndone}/{len(todo)}] {res['shard']}  {res['docs']:,} docs  {res['seconds']:.1f} s  "
                  f"{res['docs'] / max(res['seconds'], 1e-9):,.0f} docs/s  {res['matches']:,} group matches  "
                  f"| elapsed {_hms(el)}  eta {_hms(eta)}", file=sys.stderr, flush=True)
    except BaseException:                      # Ctrl-C or a crash: stop the workers; finished shards stay done
        if pool is not None:
            pool.terminate()
            pool.join()
        raise
    if pool is not None:
        pool.close()
        pool.join()
    el = time.time() - t0
    print(f"scan: {ndone - len(errors)} shards, {docs:,} docs in {_hms(el)} ({docs / max(el, 1e-9):,.0f} docs/s; "
          f"{docs / max(busy, 1e-9):,.0f} docs/s per worker); {len(errors)} failed", file=sys.stderr)
    lock.close()
    return 1 if errors else 0


# ---------------------------------------------------------------------------------------------------------- merge
def _merge_ids(sdir: Path, shards: list[str], suffix: str, key: str, names: list[str], dest: Path) -> dict:
    """Concatenate the per-shard lines of each name (rule id or group) into dest/<name>.txt, in shard order.
    Returns the number of ids written per name."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    buf, nbuf, written, made = defaultdict(list), 0, defaultdict(int), set()

    def path(name: str) -> Path:
        p = dest / f"{name}.txt"
        if p.parent not in made:
            p.parent.mkdir(parents=True, exist_ok=True)
            made.add(p.parent)
        return p

    def flush():
        for name, ids in buf.items():
            with open(path(name), "a", encoding="utf-8") as f:
                f.write("\n".join(ids) + "\n")
            written[name] += len(ids)
        buf.clear()

    for s in shards:
        with open(sdir / f"{s}.{suffix}.jsonl", encoding="utf-8") as f:
            for line in f:
                o = json.loads(line)
                buf[o[key]].append(o["doc_id"])
                nbuf += 1
                if nbuf >= 1_000_000:
                    flush()
                    nbuf = 0
    flush()
    for name in names:
        path(name).touch()
    return written


def _swap_dir(tmp: Path, final: Path) -> None:
    if final.exists():
        old = final.with_name(f".{final.name}.old{os.getpid()}")
        final.rename(old)
        tmp.rename(final)
        shutil.rmtree(old)
    else:
        tmp.rename(final)


def cmd_merge(a) -> int:
    out = Path(a.out)
    mpath = out / "scan.json"
    if not mpath.exists():
        raise SystemExit(f"{out}: no scan.json (not a scan output directory)")
    man = json.loads(mpath.read_text())
    lock = _lock(out)
    sdir = out / "shards"
    done = {}
    for p in sdir.glob("*.done.json"):
        d = json.loads(p.read_text())
        if d.get("rules_digest") != man["rules_digest"]:
            raise SystemExit(f"{p}: scanned with other rules than scan.json")
        done[d["shard"]] = d
    try:
        expected = [s.name for s in list_shards(man["corpus"])]
    except SystemExit:                         # corpus not reachable from here: trust what was scanned
        expected = sorted(done)
    order = {n: i for i, n in enumerate(expected)}
    missing = [n for n in expected if n not in done]
    if missing and not a.allow_partial:
        raise SystemExit(f"{len(missing)} of {len(expected)} shards are not scanned yet (first: {missing[0]}); "
                         "finish with `scan --resume`, or pass --allow-partial")
    names = sorted(done, key=lambda n: (order.get(n, len(order)), n))
    rule_ids = [r for r, _ in man["rules_groups"]]
    group_rules = defaultdict(list)
    for r, g in man["rules_groups"]:
        group_rules[g].append(r)
    groups = list(group_rules)
    tot = {}
    for kind, ids, ckey, rkey in (("rule", rule_ids, "counts", "recorded"),
                                  ("group", groups, "group_counts", "group_recorded")):
        c, rec = dict.fromkeys(ids, 0), dict.fromkeys(ids, 0)
        for n in names:
            for k, v in done[n][ckey].items():
                c[k] += v
                rec[k] += done[n][rkey].get(k, v)
        tot[kind] = c, rec
    staged = []
    for kind, suffix, key, ids, dirname in (("group", "groups", "group", groups, "matches"),
                                            ("rule", "matches", "rule_id", rule_ids, "rule_matches")):
        tmp = out / f".{dirname}.tmp{os.getpid()}"
        written = _merge_ids(sdir, names, suffix, key, ids, tmp)
        bad = [i for i in ids if written.get(i, 0) != tot[kind][1][i]]
        if bad:
            for t, _ in staged + [(tmp, None)]:
                shutil.rmtree(t, ignore_errors=True)
            raise SystemExit(f"{suffix}.jsonl files disagree with done.json for {len(bad)} {kind}s (first: "
                             f"{bad[0]}); rescan those shards")
        staged.append((tmp, out / dirname))
    for tmp, final in staged:
        _swap_dir(tmp, final)
    docs = sum(d["docs"] for d in done.values())
    (gc, gr), (rc, rr) = tot["group"], tot["rule"]
    write_json_atomic(out / "counts.json", {
        "complete": not missing, "shards_done": len(done), "shards_total": len(expected), "docs": docs,
        "rules_digest": man["rules_digest"], "max_per_rule": man["max_per_rule"],
        "max_per_group": man["max_per_group"],
        "group_counts": gc, "rule_counts": rc,
        "group_recorded": {g: gr[g] for g in groups if gr[g] != gc[g]},
        "rule_recorded": {r: rr[r] for r in rule_ids if rr[r] != rc[r]},
        "groups": dict(group_rules)})
    print(f"merge: {len(done)}/{len(expected)} shards, {docs:,} docs; {len(groups)} groups "
          f"({sum(1 for v in gc.values() if not v)} with no match), {len(rule_ids)} rules; "
          f"{sum(gc.values()):,} group matches ({sum(gr.values()):,} recorded) -> {out}/counts.json, "
          f"{out}/matches/, {out}/rule_matches/", file=sys.stderr)
    lock.close()
    return 0


# ---------------------------------------------------------------------------------------------------------- check
def _read_rows(path: str, rows: dict) -> dict:
    """{row: [doc ids]} -> {doc id: text} for one parquet shard, reading only the row groups that hold those rows."""
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    got, lo = {}, 0
    for g in range(pf.metadata.num_row_groups):
        hi = lo + pf.metadata.row_group(g).num_rows
        hit = sorted(r for r in rows if lo <= r < hi)
        if hit:
            col = pf.read_row_group(g, columns=["text"], use_threads=False).column(0)
            for r, t in zip(hit, col.take([r - lo for r in hit]).to_pylist()):
                for d in rows[r]:
                    got[d] = t or ""
        lo = hi
    return got


def fetch_texts(ids: Iterable[str], corpus: str | None, texts: str | None, scheme: IdScheme,
                threads: int = 16) -> dict[str, str]:
    """Full text of the given doc ids that can be found; the rest are absent from the result."""
    want = set(ids)
    got: dict[str, str] = {}
    shards = list_shards(corpus) if corpus else []
    if texts or shards[0].kind == "jsonl":
        for path in [Path(texts)] if texts else [Path(s.path) for s in shards]:
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if line.strip():
                        d, t = _doc_fields(json.loads(line), f"{path}:{n}")
                        if d in want:
                            got[d] = t
        return got
    by_name = {s.name: s for s in shards}
    need = defaultdict(lambda: defaultdict(list))  # shard -> row -> [doc ids as given]
    for d in want:
        loc = scheme.parse(d)
        if loc and loc[0] in by_name:
            need[loc[0]][loc[1]].append(d)
    with ThreadPoolExecutor(max(1, min(threads, len(need) or 1))) as ex:
        for part in ex.map(lambda name: _read_rows(by_name[name].path, need[name]), sorted(need)):
            got.update(part)
    return got


def cmd_check(a) -> int:
    if bool(a.corpus) == bool(a.texts):
        raise SystemExit("check: give exactly one of --corpus and --texts")
    rules = load_rules(a.rules)
    by_id = {r.rule_id: r for r in rules}
    engine = Engine(rules)
    known: dict[str, dict] = defaultdict(dict)      # group -> {doc id: None}, in file order
    with open(a.known, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                o = json.loads(line)
                if not isinstance(o.get("group"), str) or not isinstance(o.get("doc_id"), str):
                    raise SystemExit(f"{a.known}:{n}: expected {{\"group\", \"doc_id\"}}")
                known[o["group"]][o["doc_id"]] = None
    t0 = time.time()
    needed = sorted({d for ds in known.values() for d in ds})
    texts = fetch_texts(needed, a.corpus, a.texts, ID_SCHEMES[a.id_scheme], a.threads)
    t_fetch = time.time() - t0
    matcher = MATCHERS[resolve_matcher(a.matcher)](engine.phrases)
    have = [d for d in needed if d in texts]
    found = {d: matcher.find(t) for d, t in zip(have, normalize([texts[d] for d in have]))}
    matched_rules = {d: set(engine.evaluate(f)) for d, f in found.items()}
    report, failing = [], 0
    for g in list(engine.groups) + [g for g in known if g not in engine.group_rules]:
        rs = engine.group_rules.get(g, [])
        if g not in known:
            continue
        docs = list(known[g])
        per_doc = {d: [rules[r].rule_id for r in rs if r in matched_rules[d]] for d in docs if d in found}
        missed = [d for d, m in per_doc.items() if not m]
        unavailable = [d for d in docs if d not in found]
        carried = {rules[r].rule_id: sum(1 for m in per_doc.values() if rules[r].rule_id in m) for r in rs}
        alone = {rules[r].rule_id: sum(1 for m in per_doc.values() if m == [rules[r].rule_id]) for r in rs}
        passed = bool(rs) and not missed and not unavailable
        failing += not passed
        entry = {"group": g, "rules": [rules[r].rule_id for r in rs], "passed": passed, "known": len(docs),
                 "available": len(per_doc), "matched": len(per_doc) - len(missed),
                 "fraction": round((len(per_doc) - len(missed)) / len(docs), 4) if docs else None,
                 "missed": missed, "unavailable": unavailable, "rule_docs": carried, "rule_docs_alone": alone,
                 "docs": per_doc}
        if a.why or a.json:
            entry["why"] = {d: {rules[r].rule_id: dict(zip(("failed_clauses", "none_hits"),
                                                           engine.explain(r, found[d]))) for r in rs}
                            for d in missed}
        report.append(entry)
    unchecked = [g for g in engine.groups if g not in known]
    w = max([len(x["group"]) for x in report] + [5])
    print(f"{'group':<{w}}  matched/known  fraction  result")
    for x in report:
        frac = "-" if x["fraction"] is None else f"{x['fraction']:.3f}"
        why = "PASS" if x["passed"] else ("FAIL: no rule for this group" if not x["rules"] else "FAIL")
        print(f"{x['group']:<{w}}  {x['matched']:>6}/{x['known']:<6}  {frac:>8}  {why}")
        for rid in x["rules"]:
            print(f"{'':<{w}}    rule {rid}: {x['rule_docs'][rid]} docs ({x['rule_docs_alone'][rid]} by it alone)")
        if x["missed"]:
            print(f"{'':<{w}}    missed ({len(x['missed'])}): {' '.join(x['missed'])}")
        if x["unavailable"]:
            print(f"{'':<{w}}    text unavailable ({len(x['unavailable'])}): {' '.join(x['unavailable'])}")
        if a.docs:
            for d, m in x["docs"].items():
                print(f"{'':<{w}}    {d}: {', '.join(m) if m else '-'}")
        if a.why:
            for d, per_rule in x.get("why", {}).items():
                for rid, e in per_rule.items():
                    rule = by_id[rid]
                    why = [f"all[{c}] {list(rule.all[c])[:3]}" for c in e["failed_clauses"]]
                    why += [f"none {p!r}" for p in e["none_hits"]]
                    print(f"{'':<{w}}    why {d} / {rid}: " + "; ".join(why))
    passing = {x["group"] for x in report if x["passed"]}
    summary = {"groups_checked": len(report), "groups_passed": len(passing), "groups_failed": failing,
               "groups_unchecked": unchecked, "known_docs": len(needed), "unavailable_docs": len(needed) - len(have),
               "fetch_seconds": round(t_fetch, 2)}
    print(f"check: {len(report)} groups checked, {len(passing)} pass, {failing} fail; {len(unchecked)} groups of the "
          f"rules have no known documents (unchecked); {len(needed)} known documents, "
          f"{len(needed) - len(have)} unavailable; texts read in {t_fetch:.1f} s")
    if a.json:
        write_json_atomic(Path(a.json), {"summary": summary, "groups": report})
    if a.write_passing:
        keep = [r for r in rules if r.group in passing]
        p = Path(a.write_passing)
        tmp = p.with_name(f".{p.name}.tmp{os.getpid()}")
        tmp.write_text("".join(json.dumps(r.raw, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8")
        os.replace(tmp, p)
        print(f"check: {len(keep)} rules of the {len(passing)} passing groups -> {p}")
    return 1 if failing or not report else 0


# ----------------------------------------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="scan a corpus: matches per shard, by rule and by group")
    s.add_argument("--corpus", required=True, help="directory of parquet or .jsonl shards, or one such file")
    s.add_argument("--rules", required=True, help="JSONL rules (format in the module docstring)")
    s.add_argument("--out", required=True)
    s.add_argument("--workers", type=int, default=32)
    s.add_argument("--shards", help="A:B, a range of shard numbers (B exclusive)")
    s.add_argument("--resume", action="store_true", help="skip shards already done")
    s.add_argument("--max-per-rule", type=int, default=0,
                   help="record at most N ids per rule per shard (counts stay exact); 0: unlimited")
    s.add_argument("--max-per-group", type=int, default=0,
                   help="record at most N ids per group per shard (counts stay exact); 0: unlimited")
    s.add_argument("--matcher", choices=["auto", *MATCHERS], default="auto")
    s.add_argument("--id-scheme", choices=sorted(ID_SCHEMES), default="climbmix")
    s.add_argument("--batch-rows", type=int, default=1024, help="rows per record batch")
    m = sub.add_parser("merge", help="per-group and per-rule results: counts.json, matches/, rule_matches/")
    m.add_argument("--out", required=True)
    m.add_argument("--allow-partial", action="store_true", help="merge although some shards are not done")
    c = sub.add_parser("check", help="validate each group against its known relevant documents (exit 1 if any fails)")
    c.add_argument("--rules", required=True)
    c.add_argument("--known", required=True, help="JSONL {group, doc_id}: the hop's established qrels")
    c.add_argument("--corpus")
    c.add_argument("--texts", help="JSONL {docid or doc_id, text}")
    c.add_argument("--id-scheme", choices=sorted(ID_SCHEMES), default="climbmix")
    c.add_argument("--matcher", choices=["auto", *MATCHERS], default="auto")
    c.add_argument("--threads", type=int, default=16, help="parquet shard files read at once")
    c.add_argument("--docs", action="store_true", help="print the rules matching each known document")
    c.add_argument("--why", action="store_true", help="explain each missed document, rule by rule")
    c.add_argument("--json", help="write the full report here")
    c.add_argument("--write-passing", metavar="FILE", help="write the rules of the passing groups (the scan's rules)")
    a = ap.parse_args(argv)
    if a.cmd == "scan":
        if a.workers < 1 or a.batch_rows < 1 or a.max_per_rule < 0 or a.max_per_group < 0:
            raise SystemExit("--workers and --batch-rows must be positive, the caps non-negative")
        return cmd_scan(a)
    return cmd_merge(a) if a.cmd == "merge" else cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())
