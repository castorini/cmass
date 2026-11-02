#!/usr/bin/env python3
"""Stage 3 rule writing for the corpus scan (scan.py): per question, an agent writes match rules for every hop that
has qrels, such that each hop's group (the union of its rules) matches 100% of the hop's established qrels documents
while matching as few other documents as it can. This tool gives the agent its packet, checks coverage, measures
breadth on a sample of shards, reports each question's state from disk, and collects the rules of finished questions
into the --rules file of the full scan. The agent's guide is RULES.md; the workflow is rules.js.

  rules.py [OPTIONS] packet  QID              work/QID.md (the agent's packet) and QID.known.jsonl
  rules.py [OPTIONS] doc     QID DOCID [--grep TERM ...] [--offset N] [--chars N]
  rules.py [OPTIONS] check   QID              scan.py check of the question's groups -> work/QID.check.json
  rules.py [OPTIONS] breadth QID [--shards A:B | --sample N] [--workers N]
  rules.py [OPTIONS] state   [QID ...] [--json] [--brief] [--todo]
  rules.py [OPTIONS] collect --out-rules PATH [--no-recheck]

OPTIONS (before or after the subcommand). Each is also a key of the --config JSON file (the flag's name with "_" for
"-", e.g. {"qrels_hops": ..., "max_est": 5000}); a flag wins over the file, and relative paths in the file are
relative to the file's directory.
  --config FILE        JSON object with any of the keys below
  --qrels-hops PATH    JSONL, one question per line: {"id", "hops": [{"id", "kind" (or "type"), "clue", "docs":
                       {doc id: "full" | "partial" | "audit", or {"supports": ..., "quotes": [...], ...}}}]}
                       (Stage 3's qrels_hops.jsonl). Derivation hops (kind "derivation", or "derivation": true),
                       dropped hops and hops without documents are not scanned and get no rules.
  --qrels PATH         optional JSONL {"id", "question", "answer"}: shown in the packet
  --standards DIR      optional <qid>.json hop standards ({"hops": {hop id: {"subject", "full", "partial",
                       "reject"}}}, QRELS.md): shown in the packet
  --quotes PATH        optional JSONL {"id", "hop", "doc_id", "quote"}, for qrels formats without quotes
  --corpus PATH        the corpus, as scan.py reads it: a directory of parquet shards (ClimbMix) or JSONL. Texts
                       are read locally, never through a search service.
  --id-scheme NAME     parquet document ids (scan.py ID_SCHEMES; default climbmix)
  --set NAME           the question set: groups are <set>/<qid>/<hop>
  --out DIR            rules and work files: DIR/<set>/<qid>.rules.jsonl (written by the agent),
                       DIR/<set>/<qid>.known.jsonl, DIR/<set>/work/<qid>.{md,check.json,breadth.json}
  --max-est N          breadth cap per group, estimated over the whole corpus (default 20,000)
  --breadth-sample N   shards in the standard breadth sample (default 24), spread evenly over the corpus
  --breadth-shards A:B a shard range as the standard sample instead (scan.py --shards syntax)
  --breadth-workers N  worker processes per breadth run (default 24)
  --breadth-parallel N breadth runs at once under DIR (default 2; a run waits for a free slot)
  --matcher NAME       auto | aho | token (scan.py; default auto)

packet: for each hop with qrels documents, its id, kind, clue and standard, and per known document its id, support
label, length and a short excerpt read from the corpus: the recorded quote(s) when there are any (from the qrels
entry or --quotes), each with up to 150 characters of its context, else the ~500-character window holding the most of
the clue's rarest terms (rarity by a static proxy: stop words dropped, longer words, numbers and capitalised names
weighted up), else the document's start.
Also writes <qid>.known.jsonl, {"group", "doc_id"} per known document: scan.py check's --known.

doc: a document's text from the corpus (--offset/--chars), or with --grep the normalised text (what rules match
against: lower-cased, whitespace collapsed) around each occurrence of each term, with the counts on word boundaries
and as a bare substring; and which hops of QID the document is known for.

check: validates the rules file (scan.py's format; every rule's group is one of the question's hop groups, its
rule_id starts with "<group>/", every hop has a rule, a "meta": {"broad_ok": ...} is a reason of 10 or more
characters), runs scan.py check --why on the question's groups and writes work/<qid>.check.json (scan.py's report
plus the digests that tie it to these rules and these known documents). Exit 1 while a hop is below 100%.

breadth: runs scan.py's shard scanner (the question's rules only) on the standard sample, by default 24 shards
spread evenly over the corpus (every 272nd of ClimbMix's 6,543), and reports per rule and per group the matches in the
sample and the corpus-wide estimate (matches x total shards / sampled shards), with up to 12 example doc ids per rule
for `doc`. A group estimated above --max-est is flagged. The standard sample's report is work/<qid>.breadth.json;
--shards or --sample other than the standard writes work/<qid>.breadth_custom.json, which the state ignores. Exit 1
while a group is over the cap without broad_ok.

state: per question, from disk only (deterministic; reads no document), "next" is one of
  packet        the packet or known file is missing or out of date with the qrels       run packet
  write         no rules file, or it does not validate (detail says why)              write or fix the rules file
  check         no check report for the current rules and known documents              run check
  fix_coverage  a hop's group misses known documents (missed ids per hop)              add variants or rules
  breadth       no standard breadth report for the current rules                       run breadth
  narrow        a group's estimate is over the cap without broad_ok                     narrow its rules
  done          rules valid, every hop at 100%, every group within the cap or broad_ok
  skip          the question has no hop with qrels documents: nothing to write
  error         the question is not in --qrels-hops, or its ids cannot name a group
A group over the cap is accepted when at least one of its rules carries "meta": {"broad_ok": "<reason>"} and every
rule of the group that is itself over the cap carries one. "meta" is not part of scan.py's rules digest, so adding a
reason needs no new check or breadth run; any change to a rule's id, group, "all" or "none" makes both reports stale.

collect: the rules of every done question, as written, re-checked together with scan.py check --write-passing
against all their known documents (--no-recheck: trust each question's current check report); writes PATH and
DIR/<set>/collect/summary.json (questions by state, rules, groups, each broad_ok group with its estimate and reason).

Dependencies: Python 3.9+, pyarrow; pyahocorasick optional (scan.py).
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import multiprocessing as mp
import os
import re
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scan  # noqa: E402

DEFAULTS = {"id_scheme": "climbmix", "max_est": 20000, "breadth_sample": 24, "breadth_shards": None,
            "breadth_workers": 24, "breadth_parallel": 2, "matcher": "auto"}
PATH_KEYS = ("qrels_hops", "qrels", "standards", "quotes", "corpus", "out")
CONFIG_KEYS = set(PATH_KEYS) | {"set"} | set(DEFAULTS)
INT_KEYS = ("max_est", "breadth_sample", "breadth_workers", "breadth_parallel")
EXCERPT_CHARS = 500
EXAMPLES_PER_SHARD = 3          # example ids recorded per rule per sampled shard (counts stay exact)
EXAMPLES = 12                   # example ids reported per rule
MIN_REASON = 10                 # characters of a broad_ok reason
STOP = frozenset("""a an the of in on at to for from by with and or but nor not no so as is are was were be been being
am do does did has have had having will would can could may might must shall should this that these those which who whom
whose what when where why how it its into onto than then there their theirs they them he she his her hers him i we our
you your me my mine us also any all each every other another one some such about after before over under between
during while within without upon via per out up down off only own same very just more most less least few many much
both either neither whether if else until since because though although yet still again once here""".split())


def die(msg: str) -> None:
    raise SystemExit(f"rules: {msg}")


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scan.write_json_atomic(path, obj)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_jsonl(path: str, what: str) -> list[dict]:
    if not Path(path).is_file():
        die(f"{what} {path}: no such file")
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    o = json.loads(line)
                except json.JSONDecodeError as e:
                    die(f"{path}:{n}: not a JSON line ({e})")
                if not isinstance(o, dict):
                    die(f"{path}:{n}: expected a JSON object")
                rows.append(o)
    return rows


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " ..."


# ------------------------------------------------------------------------------------------------------- config
class Ctx:
    """Resolved options and the question data."""

    def __init__(self, a):
        cfg: dict = {}
        self.config = getattr(a, "config", None)
        if self.config:
            p = Path(self.config).resolve()
            self.config = str(p)
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                die(f"--config {p}: {e}")
            if not isinstance(cfg, dict):
                die(f"--config {p}: expected a JSON object")
            bad = set(cfg) - CONFIG_KEYS
            if bad:
                die(f"--config {p}: unknown key(s) {sorted(bad)}; known: {sorted(CONFIG_KEYS)}")
            for k in PATH_KEYS:
                if isinstance(cfg.get(k), str):
                    cfg[k] = str((p.parent / cfg[k]).resolve())
        opts = dict(DEFAULTS)
        opts.update({k: v for k, v in cfg.items() if v is not None})
        for k in CONFIG_KEYS:
            v = getattr(a, k, None)
            if v is not None:
                opts[k] = str(Path(v).resolve()) if k in PATH_KEYS else v
        for k in ("qrels_hops", "corpus", "set", "out"):
            if not opts.get(k):
                die(f"--{k.replace('_', '-')} is required (flag or config key {k!r})")
        for k in INT_KEYS:
            try:
                opts[k] = int(opts[k])
            except (TypeError, ValueError):
                die(f"{k} must be an integer (got {opts[k]!r})")
            if opts[k] < (0 if k == "max_est" else 1):
                die(f"{k} must be {'non-negative' if k == 'max_est' else 'positive'} (got {opts[k]})")
        if opts["id_scheme"] not in scan.ID_SCHEMES:
            die(f"id_scheme {opts['id_scheme']!r}: one of {sorted(scan.ID_SCHEMES)}")
        if opts["matcher"] not in ("auto", *scan.MATCHERS):
            die(f"matcher {opts['matcher']!r}: auto, aho or token")
        if not scan._name_ok(opts["set"]) or "/" in opts["set"]:
            die(f"--set {opts['set']!r}: letters, digits and _.:- only, starting with a letter or digit")
        self.o = opts
        self.set = opts["set"]
        self.corpus = opts["corpus"]
        self.out = Path(opts["out"])
        self.dir = self.out / self.set
        self.work = self.dir / "work"
        self.scheme = scan.ID_SCHEMES[opts["id_scheme"]]
        self._q = None

    # how to run this tool again with the same options (for the packet)
    def command(self) -> str:
        parts = [sys.executable if sys.executable else "python3", str(Path(__file__).resolve())]
        if self.config:
            parts += ["--config", self.config, "--set", self.set]
        else:
            for k in ("qrels_hops", "qrels", "standards", "quotes", "corpus", "set", "out"):
                if self.o.get(k):
                    parts += [f"--{k.replace('_', '-')}", str(self.o[k])]
            for k, v in DEFAULTS.items():
                if self.o.get(k) != v and self.o.get(k) is not None:
                    parts += [f"--{k.replace('_', '-')}", str(self.o[k])]
        return " ".join(parts)

    # ---------------------------------------------------------------- questions
    @property
    def questions(self) -> dict:
        if self._q is None:
            q = {}
            for o in read_jsonl(self.o["qrels_hops"], "--qrels-hops"):
                qid = str(o.get("id", ""))
                if not qid:
                    die("--qrels-hops: a line has no \"id\"")
                if qid in q:
                    die(f"--qrels-hops: question {qid} appears twice")
                if not isinstance(o.get("hops"), list):
                    die(f"--qrels-hops: question {qid} has no \"hops\" list")
                q[qid] = o
            quotes: dict = {}
            if self.o.get("quotes"):
                for o in read_jsonl(self.o["quotes"], "--quotes"):
                    if isinstance(o.get("quote"), str) and o["quote"].strip():
                        key = (str(o.get("id")), str(o.get("hop")), str(o.get("doc_id")))
                        quotes.setdefault(key, []).append(o["quote"])
            self._quotes = quotes
            self._q = q
            self._meta = {}
            if self.o.get("qrels"):
                for o in read_jsonl(self.o["qrels"], "--qrels"):
                    self._meta[str(o.get("id"))] = o
        return self._q

    def qids(self) -> list[str]:
        return list(self.questions)

    def meta(self, qid: str) -> dict:
        self.questions
        return self._meta.get(qid, {})

    def standard(self, qid: str) -> dict:
        if not self.o.get("standards"):
            return {}
        s = read_json(Path(self.o["standards"]) / f"{qid}.json")
        if not isinstance(s, dict):
            return {}
        hs = s.get("hops", s)
        return hs if isinstance(hs, dict) else {}

    def all_hops(self, qid: str) -> list[dict]:
        """Every hop of the question, normalised, with "scan" (True for a hop that gets rules) and "skip" (why not)."""
        out = []
        for h in self.questions[qid]["hops"]:
            if not isinstance(h, dict):
                continue
            hid = str(h.get("id", ""))
            kind = str(h.get("kind") or h.get("type") or "")
            docs = {}
            raw_docs = h.get("docs") or {}
            if isinstance(raw_docs, list):         # tolerate a plain list of ids
                raw_docs = {d: "" for d in raw_docs}
            for d, v in raw_docs.items():
                if isinstance(v, dict):
                    sup = str(v.get("supports", ""))
                    qs = [x for x in (v.get("quotes") or []) if isinstance(x, str) and x.strip()]
                else:
                    sup, qs = str(v or ""), []
                qs += self._quotes.get((qid, hid, str(d)), [])
                docs[str(d)] = {"supports": sup, "quotes": list(dict.fromkeys(qs))}
            skip = ("derivation" if kind == "derivation" or h.get("derivation") is True
                    else "dropped" if h.get("status") == "dropped" else "no documents" if not docs else "")
            out.append({"id": hid, "kind": kind, "clue": str(h.get("clue") or ""), "role": h.get("role"),
                        "candidate": h.get("candidate"), "docs": docs, "scan": not skip, "skip": skip})
        return out

    def hops(self, qid: str) -> list[dict]:
        return [h for h in self.all_hops(qid) if h["scan"]]

    def group(self, qid: str, hid: str) -> str:
        return f"{self.set}/{qid}/{hid}"

    def known_rows(self, qid: str) -> list[dict]:
        return [{"group": self.group(qid, h["id"]), "doc_id": d} for h in self.hops(qid) for d in h["docs"]]

    # ---------------------------------------------------------------- paths
    def rules_path(self, qid):
        return self.dir / f"{qid}.rules.jsonl"

    def known_path(self, qid):
        return self.dir / f"{qid}.known.jsonl"

    def packet_path(self, qid):
        return self.work / f"{qid}.md"

    def check_path(self, qid):
        return self.work / f"{qid}.check.json"

    def breadth_path(self, qid, custom=False):
        return self.work / f"{qid}.breadth{'_custom' if custom else ''}.json"

    def sample_spec(self) -> str:
        bs = self.o.get("breadth_shards")
        return f"shards {bs}" if bs else f"sample {self.o['breadth_sample']}"

    def fetch(self, ids) -> dict:
        return scan.fetch_texts(ids, self.corpus, None, self.scheme)


def question_ok(ctx: Ctx, qid: str) -> str:
    """'' or why the question cannot have rules."""
    if qid not in ctx.questions:
        return f"question {qid} is not in --qrels-hops"
    ids = [h["id"] for h in ctx.all_hops(qid)]
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        return f"question {qid} has more than one hop with id {', '.join(dup)}"
    for h in ctx.hops(qid):
        g = ctx.group(qid, h["id"])
        if not scan._name_ok(g) or g.count("/") != 2:
            return (f"group {g!r} is not a valid scan.py name: the set, question id and hop id must each be "
                    "letters, digits and _.:- starting with a letter or digit")
    return ""


def need_question(ctx: Ctx, qid: str) -> list[dict]:
    err = question_ok(ctx, qid)
    if err:
        die(err)
    hops = ctx.hops(qid)
    if not hops:
        die(f"question {qid} has no hop with qrels documents: nothing to write (derivation hops and hops without "
            "documents are not scanned)")
    return hops


def ensure_known(ctx: Ctx, qid: str) -> Path:
    p = ctx.known_path(qid)
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ctx.known_rows(qid))
    if not p.exists() or p.read_text(encoding="utf-8") != text:
        write_atomic(p, text)
    return p


# -------------------------------------------------------------------------------------------------------- rules
def load_question_rules(ctx: Ctx, qid: str):
    """(rules or None, errors): scan.py's validation plus this question's: groups, rule_id prefixes, every hop ruled,
    broad_ok reasons."""
    p = ctx.rules_path(qid)
    if not p.exists():
        return None, [f"no rules file {p}"]
    try:
        rules = scan.load_rules(str(p))
    except SystemExit as e:
        return None, [str(e)]
    groups = {ctx.group(qid, h["id"]): h["id"] for h in ctx.hops(qid)}
    errs = []
    for r in rules:
        if r.group not in groups:
            errs.append(f"rule {r.rule_id}: group {r.group!r} is not a hop group of {qid} "
                        f"(one of: {', '.join(groups)})")
        elif not r.rule_id.startswith(r.group + "/"):
            errs.append(f"rule {r.rule_id}: its rule_id must start with its group, {r.group}/")
        m = r.raw.get("meta")
        if isinstance(m, dict) and "broad_ok" in m:
            v = m["broad_ok"]
            if not isinstance(v, str) or len(v.strip()) < MIN_REASON:
                errs.append(f"rule {r.rule_id}: meta.broad_ok must be the reason, a string of at least {MIN_REASON} "
                            "characters")
    ruled = {r.group for r in rules}
    for g, hid in groups.items():
        if g not in ruled:
            errs.append(f"hop {hid}: no rule (group {g})")
    return rules, errs


def broad_reason(rule) -> str:
    m = rule.raw.get("meta")
    v = m.get("broad_ok") if isinstance(m, dict) else None
    return v.strip() if isinstance(v, str) and len(v.strip()) >= MIN_REASON else ""


# ------------------------------------------------------------------------------------------------------- packet
def clue_terms(clue: str, k: int = 6) -> dict[str, int]:
    """The clue's k rarest terms by a static proxy, with their weights: stop words dropped; longer words, numbers and
    capitalised words (not the first) weighted up."""
    w: dict[str, int] = {}
    for i, m in enumerate(re.finditer(r"\w+", clue)):
        t = m.group(0)
        low = t.lower()
        if low in STOP or (len(low) < 3 and not low.isdigit()):
            continue
        score = len(low) + (4 if any(c.isdigit() for c in low) else 0) + (3 if i and t[0].isupper() else 0)
        w[low] = max(w.get(low, 0), score)
    return dict(sorted(w.items(), key=lambda kv: (-kv[1], kv[0]))[:k])


def best_window(text: str, terms: dict[str, int], width: int = EXCERPT_CHARS):
    """(excerpt, terms in it) for the width-character window of the whitespace-collapsed text holding the greatest
    weight of distinct terms, or None when no term occurs."""
    t = flat(text)
    hits = sorted((m.start(), term) for term in terms
                  for m in re.finditer(r"(?<!\w)" + re.escape(term) + r"(?!\w)", t, re.IGNORECASE))
    if not hits:
        return None
    # windows start a quarter-width before each hit; a sliding count of the terms starting inside the window
    cnt: dict[str, int] = {}
    score, lo, hi, best = 0, 0, 0, None
    for p, _ in hits:
        s = max(0, p - width // 4)
        e = s + width
        while hi < len(hits) and hits[hi][0] < e:
            term = hits[hi][1]
            cnt[term] = cnt.get(term, 0) + 1
            score += terms[term] if cnt[term] == 1 else 0
            hi += 1
        while hits[lo][0] < s:
            term = hits[lo][1]
            cnt[term] -= 1
            score -= terms[term] if cnt[term] == 0 else 0
            lo += 1
        if best is None or score > best[0]:
            best = (score, s, e, {x for x, n in cnt.items() if n})
    _, s, e, seen = best
    if e < len(t):
        e = s + max(len(t[s:e].rsplit(" ", 1)[0]), 1)
    if s > 0 and t[s - 1] != " ":                     # start on a word
        nxt = t.find(" ", s)
        s = nxt + 1 if 0 <= nxt < s + 40 else s
    ex = ("... " if s > 0 else "") + t[s:e].strip() + (" ..." if e < len(t) else "")
    return ex, sorted(seen, key=lambda x: (-terms[x], x))


def quote_in_context(t: str, quote: str, around: int = 150) -> str:
    """The quote, in **bold**, with up to `around` characters of the (whitespace-collapsed) text t on each side: the
    words around a quote often name the subject the quote leaves out."""
    q = flat(quote)
    i = t.find(q)
    if i < 0:
        m = re.search(re.escape(q), t, re.IGNORECASE)
        i = m.start() if m else -1
    if i < 0:
        return f"**{clip(q, 600)}** (not found verbatim in the text)"
    j = i + len(q)
    s, e = max(0, i - around), min(len(t), j + around)
    if s > 0 and t[s - 1] != " ":
        s = min(t.find(" ", s) + 1 or s, i)
    if e < len(t) and t[e] != " ":
        sp = t.rfind(" ", j, e)
        e = sp if sp > j else e
    body = t[i:j] if len(q) <= 600 else clip(t[i:j], 600)
    return (("... " if s else "") + t[s:i] + f"**{body}**" + (t[j:e] if len(q) <= 600 else "")
            + (" ..." if e < len(t) else ""))


def excerpt(text: str | None, quotes: list[str], terms: dict[str, int]) -> tuple[str, str]:
    """(how, excerpt)."""
    if text is None:
        return "text unavailable in the corpus", ""
    if quotes:
        t = flat(text)
        shown = [quote_in_context(t, q) for q in quotes[:3]]
        more = f" (+{len(quotes) - 3} more)" if len(quotes) > 3 else ""
        return f"recorded quote{'s' if len(quotes) > 1 else ''}{more}, in context", "\n>\n> ".join(shown)
    w = best_window(text, terms) if terms else None
    if w:
        return "window around " + ", ".join(f'"{x}"' for x in w[1]), w[0]
    return "document start (no clue term found)", clip(flat(text), EXCERPT_CHARS)


def cmd_packet(ctx: Ctx, a) -> int:
    qid = a.qid
    hops = need_question(ctx, qid)
    allh = ctx.all_hops(qid)
    std = ctx.standard(qid)
    meta = ctx.meta(qid)
    texts = ctx.fetch({d for h in hops for d in h["docs"]})
    R = ctx.command()
    L = [f"# Rules packet: {ctx.set}/{qid}", "",
         f"Write the match rules of this question to {ctx.rules_path(qid)}: JSON lines in scan.py's format, as "
         f"{HERE / 'RULES.md'} describes. Each hop below is one group; every rule names its hop's group and its "
         "rule_id starts with \"<group>/\". Each group must match every known document listed under its hop (100%), "
         f"and stay within the breadth cap ({ctx.o['max_est']:,} documents estimated over the corpus).", ""]
    for k in ("question", "answer"):
        v = meta.get(k)
        if v not in (None, "", []):
            L.append(f"{k}: {flat(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))}")
    skipped = [f"hop {h['id']} ({h['skip']})" for h in allh if not h["scan"]]
    L += ["", f"Hops with qrels documents: {len(hops)}"
          + (f"; not scanned: {', '.join(skipped)}" if skipped else ""), ""]
    unavailable = []
    for h in hops:
        g = ctx.group(qid, h["id"])
        L += [f"## Hop {h['id']}  (group {g})", ""]
        bits = [f"kind: {h['kind'] or '-'}"]
        if h.get("role"):
            bits.append(f"role: {h['role']}")
        if h.get("candidate"):
            bits.append(f"candidate: {flat(str(h['candidate']))}")
        L += ["; ".join(bits), f"clue: {flat(h['clue']) or '-'}"]
        s = std.get(h["id"])
        if isinstance(s, dict):
            L += ["standard:"]
            for k in ("subject", "full", "partial", "reject"):
                v = s.get(k)
                if isinstance(v, list):
                    for x in v:
                        L.append(f"  {k}: {flat(str(x))}")
                elif v:
                    L.append(f"  {k}: {flat(str(v))}")
        terms = clue_terms(h["clue"])
        L += ["", f"Known documents ({len(h['docs'])}): every one must match a rule of {g}.", ""]
        for d, info in h["docs"].items():
            text = texts.get(d)
            how, ex = excerpt(text, info["quotes"], terms)
            size = f"{len(text):,} characters" if text is not None else "not in the corpus"
            L.append(f"### {d}  ({info['supports'] or '-'}; {size}; {how})")
            if text is None:
                unavailable.append(d)
                L += ["", "(this document cannot be read from the corpus: no rule can match it; report it)", ""]
                continue
            L += ["", "> " + ex, ""]
    L += ["## Commands", "",
          f"R=\"{R}\"",
          f"$R doc {qid} DOCID [--grep PHRASE ...]   # a document's text, or the normalised text around each phrase",
          f"$R check {qid}                          # coverage: every hop at 100%? missed ids and why",
          f"$R breadth {qid}                        # matches in the shard sample and the corpus-wide estimate",
          f"$R state {qid}                          # what is left to do (done when it says next: done)", ""]
    write_atomic(ctx.packet_path(qid), "\n".join(L))
    known = ensure_known(ctx, qid)
    n = sum(len(h["docs"]) for h in hops)
    print(f"packet {qid}: {len(hops)} hops, {n} known documents ({len(unavailable)} unavailable) -> "
          f"{ctx.packet_path(qid)}; known -> {known}")
    if unavailable:
        print(f"packet {qid}: not in the corpus: {' '.join(unavailable)}")
    return 0


# ---------------------------------------------------------------------------------------------------------- doc
def cmd_doc(ctx: Ctx, a) -> int:
    qid, d = a.qid, a.docid
    if qid not in ctx.questions:
        die(f"question {qid} is not in --qrels-hops")
    text = ctx.fetch([d]).get(d)
    if text is None:
        die(f"{d}: not found in the corpus {ctx.corpus}")
    known = [f"hop {h['id']} ({h['docs'][d]['supports'] or '-'})" for h in ctx.hops(qid) if d in h["docs"]]
    print(f"{d}: {len(text):,} characters; " + (f"known for {', '.join(known)} of {qid}" if known
                                                 else f"not a known document of {qid}"))
    if a.grep:
        norm = scan.normalize([text])[0]
        for term in a.grep:
            p = scan.normalize([term])[0].strip()
            if not p:
                continue
            rx = re.compile(("(?<!\\w)" if scan.is_word(p[0]) else "") + re.escape(p)
                            + ("(?!\\w)" if scan.is_word(p[-1]) else ""))
            hits = [m.start() for m in rx.finditer(norm)]
            sub = norm.count(p)
            print(f"--- {term!r}: {len(hits)} on word boundaries, {sub} as a bare substring"
                  + (f" (showing {a.max_hits})" if len(hits) > a.max_hits else ""))
            for pos in hits[:a.max_hits]:
                s, e = max(0, pos - a.window), min(len(norm), pos + len(p) + a.window)
                print(f"  [{pos}] {'...' if s else ''}{norm[s:e]}{'...' if e < len(norm) else ''}")
        print("(--grep shows the normalised text: lower-cased, whitespace collapsed, as rules see it)")
        return 0
    s = max(0, a.offset)
    part = text[s:s + a.chars]
    print(part)
    if s + a.chars < len(text):
        print(f"\n[... {len(text) - s - a.chars:,} more characters: --offset {s + a.chars}]")
    return 0


# -------------------------------------------------------------------------------------------------------- check
def cmd_check(ctx: Ctx, a) -> int:
    qid = a.qid
    hops = need_question(ctx, qid)
    known = ensure_known(ctx, qid)
    rules, errs = load_question_rules(ctx, qid)
    if errs:
        for e in errs:
            print(f"rules {qid}: {e}")
        print(f"rules {qid}: fix the rules file {ctx.rules_path(qid)} first ({len(errs)} problem(s))")
        return 2
    ctx.work.mkdir(parents=True, exist_ok=True)
    tmp = ctx.work / f".{qid}.check.tmp{os.getpid()}.json"
    try:
        rc = scan.main(["check", "--rules", str(ctx.rules_path(qid)), "--known", str(known), "--corpus", ctx.corpus,
                        "--id-scheme", ctx.o["id_scheme"], "--matcher", ctx.o["matcher"], "--why", "--json", str(tmp)])
        rep = json.loads(tmp.read_text(encoding="utf-8"))
    finally:
        tmp.unlink(missing_ok=True)
    rep = {"qid": qid, "set": ctx.set, "rules_digest": scan.rules_digest(rules),
           "known_digest": digest(ctx.known_rows(qid)), "corpus": ctx.corpus,
           "checked": time.strftime("%Y-%m-%dT%H:%M:%S"), **rep}
    write_json(ctx.check_path(qid), rep)
    by_g = {x["group"]: x for x in rep["groups"]}
    print(f"\ncheck {qid} (per hop):")
    for h in hops:
        x = by_g.get(ctx.group(qid, h["id"]))
        if not x:
            print(f"  hop {h['id']}: not checked")
            continue
        print(f"  hop {h['id']}: {x['matched']}/{x['known']} {'PASS' if x['passed'] else 'FAIL'}"
              + (f"; missed: {' '.join(x['missed'])}" if x["missed"] else "")
              + (f"; unavailable: {' '.join(x['unavailable'])}" if x["unavailable"] else ""))
    print(f"check {qid}: report -> {ctx.check_path(qid)}")
    return 0 if rc == 0 and all(x["passed"] for x in rep["groups"]) else 1


# ------------------------------------------------------------------------------------------------------ breadth
def sample_shards(shards: list, n: int) -> list:
    """n shards spread evenly over the sorted list: every (len // n)-th from the first; all of them when n >= len."""
    if n >= len(shards):
        return list(shards)
    step = len(shards) // n
    return [shards[i * step] for i in range(n)]


@contextmanager
def breadth_slot(ctx: Ctx):
    """One of breadth_parallel slots under DIR/.locks, so concurrent agents do not oversubscribe the host."""
    d = ctx.out / ".locks"
    d.mkdir(parents=True, exist_ok=True)
    waited = False
    while True:
        for i in range(ctx.o["breadth_parallel"]):
            f = open(d / f"breadth_{i}.lock", "w")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                f.close()
                continue
            try:
                yield
            finally:
                f.close()
            return
        if not waited:
            print("breadth: waiting for a breadth slot (other breadth runs are using them)", file=sys.stderr,
                  flush=True)
            waited = True
        time.sleep(2)


def run_sample(ctx: Ctx, rules, shards, workers: int, tmp: Path) -> tuple[dict, dict, dict, int]:
    """scan.py's shard scanner on the given shards: (counts per rule, counts per group, examples per rule, docs)."""
    dig = scan.rules_digest(rules)
    matcher = scan.resolve_matcher(ctx.o["matcher"])
    (tmp / "shards").mkdir(parents=True)
    init = (rules, matcher, ctx.o["id_scheme"], str(tmp), EXAMPLES_PER_SHARD, EXAMPLES_PER_SHARD, 1024, dig)
    workers = max(1, min(workers, len(shards)))
    if workers == 1:
        scan._init_worker(*init)
        results = [scan._scan_shard(s) for s in shards]
    else:
        with mp.get_context("spawn").Pool(workers, initializer=scan._init_worker, initargs=init) as pool:
            results = list(pool.imap_unordered(scan._scan_shard, shards, chunksize=1))
    bad = [r for r in results if "error" in r]
    if bad:
        die(f"breadth: {len(bad)} shard(s) failed (first: {bad[0]['shard']}: {bad[0]['error']})")
    rc = {r.rule_id: 0 for r in rules}
    gc = {r.group: 0 for r in rules}
    per_shard = {r.rule_id: [] for r in rules}      # rule -> [[ids of one shard], ...]
    docs = 0
    for s in shards:
        done = json.loads((tmp / "shards" / f"{s.name}.done.json").read_text())
        docs += done["docs"]
        for k, v in done["counts"].items():
            rc[k] += v
        for k, v in done["group_counts"].items():
            gc[k] += v
        got: dict = {}
        with open(tmp / "shards" / f"{s.name}.matches.jsonl", encoding="utf-8") as f:
            for line in f:
                o = json.loads(line)
                got.setdefault(o["rule_id"], []).append(o["doc_id"])
        for k, v in got.items():
            per_shard[k].append(v)
    ex = {}
    for k, lists in per_shard.items():           # round robin over the shards: examples from across the sample
        ex[k] = [x for i in range(EXAMPLES_PER_SHARD) for v in lists if i < len(v) for x in [v[i]]][:EXAMPLES]
    return rc, gc, ex, docs


def cmd_breadth(ctx: Ctx, a) -> int:
    qid = a.qid
    need_question(ctx, qid)
    rules, errs = load_question_rules(ctx, qid)
    if errs:
        for e in errs:
            print(f"rules {qid}: {e}")
        print(f"rules {qid}: fix the rules file {ctx.rules_path(qid)} first ({len(errs)} problem(s))")
        return 2
    all_shards = scan.list_shards(ctx.corpus)
    if all_shards[0].kind == "parquet":
        bad = [s.name for s in all_shards if not re.fullmatch(ctx.scheme.stem, s.name)]
        if bad:
            die(f"id_scheme {ctx.o['id_scheme']} needs shard files named like {ctx.scheme.stem!r} (first: {bad[0]})")
    if a.shards:
        spec, shards = f"shards {a.shards}", scan.select_shards(all_shards, a.shards)
    elif a.sample:
        spec, shards = f"sample {a.sample}", sample_shards(all_shards, a.sample)
    elif ctx.o.get("breadth_shards"):
        spec, shards = ctx.sample_spec(), scan.select_shards(all_shards, ctx.o["breadth_shards"])
    else:
        spec, shards = ctx.sample_spec(), sample_shards(all_shards, ctx.o["breadth_sample"])
    standard = spec == ctx.sample_spec()
    factor = len(all_shards) / len(shards)
    cap = ctx.o["max_est"]
    workers = a.workers or ctx.o["breadth_workers"]
    ctx.work.mkdir(parents=True, exist_ok=True)
    tmp = ctx.work / f".breadth_{qid}_{os.getpid()}"
    t0 = time.time()
    try:
        with breadth_slot(ctx):
            t1 = time.time()
            rc, gc, ex, docs = run_sample(ctx, rules, shards, workers, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    secs = time.time() - t1
    est = lambda n: int(math.ceil(n * factor))   # noqa: E731
    reasons = {r.rule_id: broad_reason(r) for r in rules}
    groups = {}
    for g in dict.fromkeys(r.group for r in rules):
        groups[g] = {"sample": gc[g], "est": est(gc[g]), "rules": [r.rule_id for r in rules if r.group == g]}
    rep = {"qid": qid, "set": ctx.set, "rules_digest": scan.rules_digest(rules), "corpus": ctx.corpus,
           "id_scheme": ctx.o["id_scheme"], "spec": spec, "standard": standard,
           "shards": [s.name for s in shards], "shards_total": len(all_shards), "docs_sampled": docs,
           "factor": round(factor, 4), "max_est": cap, "seconds": round(secs, 2),
           "waited_seconds": round(t1 - t0, 2), "measured": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "rules": {r.rule_id: {"group": r.group, "sample": rc[r.rule_id], "est": est(rc[r.rule_id]),
                                 "examples": ex[r.rule_id]} for r in rules},
           "groups": groups}
    path = ctx.breadth_path(qid, custom=not standard)
    write_json(path, rep)
    print(f"breadth {qid}: {len(shards)} of {len(all_shards)} shards "
          f"({spec}{'' if standard else ', not the standard sample'}), "
          f"{docs:,} documents in {secs:.1f} s; estimate = sample matches x {factor:,.1f}; cap {cap:,} per group")
    over = 0
    for g, x in groups.items():
        grs = [r for r in rules if r.group == g]
        ok, why = group_accepted(x["est"], {r.rule_id: est(rc[r.rule_id]) for r in grs},
                                 {r.rule_id: reasons[r.rule_id] for r in grs}, cap)
        over += not ok
        print(f"group {g}: {x['sample']:,} in the sample, est {x['est']:,}  {'ok' if ok else 'OVER CAP'}"
              + (f" ({why})" if why else ""))
        for r in grs:
            e = est(rc[r.rule_id])
            print(f"  rule {r.rule_id}: {rc[r.rule_id]:,} in the sample, est {e:,}"
                  + ("  over cap" if e > cap else "")
                  + (f"  broad_ok: {reasons[r.rule_id]!r}" if reasons[r.rule_id] else "")
                  + (f"\n    examples: {' '.join(ex[r.rule_id])}" if ex[r.rule_id] and e > cap else ""))
    print(f"breadth {qid}: report -> {path}"
          + ("" if standard else " (custom sample: the state reads only the standard one)"))
    return 1 if over else 0


def group_accepted(group_est: int, rule_est: dict, reasons: dict, cap: int) -> tuple[bool, str]:
    if group_est <= cap:
        return True, ""
    if not any(reasons.values()):
        return False, f"est {group_est:,} > cap {cap:,}"
    lacking = [r for r, e in rule_est.items() if e > cap and not reasons.get(r)]
    if lacking:
        return False, f"est {group_est:,} > cap {cap:,}; over the cap without broad_ok: {', '.join(lacking)}"
    return True, "broad_ok"


# -------------------------------------------------------------------------------------------------------- state
def hop_entry(ctx, qid, h) -> dict:
    return {"hop": h["id"], "group": ctx.group(qid, h["id"]), "known": len(h["docs"]), "rules": 0, "matched": -1,
            "missed": [], "unavailable": [], "passed": False, "est": -1, "cap_ok": False, "broad_ok": ""}


def question_state(ctx: Ctx, qid: str) -> dict:
    s = {"set": ctx.set, "qid": qid, "next": "", "done": False, "detail": "", "max_est": ctx.o["max_est"],
         "packet_file": str(ctx.packet_path(qid)), "rules_file": str(ctx.rules_path(qid)),
         "check_file": str(ctx.check_path(qid)), "breadth_file": str(ctx.breadth_path(qid)), "hops": []}

    def out(nxt, detail=""):
        s["next"], s["detail"], s["done"] = nxt, detail, nxt == "done"
        return s

    err = question_ok(ctx, qid)
    if err:
        return out("error", err)
    hops = ctx.hops(qid)
    if not hops:
        return out("skip", "no hop with qrels documents (derivation hops and hops without documents are not scanned)")
    s["hops"] = [hop_entry(ctx, qid, h) for h in hops]
    by_g = {x["group"]: x for x in s["hops"]}
    rows = ctx.known_rows(qid)
    kp = ctx.known_path(qid)
    expected = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    if not ctx.packet_path(qid).exists() or not kp.exists() or kp.read_text(encoding="utf-8") != expected:
        return out("packet", "no packet yet" if not ctx.packet_path(qid).exists()
                   else "the known documents changed since the packet was written")
    rules, errs = load_question_rules(ctx, qid)
    if rules is None and not ctx.rules_path(qid).exists():
        return out("write", "no rules file yet")
    if errs:
        return out("write", "the rules file does not validate: " + "; ".join(errs[:5])
                   + (f"; and {len(errs) - 5} more" if len(errs) > 5 else ""))
    for r in rules:
        by_g[r.group]["rules"] += 1
        if broad_reason(r) and not by_g[r.group]["broad_ok"]:
            by_g[r.group]["broad_ok"] = broad_reason(r)
    rd = scan.rules_digest(rules)
    chk = read_json(ctx.check_path(qid))
    if not (isinstance(chk, dict) and chk.get("rules_digest") == rd and chk.get("known_digest") == digest(rows)
            and chk.get("corpus") == ctx.corpus):
        return out("check", "no check report yet" if chk is None else "the check report is for other rules or "
                   "known documents (re-run check)")
    rep = {x["group"]: x for x in chk.get("groups", [])}
    failing = []
    for g, x in by_g.items():
        c = rep.get(g)
        if c is None:
            failing.append(f"hop {x['hop']}: not checked")
            continue
        x.update(matched=c["matched"], missed=c["missed"], unavailable=c["unavailable"], passed=bool(c["passed"]))
        if not c["passed"]:
            failing.append(f"hop {x['hop']}: {c['matched']}/{c['known']}"
                           + (f", missed {' '.join(c['missed'][:20])}" + (" ..." if len(c["missed"]) > 20 else "")
                              if c["missed"] else "")
                           + (f", unavailable {' '.join(c['unavailable'])}" if c["unavailable"] else ""))
    if failing:
        return out("fix_coverage", "; ".join(failing))
    br = read_json(ctx.breadth_path(qid))
    if not (isinstance(br, dict) and br.get("rules_digest") == rd and br.get("spec") == ctx.sample_spec()
            and br.get("corpus") == ctx.corpus):
        return out("breadth", "no breadth report yet" if br is None else "the breadth report is for other rules or "
                   "another sample (re-run breadth)")
    cap = ctx.o["max_est"]
    reasons = {r.rule_id: broad_reason(r) for r in rules}
    over = []
    for g, x in by_g.items():
        bg = br["groups"].get(g)
        if bg is None:
            return out("breadth", f"the breadth report has no group {g} (re-run breadth)")
        x["est"] = int(bg["est"])
        grs = [r.rule_id for r in rules if r.group == g]
        ok, why = group_accepted(x["est"], {r: int(br["rules"][r]["est"]) for r in grs}, {r: reasons[r] for r in grs},
                                 cap)
        x["cap_ok"] = ok
        if not ok:
            top = max(grs, key=lambda r: br["rules"][r]["est"])
            over.append(f"hop {x['hop']}: {why} (broadest rule {top}: est {br['rules'][top]['est']:,})")
    if over:
        return out("narrow", "; ".join(over))
    broad = [f"hop {x['hop']} est {x['est']:,} (broad_ok)" for x in by_g.values() if x["est"] > cap]
    return out("done", "every hop at 100%, every group within the cap"
               + (f" or broad_ok: {', '.join(broad)}" if broad else ""))


def cmd_state(ctx: Ctx, a) -> int:
    qids = a.qids or ctx.qids()
    rows = [question_state(ctx, str(q)) for q in qids]
    count: dict = {}
    for s in rows:
        count[s["next"]] = count.get(s["next"], 0) + 1
    if a.todo:
        rows = [s for s in rows if s["next"] not in ("done", "skip")]
    if a.json:
        qs = [{k: s[k] for k in ("set", "qid", "next", "done", "detail")} if a.brief else s for s in rows]
        print(json.dumps({"set": ctx.set, "questions": qs, "count": count}, ensure_ascii=False))
        return 0
    for s in rows:
        hops = s["hops"]
        cov = f"{sum(h['passed'] for h in hops)}/{len(hops)} hops at 100%" if hops else "-"
        print(f"{s['qid']}: {s['next']}  ({cov})  {s['detail']}")
        if not a.brief:
            for h in hops:
                print(f"    hop {h['hop']}: rules {h['rules']}, matched {h['matched'] if h['matched'] >= 0 else '?'}"
                      f"/{h['known']}, est {h['est'] if h['est'] >= 0 else '?'}"
                      + (f", broad_ok {h['broad_ok']!r}" if h["broad_ok"] else ""))
    print(f"state {ctx.set}: {len(rows)} question(s): " + ", ".join(f"{k} {v}" for k, v in sorted(count.items())))
    return 0


# ------------------------------------------------------------------------------------------------------ collect
def cmd_collect(ctx: Ctx, a) -> int:
    states = [question_state(ctx, q) for q in ctx.qids()]
    done = [s for s in states if s["done"]]
    if not done:
        die("collect: no question is done (rules.py state)")
    cdir = ctx.dir / "collect"
    cdir.mkdir(parents=True, exist_ok=True)
    lines, known = [], []
    for s in done:
        lines += [ln for ln in ctx.rules_path(s["qid"]).read_text(encoding="utf-8").splitlines() if ln.strip()]
        known += ctx.known_rows(s["qid"])
    out = Path(a.out_rules).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_rules = cdir / f".rules.tmp{os.getpid()}.jsonl"
    tmp_known = cdir / f".known.tmp{os.getpid()}.jsonl"
    write_atomic(tmp_rules, "\n".join(lines) + "\n")
    write_atomic(tmp_known, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in known))
    rc = 0
    try:
        rules = scan.load_rules(str(tmp_rules))       # one file: rule ids unique, no name clashes
        if a.no_recheck:
            write_atomic(out, "\n".join(lines) + "\n")
        else:
            rc = scan.main(["check", "--rules", str(tmp_rules), "--known", str(tmp_known), "--corpus", ctx.corpus,
                            "--id-scheme", ctx.o["id_scheme"], "--matcher", ctx.o["matcher"],
                            "--json", str(cdir / "check.json"), "--write-passing", str(out)])
    finally:
        tmp_rules.unlink(missing_ok=True)
        tmp_known.unlink(missing_ok=True)
    try:
        kept = scan.load_rules(str(out))
    except SystemExit:                                # every group failed the re-check: an empty file
        kept = []
    kept_groups = {r.group for r in kept}
    broad = [{"group": h["group"], "est": h["est"], "broad_ok": h["broad_ok"]}
             for s in done for h in s["hops"] if h["est"] > ctx.o["max_est"]]
    count: dict = {}
    for s in states:
        count[s["next"]] = count.get(s["next"], 0) + 1
    summary = {"set": ctx.set, "out_rules": str(out.resolve()), "rechecked": not a.no_recheck,
               "questions": len(states), "by_state": count, "done": [s["qid"] for s in done],
               "not_done": {s["qid"]: s["next"] for s in states if not s["done"] and s["next"] != "skip"},
               "rules": len(kept), "groups": len(kept_groups),
               "groups_dropped_at_recheck": sorted({r.group for r in rules} - kept_groups),
               "est_total": sum(h["est"] for s in done for h in s["hops"] if h["group"] in kept_groups),
               "broad_ok": broad, "max_est": ctx.o["max_est"], "collected": time.strftime("%Y-%m-%dT%H:%M:%S")}
    write_json(cdir / "summary.json", summary)
    by = ", ".join(f"{k} {v}" for k, v in sorted(count.items()))
    print(f"collect {ctx.set}: {len(done)} done of {len(states)} questions ({by}); "
          f"{len(kept)} rules in {len(kept_groups)} groups -> {out}; estimated candidates over the corpus "
          f"{summary['est_total']:,}; {len(broad)} broad_ok group(s); summary -> {cdir / 'summary.json'}")
    if summary["groups_dropped_at_recheck"]:
        print(f"collect: groups failing the re-check, left out: {' '.join(summary['groups_dropped_at_recheck'])}")
    return 1 if rc or summary["groups_dropped_at_recheck"] else 0


# --------------------------------------------------------------------------------------------------------- main
def common_options() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    S = argparse.SUPPRESS
    p.add_argument("--config", default=S, help="JSON file with any of the options below (flags win)")
    p.add_argument("--qrels-hops", dest="qrels_hops", default=S)
    p.add_argument("--qrels", default=S)
    p.add_argument("--standards", default=S)
    p.add_argument("--quotes", default=S)
    p.add_argument("--corpus", default=S)
    p.add_argument("--id-scheme", dest="id_scheme", choices=sorted(scan.ID_SCHEMES), default=S)
    p.add_argument("--set", default=S)
    p.add_argument("--out", default=S)
    p.add_argument("--max-est", dest="max_est", type=int, default=S)
    p.add_argument("--breadth-sample", dest="breadth_sample", type=int, default=S)
    p.add_argument("--breadth-shards", dest="breadth_shards", default=S)
    p.add_argument("--breadth-workers", dest="breadth_workers", type=int, default=S)
    p.add_argument("--breadth-parallel", dest="breadth_parallel", type=int, default=S)
    p.add_argument("--matcher", choices=["auto", *scan.MATCHERS], default=S)
    return p


def main(argv=None) -> int:
    common = common_options()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], parents=[common],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="See the module docstring.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("packet", parents=[common], help="the agent's packet and the known file")
    p.add_argument("qid")
    d = sub.add_parser("doc", parents=[common], help="a document's text, or the text around phrases")
    d.add_argument("qid")
    d.add_argument("docid")
    d.add_argument("--grep", nargs="+", metavar="PHRASE")
    d.add_argument("--offset", type=int, default=0)
    d.add_argument("--chars", type=int, default=20000)
    d.add_argument("--window", type=int, default=250, help="characters of context each side of a --grep hit")
    d.add_argument("--max-hits", dest="max_hits", type=int, default=5)
    c = sub.add_parser("check", parents=[common], help="coverage of every hop (scan.py check)")
    c.add_argument("qid")
    b = sub.add_parser("breadth", parents=[common], help="matches in a shard sample and corpus-wide estimates")
    b.add_argument("qid")
    g = b.add_mutually_exclusive_group()
    g.add_argument("--shards", help="A:B, a shard range instead of the standard sample")
    g.add_argument("--sample", type=int, help="N shards spread over the corpus instead of the standard sample")
    b.add_argument("--workers", type=int, help="worker processes (default: breadth_workers)")
    s = sub.add_parser("state", parents=[common], help="per question: the next action, from disk")
    s.add_argument("qids", nargs="*")
    s.add_argument("--json", action="store_true")
    s.add_argument("--brief", action="store_true", help="only set, qid, next, done and detail per question")
    s.add_argument("--todo", action="store_true", help="only the questions whose next is not done or skip")
    k = sub.add_parser("collect", parents=[common], help="the rules of every done question, for the full scan")
    k.add_argument("--out-rules", dest="out_rules", required=True)
    k.add_argument("--no-recheck", dest="no_recheck", action="store_true",
                   help="trust each question's check report instead of re-checking all groups together")
    a = ap.parse_args(argv)
    if a.cmd == "breadth" and ((a.sample is not None and a.sample < 1) or (a.workers is not None and a.workers < 1)):
        die("--sample and --workers must be positive")
    ctx = Ctx(a)
    return {"packet": cmd_packet, "doc": cmd_doc, "check": cmd_check, "breadth": cmd_breadth, "state": cmd_state,
            "collect": cmd_collect}[a.cmd](ctx, a)


if __name__ == "__main__":
    sys.exit(main())
