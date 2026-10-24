#!/usr/bin/env python3
"""Stage 3, phase 1: qrels construction from the accepted Stage 2 evidence (candidate pooling, precision filter).

Run inside a run directory (or pass --run / set $CMASS_RUN); --set names the Stage 2 output final/<set>_final.jsonl.
Every file lives under qrels/<set>/ (README.md has the layout); the judging standard agents follow is QRELS.md.

  qrels.py --set S pool [QID ...]          inventory: live hops, Stage 2 evidence (fixed), the candidate pool
  qrels.py --set S show QID                the question, its live hops and its pool, for an agent
  qrels.py --set S std-check QID           the hop standards file (standards/QID.json) is complete
  qrels.py --set S recall QID              recall/QID.queries.json -> deep search, RRF, anchors -> QID.candidates.json
  qrels.py --set S fetch [QID ...]         cache the full text of every pooled document
  qrels.py --set S chunks QID              render the documents to judge: work/QID/chunk_NN_a.md, _b.md, ...
  qrels.py --set S unjudged QID            chunks with no clean verdict file yet
  qrels.py --set S check QID [NN ...]      verdicts: one per document, live hops only, quotes verbatim, reason codes
  qrels.py --set S second QID --round N [--drops-only]   blind second-judge sample of round N (tiering policy)
  qrels.py --set S second-check QID NN --round N
  qrels.py --set S compare QID --round N   second judge vs first; disagreements -> adjudication parts
  qrels.py --set S adj-check QID PART      the adjudicator's verdicts for one part
  qrels.py --set S build [QID ...]         phase1/{qrels.jsonl, qrels_hops.jsonl, rejections.jsonl, stats.json, qrels.trec}
  qrels.py --set S verify [--phase phase2] every Stage 2 evidence document kept, every live hop grounded, quotes verbatim

--phase2 N points the judging commands (show, chunks, unjudged, check, second, second-check, compare, adj-check) at
round N of the duplicate expansion (qrels/<set>/phase2/rN/), whose documents are the near duplicates dups.py queued.

Pool: the documents of every live hop's accepted Stage 2 evidence (fixed: kept for the hops they are cited for, never
re-judged), the documents Stage 1 cited for each hop, every document named in its Stage 2 audit, revision, adversarial and
final-check files, the recall candidates, and (only with "searchlog_top" set) the results of its logged searches. Every candidate that is not fixed is read
in full and judged against all live hops; it stays if it supports at least one. Stdlib only.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import re
import sys
import textwrap
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cmass import records as R  # noqa: E402
from cmass.corpus import Corpus  # noqa: E402
from cmass.run import Run, read_jsonl, write_json, write_jsonl  # noqa: E402
from cmass.text import is_verbatim, norm_ws  # noqa: E402

REASONS = ("no_hop_fact", "wrong_entity", "mention_only", "contradicts", "out_hop_only", "unusable")
SUPPORTS = ("full", "partial")
RECALL_MODES = ("all", "no_seed", "none")

# Stage 3 settings. Layers, later wins: these defaults, the set's family (below), "qrels" at the top of cmass.json,
# "qrels" inside the set's entry. "tiers" and "phase2_tiers" merge key by key; phase2_tiers falls back to tiers.
DEFAULTS = {
    "recall": "all",              # which live hops get a recall pass: all | no_seed (hops Stage 1 cited nothing for) | none
    "recall_k": 20,               # candidates kept per recall hop
    "recall_hits": 100,           # hits per recall query
    "recall_min_queries": 3,
    "rrf_k": 60,
    "searchlog_top": 0,           # pool the top N results of each logged search (0: none, as the CMASS run did; None: all)
    "keep_seed_docs": False,      # keep Stage 1 cited documents of live hops even when the judges drop them
    "chunk_chars": 80_000,        # one judge agent
    "chunk_docs": 15,
    "part_chars": 50_000,         # one file an agent reads in a single Read
    "wrap": 160,
    "min_quote_chars": 15,
    "trec_graded": False,         # qrels.trec relevance 2 for a document with full support of some hop, else 1
    "judge_note": "",             # a dataset-specific judging rule, printed by `show` (e.g. a snapshot date)
    "tiers": {
        "keep_rate": 0.10,        # share of full-support keeps the second judge re-judges
        "min_keeps": 3,
        "drop_rate": 0.20,        # share of drops the second judge re-judges (1.0: every drop)
        "min_drops": 3,
        "all_seed_drops": True,   # every dropped Stage 1 cited document is re-judged too
        "escalate_at": 2,         # this many sampled drops turned into keeps -> every remaining drop is re-judged
    },                            # every partial-only keep is always re-judged
    "phase2_tiers": {},
}
FAMILY_DEFAULTS = {
    "bcp": {"recall": "no_seed", "recall_k": 40, "tiers": {"drop_rate": 1.0}},
    "hotpotqa": {"judge_note": "Read every hop as of the source snapshot: a document that gives only a value or "
                               "holder from after the snapshot does not support the hop (no_hop_fact)."},
}


def qrels_config(run: Run, sid: str) -> dict:
    s = run.config.set(sid)
    out = copy.deepcopy(DEFAULTS)
    for layer in (FAMILY_DEFAULTS.get(s.get("family"), {}), run.config.data.get("qrels") or {}, s.get("qrels") or {}):
        for k, v in layer.items():
            if k not in DEFAULTS:
                raise SystemExit(f"qrels config: unknown key {k!r}; known: {sorted(DEFAULTS)}")
            if k in ("tiers", "phase2_tiers"):
                bad = set(v) - set(DEFAULTS["tiers"])
                if bad:
                    raise SystemExit(f"qrels config: unknown {k} keys {sorted(bad)}")
                out[k] = {**out[k], **v}
            else:
                out[k] = v
    out["phase2_tiers"] = {**out["tiers"], **out["phase2_tiers"]}
    if out["recall"] not in RECALL_MODES:
        raise SystemExit(f"qrels config: recall must be one of {RECALL_MODES}")
    return out


# ----------------------------------------------------------------------------- final records and hops
def all_hops(rec: dict) -> list[dict]:
    """The record's hops (original ids "1".."n", added "A1".."), plus added_hops / dropped_hops when kept apart."""
    out = list(rec.get("hops") or []) + list(rec.get("added_hops") or [])
    out += [dict(h, status="dropped") for h in rec.get("dropped_hops") or []]
    return out


def hop_id(h: dict, i: int) -> str:
    return str(h.get("id") or h.get("index") or i)


def is_out(h: dict) -> bool:
    return h.get("status") == "dropped"


def is_derivation(h: dict) -> bool:
    return h.get("type") == "derivation" or h.get("kind") == "derivation"


def natural_key(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


def sha(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()


# ----------------------------------------------------------------------------- the set being built
class QSet:
    """One set's Stage 3 files. phase2_round=N points the judging directories at round N of the duplicate expansion."""

    def __init__(self, run: Run, sid: str, phase2_round: int | None = None):
        self.run, self.sid = run, sid
        self.cfg = qrels_config(run, sid)
        self.dir = run.qrels_dir(sid)
        self.inventory_path = self.dir / "inventory.jsonl"
        self.standards = self.dir / "standards"
        self.recall_dir = self.dir / "recall"
        self.p2 = phase2_round
        base = self.dir if phase2_round is None else self.p2_dir / f"r{phase2_round}"
        self.work = base / "work"
        self.judged = base / "judged"
        self._corpus = None
        self._inv = None
        self._pairs = None

    @property
    def p2_dir(self) -> Path:
        return self.dir / "phase2"

    @property
    def corpus(self) -> Corpus:
        if self._corpus is None:
            self._corpus = Corpus(self.run)
        return self._corpus

    def text(self, d: str) -> str | None:
        t = self.corpus.cached_doc(d)
        return t if t is not None else self.corpus.get_document(d)

    def tiers(self) -> dict:
        return self.cfg["phase2_tiers" if self.p2 else "tiers"]

    def phase_label(self) -> str:
        return f"phase2 round {self.p2}" if self.p2 else "phase1"

    # ------------------------------------------------------------------ inputs
    def final_records(self) -> list[dict]:
        p = self.run.final_file(self.sid)
        if not p.exists():
            raise SystemExit(f"no Stage 2 output for set {self.sid}: {p}")
        recs = read_jsonl(p)
        seen = set()
        for r in recs:
            r["record_id"] = str(r.get("record_id") or r.get("id") or "")
            if not r["record_id"] or r["record_id"] in seen or re.search(r"\s", r["record_id"]):
                raise SystemExit(f"{p}: record_id {r['record_id']!r} is empty, repeated or contains whitespace")
            seen.add(r["record_id"])
        return recs

    def stage1_record(self, src: str, qid: str) -> dict | None:
        if src not in self.run.config.sets or not self.run.input_file(src).exists():
            return None
        for r in self.run.records(src):
            if r["record_id"] == qid:
                return r
        return None

    def inventory(self) -> dict:
        if self._inv is None:
            if not self.inventory_path.exists():
                raise SystemExit(f"no inventory: run `qrels.py --set {self.sid} pool` first ({self.inventory_path})")
            self._inv = {r["id"]: r for r in read_jsonl(self.inventory_path)}
        return self._inv

    def row(self, qid: str) -> dict:
        inv = self.inventory()
        if qid not in inv:
            raise SystemExit(f"{qid}: not in {self.inventory_path}")
        return inv[qid]

    # ------------------------------------------------------------------ pool and candidates
    def recall_file(self, qid: str, kind: str) -> Path:
        return self.recall_dir / f"{qid}.{kind}.json"

    def full_pool(self, row: dict) -> dict[str, list[str]]:
        pool = {d: set(v) for d, v in row["pool"].items()}
        cf = self.recall_file(row["id"], "candidates")
        if cf.exists():
            for hid, h in json.loads(cf.read_text())["hops"].items():
                for c in h["candidates"]:
                    pool.setdefault(c["doc_id"], set()).add(f"recall:{hid}")
        return {d: sorted(v) for d, v in sorted(pool.items(), key=lambda kv: natural_key(kv[0]))}

    def pairs(self) -> list[dict]:
        """Phase 2: this round's duplicate pairs (dups.py lookup)."""
        if self._pairs is None:
            p = self.p2_dir / f"pairs_r{self.p2}.jsonl"
            if not p.exists():
                raise SystemExit(f"no {p}: run `dups.py --set {self.sid} lookup --round {self.p2}` first")
            self._pairs = read_jsonl(p)
        return self._pairs

    def near_pairs(self, qid: str) -> dict[str, dict]:
        return {x["doc_id"]: x for x in self.pairs() if x["id"] == qid and x["kind"] == "near" and x["status"] == "new"}

    def candidates(self, row: dict) -> list[str]:
        """Documents to judge, before dropping the ones the corpus does not have."""
        if self.p2:
            return list(self.near_pairs(row["id"]))
        return [d for d in self.full_pool(row) if d not in row["fixed"]]

    def missing_path(self, qid: str) -> Path:
        return self.work / qid / "missing.json"

    def missing(self, qid: str) -> set[str]:
        p = self.missing_path(qid)
        return set(json.loads(p.read_text())) if p.exists() else set()

    def to_judge(self, row: dict) -> list[str]:
        miss = self.missing(row["id"])
        return [d for d in self.candidates(row) if d not in miss]

    def seed_docs(self, row: dict) -> dict[str, list[str]]:
        """{doc: [live hop ids]} of the Stage 1 cited documents of live hops."""
        live = {h["id"] for h in row["hops"]}
        out = defaultdict(list)
        for hid, docs in row["seeds"].items():
            if hid in live:
                for d in docs:
                    out[d].append(hid)
        return out

    # ------------------------------------------------------------------ verdicts
    def first_verdicts(self, qid: str) -> dict[str, dict]:
        out = {}
        for p in sorted((self.judged / qid).glob("chunk_*.json")):
            for v in load_verdicts(p):
                out[v.get("doc_id")] = v
        return out

    def adjudicated(self, qid: str) -> dict[str, dict]:
        """Adjudicated verdicts of every round, in round order (a later round wins)."""
        out = {}

        def order(p):        # round 1 parts are adjudicated_<letters>, round N parts adjudicated_rN_<letters>
            m = re.fullmatch(r"adjudicated_r(\d+)_([a-z]+)", p.stem)
            return (int(m.group(1)), len(m.group(2)), m.group(2)) if m else (1, len(p.stem), p.stem)

        files = sorted((self.judged / qid).glob("adjudicated_*.json"), key=order)
        for p in files:
            out |= {v.get("doc_id"): v for v in load_verdicts(p)}
        return out

    def final_verdicts(self, qid: str) -> dict[str, dict]:
        return self.first_verdicts(qid) | self.adjudicated(qid)

    def rounds(self, qid: str) -> dict[int, dict]:
        """Second-judge rounds rendered for a question: {round: manifest}."""
        out = {}
        for p in (self.work / qid).glob("second*.json"):
            m = re.fullmatch(r"second(\d*)\.json", p.name)
            if m:
                out[int(m.group(1) or 1)] = json.loads(p.read_text())
        return dict(sorted(out.items()))


def load_verdicts(path: Path) -> list[dict]:
    d = json.loads(path.read_text())
    vs = d.get("verdicts") if isinstance(d, dict) else None
    if not isinstance(vs, list):
        raise ValueError(f"{path}: expected {{\"verdicts\": [...]}}")
    return vs


def round_tags(rnd: int) -> tuple[str, str]:
    """(second-judge file stem, adjudication part prefix) of a round: round 1 is 'second' / '', round N 'secondN' / 'rN_'."""
    return ("second" if rnd == 1 else f"second{rnd}"), ("" if rnd == 1 else f"r{rnd}_")


def partial_only(v: dict) -> bool:
    return bool(v.get("keep")) and all(h.get("supports") == "partial" for h in v.get("hops") or [])


# ----------------------------------------------------------------------------- rendering
def part_name(j: int) -> str:
    s = ""
    j += 1
    while j:
        j, r = divmod(j - 1, 26)
        s = "abcdefghijklmnopqrstuvwxyz"[r] + s
    return s


def render(doc_id: str, text: str, wrap: int, note: str | None = None) -> str:
    body = "\n".join(textwrap.fill(p, wrap, break_long_words=False, break_on_hyphens=False) if p.strip() else ""
                     for p in text.split("\n"))
    head = f"\n\n======== DOC {doc_id} ({len(text)} chars) ========\n"
    if note:
        head += note.rstrip() + "\n-------- document text --------\n"
    return head + body + f"\n======== END {doc_id} ========"


def clear(wd: Path, pattern: str) -> None:
    rx = re.compile(pattern)
    for f in wd.glob("*.md"):
        if rx.fullmatch(f.name):
            f.unlink()


def write_parts(q: QSet, wd: Path, stem: str, docs: list[str], title: str, notes: dict | None = None) -> dict[str, list[str]]:
    """Render docs into <stem>_a.md, <stem>_b.md, ... of at most part_chars each (a long document gets its own part).
    Returns {part file name: [docs]}."""
    wd.mkdir(parents=True, exist_ok=True)
    parts, cur, n = [], [], 0
    for d in docs:
        L = len(q.text(d) or "")
        if cur and n + L > q.cfg["part_chars"]:
            parts.append(cur)
            cur, n = [], 0
        cur.append(d)
        n += L
    if cur:
        parts.append(cur)
    names = [f"{stem}_{part_name(j)}.md" for j in range(len(parts))]
    out = {}
    for name, ps in zip(names, parts):
        head = (f"{title}\nPart {names.index(name) + 1} of {len(parts)} ({', '.join(names)}). "
                f"Documents in this part: {', '.join(ps)}")
        body = "".join(render(d, q.text(d) or "", q.cfg["wrap"], (notes or {}).get(d)) for d in ps)
        tmp = wd / f".{name}.tmp"
        tmp.write_text(head + body + "\n")
        tmp.replace(wd / name)
        out[name] = ps
    return out


def chunk_docs(q: QSet, docs: list[str]) -> list[list[str]]:
    chunks, cur, n = [], [], 0
    for d in docs:
        L = len(q.text(d) or "")
        if cur and (n + L > q.cfg["chunk_chars"] or len(cur) >= q.cfg["chunk_docs"]):
            chunks.append(cur)
            cur, n = [], 0
        cur.append(d)
        n += L
    if cur:
        chunks.append(cur)
    return chunks


def phase2_notes(q: QSet, row: dict, docs: list[str]) -> dict[str, str]:
    """For a near duplicate: its parent, the Jaccard, and the parent's support, so the judge can verify the same evidence."""
    if not q.p2:
        return {}
    pairs = q.near_pairs(row["id"])
    notes = {}
    for d in docs:
        x = pairs.get(d)
        if not x:
            continue
        text = q.text(d) or ""
        lines = [f"Near duplicate of {x['parent']} (5-gram Jaccard {x['jaccard']}), which is kept for:"]
        for hid, info in sorted((x.get("parent_hops") or {}).items(), key=lambda kv: natural_key(kv[0])):
            qs = info.get("quotes") or []
            if qs:
                for qt in qs[:2]:
                    lines.append(f"  hop {hid} ({info.get('supports')}): parent quote {json.dumps(qt, ensure_ascii=False)}"
                                 f"; verbatim in this document: {'yes' if is_verbatim(text, qt) else 'NO'}")
            else:
                lines.append(f"  hop {hid} ({info.get('supports')}): no parent quote on record")
        lines.append("Judge this document on its own text against ALL live hops: keep it only for hops its own text "
                     "supports, each with a quote copied from it.")
        notes[d] = "\n".join(lines)
    return notes


# ----------------------------------------------------------------------------- verdict checks
def check_verdicts(q: QSet, row: dict, docs: set[str], verdicts, where: str) -> list[str]:
    factual = {h["id"] for h in row["hops"] if not h["derivation"]}
    derived = {h["id"] for h in row["hops"] if h["derivation"]}
    out_ids = {h["id"] for h in row["out_hops"]}
    errs, got = [], {}
    if not isinstance(verdicts, list):
        return [f"{where}: verdicts must be a list"]
    for v in verdicts:
        if not isinstance(v, dict):
            errs.append(f"{where}: a verdict is not an object: {v!r:.80}")
            continue
        d = v.get("doc_id")
        if d in got:
            errs.append(f"{where}: {d} judged twice")
        got[d] = v
        if d not in docs:
            errs.append(f"{where}: {d} is not one of the documents to judge here")
            continue
        text = q.text(d) or ""
        if v.get("keep") is True:
            hs = v.get("hops")
            if not isinstance(hs, list) or not hs:
                errs.append(f"{where}: {d} kept with no hop")
                continue
            seen = set()
            for h in hs:
                hid = str(h.get("hop"))
                if hid in seen:
                    errs.append(f"{where}: {d} lists hop {hid} twice")
                seen.add(hid)
                if hid in derived:
                    errs.append(f"{where}: {d} cites derivation hop {hid}: derivation hops are not judged; cite the "
                                f"hops whose facts it uses")
                elif hid in out_ids:
                    errs.append(f"{where}: {d} cites out hop {hid}: drop it as out_hop_only unless it supports a live hop")
                elif hid not in factual:
                    errs.append(f"{where}: {d} cites hop {hid!r}, not a live hop ({sorted(factual, key=natural_key)})")
                if h.get("supports") not in SUPPORTS:
                    errs.append(f"{where}: {d} hop {hid}: supports must be one of {SUPPORTS}")
                qt = h.get("quote")
                if not isinstance(qt, str) or len(norm_ws(qt)) < q.cfg["min_quote_chars"]:
                    errs.append(f"{where}: {d} hop {hid}: quote missing or shorter than {q.cfg['min_quote_chars']} characters")
                elif not is_verbatim(text, qt):
                    errs.append(f"{where}: {d} hop {hid}: quote not verbatim in the document: {qt[:90]!r}")
        elif v.get("keep") is False:
            if v.get("reason") not in REASONS:
                errs.append(f"{where}: {d} dropped with reason {v.get('reason')!r}, not one of {REASONS}")
            if not (v.get("note") or "").strip():
                errs.append(f"{where}: {d} dropped with no note")
            if v.get("hops"):
                errs.append(f"{where}: {d} is dropped but lists hops; keep it or remove the hops")
        else:
            errs.append(f"{where}: {d} has no keep true/false")
    for d in sorted(docs - set(got), key=natural_key):
        errs.append(f"{where}: {d} has no verdict")
    return errs


def check_file(q: QSet, row: dict, docs, path: Path, where: str) -> list[str]:
    if not path.exists():
        return [f"{where}: no verdict file {path}"]
    try:
        vs = load_verdicts(path)
    except (ValueError, json.JSONDecodeError) as e:
        return [f"{where}: unreadable verdict file: {e}"]
    return check_verdicts(q, row, set(docs), vs, where)


def standard_errors(q: QSet, row: dict) -> list[str]:
    p = q.standards / f"{row['id']}.json"
    if not p.exists():
        return [f"no {p}"]
    try:
        hs = json.loads(p.read_text()).get("hops")
    except json.JSONDecodeError as e:
        return [f"{p}: invalid JSON: {e}"]
    if not isinstance(hs, dict):
        return [f"{p}: expected {{\"hops\": {{hop id: standard}}}}"]
    errs = []
    factual = [h["id"] for h in row["hops"] if not h["derivation"]]
    for hid in factual:
        s = hs.get(hid)
        if not isinstance(s, dict):
            errs.append(f"hop {hid}: no standard")
            continue
        for k, typ in (("subject", str), ("full", str), ("partial", list), ("reject", list)):
            if not isinstance(s.get(k), typ) or (typ is str and not s[k].strip()):
                errs.append(f"hop {hid}: {k!r} missing or not a {'non-empty string' if typ is str else 'list'}")
    for k in hs:
        if k not in factual:
            errs.append(f"hop {k}: not a live factual hop (derivation and out hops take no standard)")
    return errs


# ----------------------------------------------------------------------------- pool
def _collect_doc_ids(obj, out: set) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "doc_id" and isinstance(v, str) and v:
                out.add(v)
            elif isinstance(v, list) and (k in ("doc_ids", "supporting_doc_ids") or k.startswith("docs")):
                out |= {x for x in v if isinstance(x, str) and x}     # e.g. a verifier's "docs_read"
                _collect_doc_ids([x for x in v if not isinstance(x, str)], out)
            else:
                _collect_doc_ids(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_doc_ids(v, out)


def stage2_files(run: Run, src: str, qid: str) -> dict[str, list[Path]]:
    """The question's Stage 2 working files: audits, revisions, adversarial findings, final checks."""
    out = {}
    for label, d in (("audit", run.work_dir(src)), ("revision", run.root / "revisions" / src),
                     ("adversarial", run.root / "adversarial" / src), ("final_check", run.root / "final_check" / src)):
        if d.exists():
            fs = sorted(p for p in d.glob("*.json") if p.name == f"{qid}.json" or p.name.startswith(f"{qid}."))
            if fs:
                out[label] = fs
    return out


def inventory_row(q: QSet, rec: dict) -> dict:
    qid = rec["record_id"]
    src = rec.get("set") or q.sid
    cov = defaultdict(list)
    for c in rec.get("constraints") or []:
        for x in c.get("covered_by") or []:
            cov[str(x)].append(c.get("text", ""))
    hops, out_hops, fixed, pool = [], [], defaultdict(dict), defaultdict(set)
    for i, h in enumerate(all_hops(rec), 1):
        hid = hop_id(h, i)
        ev = [{"doc_id": e["doc_id"], "supports": e.get("supports") or "full", "quotes": list(e.get("quotes") or [])}
              for e in h.get("evidence") or [] if e.get("doc_id")]
        if is_out(h):
            out_hops.append({"id": hid, "clue": h.get("clue", ""), "why": h.get("note") or "dropped in revision"})
            for e in ev:
                pool[e["doc_id"]].add(f"stage2_out:{hid}")
            continue
        hops.append({"id": hid, "type": h.get("type") or "required", "role": h.get("role") or "inclusion",
                     "candidate": h.get("candidate"), "clue": h.get("clue", ""), "constraints": cov.get(hid, []),
                     "derivation": is_derivation(h), "inputs": [str(x) for x in h.get("inputs") or []],
                     "note": h.get("note", "") if is_derivation(h) else "", "evidence": ev})
        for e in ev:
            prev = fixed[e["doc_id"]].get(hid)
            fixed[e["doc_id"]][hid] = "full" if "full" in (prev, e["supports"]) else e["supports"]
            pool[e["doc_id"]].add(f"stage2:{hid}")
    live = {h["id"] for h in hops}
    seeds = {}
    s1 = q.stage1_record(src, qid)
    for i, h in enumerate(R.hops(s1) if s1 else [], 1):
        docs = R.hop_doc_ids(h)
        if docs:
            seeds[str(i)] = docs
            for d in docs:
                pool[d].add(f"seed:{i}" if str(i) in live else f"seed_out:{i}")
    for d in (rec.get("completeness") or {}).get("doc_ids") or []:     # list documents behind a set's completeness
        pool[d].add("completeness")
    top = q.cfg["searchlog_top"]
    for line in read_jsonl(q.run.searchlog(src, qid)):
        for d in (line.get("results") or [])[:top]:
            d = d.get("docid") if isinstance(d, dict) else d
            if d:
                pool[d].add("search")
    for label, files in stage2_files(q.run, src, qid).items():
        ids = set()
        for f in files:
            try:
                _collect_doc_ids(json.loads(f.read_text()), ids)
            except json.JSONDecodeError:
                print(f"warning: {f} is not valid JSON; skipped", file=sys.stderr)
        for d in ids:
            pool[d].add(label)
    factual = [h["id"] for h in hops if not h["derivation"]]
    mode = q.cfg["recall"]
    needs = factual if mode == "all" else [h for h in factual if not seeds.get(h)] if mode == "no_seed" else []
    return {"id": qid, "set": q.sid, "source_set": src, "has_stage1": s1 is not None,
            "answer_type": R.answer_type(rec, q.run.config.set(q.sid)["answer_type"]),
            "question": R.question_text(rec), "answer": R.gold_answer(rec),
            "hops": hops, "out_hops": out_hops, "candidates": rec.get("candidates") or [], "needs_recall": needs,
            "fixed": {d: dict(sorted(v.items())) for d, v in sorted(fixed.items(), key=lambda kv: natural_key(kv[0]))},
            "seeds": seeds,
            "pool": {d: sorted(v) for d, v in sorted(pool.items(), key=lambda kv: natural_key(kv[0]))}}


def cmd_pool(q: QSet, a) -> None:
    recs = q.final_records()
    if a.qids:
        unknown = set(a.qids) - {r["record_id"] for r in recs}
        if unknown:
            raise SystemExit(f"not in {q.run.final_file(q.sid)}: {sorted(unknown)}")
        recs = [r for r in recs if r["record_id"] in set(a.qids)]
    rows = [inventory_row(q, r) for r in recs]
    if a.qids and q.inventory_path.exists():
        upd = {r["id"]: r for r in rows}
        rows = [upd.pop(r["id"], r) for r in read_jsonl(q.inventory_path)] + list(upd.values())
    write_jsonl(q.inventory_path, rows)
    q._inv = None
    built = [r for r in rows if not a.qids or r["id"] in set(a.qids)]
    prov = Counter(p.split(":")[0] for r in built for v in r["pool"].values() for p in v)
    n_pool = sorted(len(r["pool"]) for r in built) or [0]
    print(json.dumps({"set": q.sid, "questions": len(built), "inventory": str(q.inventory_path),
                      "live_hops": sum(len(r["hops"]) for r in built),
                      "derivation_hops": sum(h["derivation"] for r in built for h in r["hops"]),
                      "out_hops": sum(len(r["out_hops"]) for r in built),
                      "hops_needing_recall": sum(len(r["needs_recall"]) for r in built),
                      "without_stage1_record": [r["id"] for r in built if not r["has_stage1"]],
                      "pool_pairs": sum(n_pool), "pool_median": n_pool[len(n_pool) // 2],
                      "fixed_pairs": sum(len(r["fixed"]) for r in built), "provenance": dict(prov)}))


# ----------------------------------------------------------------------------- show
def cmd_show(q: QSet, a) -> None:
    r = q.row(a.qid)
    ans = " | ".join(map(str, r["answer"])) if isinstance(r["answer"], list) else r["answer"]
    print(f"# {r['id']} (set {r['set']}, {r['answer_type']} answer){'  [' + q.phase_label() + ']' if q.p2 else ''}")
    print(f"Question: {r['question']}\nAnswer: {ans}")
    if q.cfg["judge_note"]:
        print(f"Dataset rule: {q.cfg['judge_note']}")
    print()
    for h in r["hops"]:
        tags = [h["type"], h["role"]] + ([f"candidate: {h['candidate']}"] if h.get("candidate") else [])
        flag = "  (needs recall)" if h["id"] in r["needs_recall"] and not q.p2 else ""
        print(f"## hop {h['id']} [{', '.join(tags)}]{flag}\n  clue: {h['clue']}")
        if h["derivation"]:
            print(f"  derivation: not judged; its documents are those of its inputs {h['inputs'] or '(not listed)'}. "
                  f"{h['note']}")
        for c in h["constraints"]:
            print(f"  constraint: {c}")
        for e in h["evidence"][:3]:
            print(f"  accepted evidence {e['doc_id']} ({e['supports']}): {norm_ws(' … '.join(e['quotes']))[:400]}")
    for h in r["out_hops"]:
        print(f"## OUT hop {h['id']} ({h['why']}): {h['clue']}")
    others = [c for c in r["candidates"] if c.get("role") != "answer"]
    if others:
        print("\nCandidates the audit considered:")
        for c in others:
            print(f"  {c.get('name')} ({c.get('role')}, {c.get('status')}; hops {c.get('hops') or []})")
    if q.p2:
        print(f"\n{len(q.near_pairs(r['id']))} near duplicates to judge in {q.phase_label()}")
    else:
        pool = q.full_pool(r)
        prov = Counter(p.split(":")[0] for v in pool.values() for p in v)
        print(f"\npool {len(pool)} documents (provenance {dict(prov)}); accepted Stage 2 evidence {len(r['fixed'])} "
              f"(fixed, not judged); to judge {len(q.candidates(r))}")


def cmd_std_check(q: QSet, a) -> None:
    errs = standard_errors(q, q.row(a.qid))
    for e in errs:
        print(e)
    print(f"{a.qid} standard: {len(errs)} errors")
    sys.exit(1 if errs else 0)


# ----------------------------------------------------------------------------- recall
def _search_key(query: str, hits: int) -> str:
    # the cache key cmass.corpus.Corpus.search uses; only to tell whether a search is already cached
    return hashlib.sha1(f"{query}|{hits}".encode()).hexdigest()[:16]


def _has_anchor(text: str, anchors: list[str]) -> bool:
    if not anchors:
        return True
    t = norm_ws(text).lower()
    return any(norm_ws(x).lower() in t for x in anchors)


def cmd_recall(q: QSet, a) -> None:
    r = q.row(a.qid)
    qf = q.recall_file(a.qid, "queries")
    if not qf.exists():
        raise SystemExit(f"no {qf}: write the query families first (QRELS.md, step 2)")
    spec = json.loads(qf.read_text()).get("hops") or {}
    missing = [h for h in r["needs_recall"] if h not in spec]
    if missing:
        raise SystemExit(f"{qf}: no query family for hops {missing}")
    factual = {h["id"] for h in r["hops"] if not h["derivation"]}
    k, hits = a.k or q.cfg["recall_k"], q.cfg["recall_hits"]
    fams = {}
    for hid, s in spec.items():
        if hid not in factual:
            raise SystemExit(f"{qf}: hop {hid} is not a live factual hop of {a.qid}")
        qs = list(dict.fromkeys(norm_ws(x) for x in s.get("queries") or [] if norm_ws(x)))
        if len(qs) < q.cfg["recall_min_queries"]:
            raise SystemExit(f"{qf}: hop {hid} needs at least {q.cfg['recall_min_queries']} distinct queries")
        fams[hid] = (qs, [x for x in s.get("anchors") or [] if norm_ws(x)])
    # one question's recall at a time, host-wide: the corpus service is paced, so interleaving many questions' searches
    # makes every recall slow and the waiting agents time out; queued, each finishes quickly
    uncached = [x for qs, _ in fams.values() for x in qs if not (q.run.searches / f"{_search_key(x, hits)}.json").exists()]
    lock = None
    if uncached:
        lock = open(q.corpus.lock_dir / "qrels_recall.lock", "w")
        print(f"{a.qid}: {len(uncached)} searches to run; waiting for the recall queue", flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(f"{a.qid}: recall started", flush=True)
    try:
        out = {}
        for hid, (qs, anchors) in fams.items():
            fused, lists = defaultdict(float), defaultdict(int)
            for x in qs:
                for c in q.corpus.search(x, hits):
                    fused[c["docid"]] += 1.0 / (q.cfg["rrf_k"] + int(c["rank"]))
                    lists[c["docid"]] += 1
            ranked = sorted(fused, key=lambda d: (-fused[d], natural_key(d)))
            kept, filtered, checked = [], 0, 0
            for d in ranked:                     # fetch lazily: a CLI backend returns previews only
                if len(kept) >= 3 * k:
                    break
                checked += 1
                if _has_anchor(q.text(d) or "", anchors):
                    kept.append(d)
                else:
                    filtered += 1
            row = lambda d: {"doc_id": d, "rrf": round(fused[d], 5), "lists": lists[d]}  # noqa: E731
            out[hid] = {"queries": qs, "anchors": anchors, "fused": len(ranked), "anchor_checked": checked,
                        "anchor_filtered": filtered, "candidates": [row(d) for d in kept[:k]],
                        "next": [row(d) for d in kept[k:]]}
            print(f"hop {hid}: {len(qs)} queries, {len(ranked)} fused, {filtered} of {checked} checked without an "
                  f"anchor, {min(k, len(kept))} candidates")
    finally:
        if lock:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
    cf = q.recall_file(a.qid, "candidates")
    write_json(cf, {"qid": a.qid, "k": k, "hits": hits, "hops": out})
    print(f"wrote {cf}: {len({c['doc_id'] for h in out.values() for c in h['candidates']})} unique candidates")


# ----------------------------------------------------------------------------- fetch
def fetch_list(q: QSet, row: dict) -> list[str]:
    if q.p2:
        pairs = q.near_pairs(row["id"])
        exact = [x for x in q.pairs() if x["id"] == row["id"] and x["kind"] == "exact" and x["status"] == "new"]
        return list(dict.fromkeys(list(pairs) + [x["parent"] for x in pairs.values()]
                                  + [d for x in exact for d in (x["doc_id"], x["parent"])]))
    return list(q.full_pool(row))


def cmd_fetch(q: QSet, a) -> None:
    """Cache every document of the pool, question by question (smallest remainder first), printing "ready QID" as each
    question's pool is complete, so judging can start before the whole fetch ends."""
    inv = q.inventory()
    qids = a.qids or ([x["id"] for x in q.pairs()] if q.p2 else list(inv))
    qids = list(dict.fromkeys(qids))
    todo = {x: [d for d in fetch_list(q, inv[x]) if q.corpus.cached_doc(d) is None] for x in qids}
    print(f"{sum(len(v) for v in todo.values())} documents to fetch for {len(qids)} questions", flush=True)
    not_found = []
    for x in sorted(qids, key=lambda x: len(todo[x])):
        for d in todo[x]:
            if q.corpus.cached_doc(d) is None and q.corpus.get_document(d) is None:
                not_found.append(f"{x}/{d}")
        print(f"ready {x}", flush=True)
    print(json.dumps({"fetched_for": len(qids), "not_found": not_found}))


# ----------------------------------------------------------------------------- chunks
def cmd_chunks(q: QSet, a) -> dict:
    """Incremental: documents already in a chunk keep it (and its verdicts); new ones get new chunks."""
    r = q.row(a.qid)
    wd = q.work / a.qid
    wd.mkdir(parents=True, exist_ok=True)
    cj = wd / "chunks.json"
    old = json.loads(cj.read_text()) if cj.exists() else {}
    done = {d for ch in old.values() for d in ch}
    miss = q.missing(a.qid)
    docs, new_missing = [], []
    for d in q.candidates(r):
        if d in done or d in miss:
            continue
        if q.text(d) is None:
            new_missing.append(d)
        else:
            docs.append(d)
    if new_missing:
        write_json(q.missing_path(a.qid), sorted(miss | set(new_missing), key=natural_key))
        print(f"warning: {len(new_missing)} documents not in the corpus, not judged: {new_missing[:5]}", file=sys.stderr)
    new = {f"{i:02d}": ch for i, ch in enumerate(chunk_docs(q, docs), len(old) + 1)}
    p2 = (" These are near duplicates of kept documents (duplicate expansion): judge each on its own text; similarity "
          "to its parent proves nothing.") if q.p2 else ""
    for c, ch in new.items():
        clear(wd, rf"chunk_{c}_[a-z]+\.md")
        write_parts(q, wd, f"chunk_{c}", ch, f"# {a.qid} chunk {c}: {len(ch)} documents to judge.{p2} Verdicts to "
                                             f"{q.judged / a.qid / f'chunk_{c}.json'} (QRELS.md, step 3).",
                    phase2_notes(q, r, ch))
    write_json(cj, old | new)
    res = {"qid": a.qid, "to_judge": len(docs), "already_chunked": len(done), "fixed": 0 if q.p2 else len(r["fixed"]),
           "not_in_corpus": len(miss) + len(new_missing), "n_chunks": len(new), "chunks": list(new),
           "all_chunks": list(old | new)}
    print(json.dumps(res))
    return res


def cmd_unjudged(q: QSet, a) -> None:
    r = q.row(a.qid)
    cj = q.work / a.qid / "chunks.json"
    chunks = json.loads(cj.read_text()) if cj.exists() else {}
    bad = [c for c, docs in chunks.items()
           if check_file(q, r, docs, q.judged / a.qid / f"chunk_{c}.json", f"chunk {c}")]
    print(json.dumps({"qid": a.qid, "chunks": bad, "all_chunks": list(chunks)}))


def cmd_check(q: QSet, a) -> None:
    r = q.row(a.qid)
    cj = q.work / a.qid / "chunks.json"
    if not cj.exists():
        raise SystemExit(f"no {cj}: run chunks first")
    chunks = json.loads(cj.read_text())
    errs, n = [], Counter()
    for c in a.chunks or list(chunks):
        if c not in chunks:
            errs.append(f"chunk {c}: not a chunk of {a.qid} ({list(chunks)})")
            continue
        p = q.judged / a.qid / f"chunk_{c}.json"
        e = check_file(q, r, chunks[c], p, f"chunk {c}")
        errs += e
        if p.exists() and not any("unreadable" in x for x in e):
            n.update("keep" if v.get("keep") else "drop" for v in load_verdicts(p))
    for e in errs:
        print(e)
    print(f"{a.qid}: {len(errs)} errors; {dict(n)}")
    sys.exit(1 if errs else 0)


# ----------------------------------------------------------------------------- second judge, compare, adjudicate
def cmd_second(q: QSet, a) -> None:
    """Render round N of the blind second judge. A policy round samples, from the judged documents no earlier round
    considered: every partial-only keep, keep_rate of the full keeps (at least min_keeps), drop_rate of the drops (at
    least min_drops; 1.0 = all) and, with all_seed_drops, every dropped Stage 1 cited document. A --drops-only round
    (the escalation) takes every drop no earlier round re-judged."""
    r = q.row(a.qid)
    first = q.first_verdicts(a.qid)
    docs = q.to_judge(r)
    missing = [d for d in docs if d not in first]
    if missing:
        raise SystemExit(f"{a.qid}: {len(missing)} documents not judged yet (first: {missing[0]})")
    rounds = q.rounds(a.qid)
    if a.round in rounds:
        raise SystemExit(f"{a.qid}: round {a.round} is already rendered ({q.work / a.qid}); start round "
                         f"{max(rounds) + 1} instead")
    if a.round != (max(rounds) + 1 if rounds else 1):
        raise SystemExit(f"{a.qid}: the next round is {max(rounds) + 1 if rounds else 1}")
    t = q.tiers()
    considered = {d for m in rounds.values() for d in m["population"]}
    second_judged = {d for m in rounds.values() for ch in m["chunks"].values() for d in ch}
    order = sorted(docs, key=lambda d: sha(f"second|{q.sid}|{a.qid}|{d}"))
    seed = q.seed_docs(r)
    if a.drops_only:
        population = [d for d in order if not first[d].get("keep") and d not in second_judged]
        sample = list(population)
        info = {"drops": len(population)}
    else:
        population = [d for d in order if d not in considered]
        drops = [d for d in population if not first[d].get("keep")]
        pkeeps = [d for d in population if partial_only(first[d])]
        fkeeps = [d for d in population if first[d].get("keep") and not partial_only(first[d])]
        nk = min(len(fkeeps), max(t["min_keeps"], round(t["keep_rate"] * len(fkeeps))))
        nd = len(drops) if t["drop_rate"] >= 1 else min(len(drops), max(t["min_drops"], round(t["drop_rate"] * len(drops))))
        seed_drops = [d for d in drops[nd:] if d in seed] if t["all_seed_drops"] else []
        sample = list(dict.fromkeys(drops[:nd] + seed_drops + pkeeps + fkeeps[:nk]))
        info = {"drops": len(drops), "drops_sampled": nd, "seed_drops_added": len(seed_drops),
                "partial_keeps": len(pkeeps), "full_keeps": len(fkeeps), "full_keeps_sampled": nk}
    sample = sorted(sample, key=lambda d: sha(f"second2|{q.sid}|{a.qid}|{d}"))
    stem, _ = round_tags(a.round)
    wd = q.work / a.qid
    clear(wd, rf"{stem}_\d+_[a-z]+\.md")
    chunks = {f"{i:02d}": ch for i, ch in enumerate(chunk_docs(q, sample), 1)}
    for c, ch in chunks.items():
        write_parts(q, wd, f"{stem}_{c}", ch, f"# {a.qid} second judge, round {a.round}, set {c}: {len(ch)} documents. "
                                              f"Verdicts to {q.judged / a.qid / f'{stem}_{c}.json'} (QRELS.md, step 3).",
                    phase2_notes(q, r, ch))
    write_json(wd / f"{stem}.json", {"round": a.round, "drops_only": bool(a.drops_only), "population": population,
                                     "chunks": chunks})
    print(json.dumps({"qid": a.qid, "round": a.round, "drops_only": bool(a.drops_only), "judged": len(docs),
                      "population": len(population), **info, "to_second_judge": len(sample), "chunks": list(chunks)}))


def _round(q: QSet, qid: str, rnd: int) -> dict:
    m = q.rounds(qid).get(rnd)
    if m is None:
        raise SystemExit(f"{qid}: round {rnd} is not rendered; run `second {qid} --round {rnd}`")
    return m


def cmd_second_check(q: QSet, a) -> None:
    r = q.row(a.qid)
    m = _round(q, a.qid, a.round)
    stem, _ = round_tags(a.round)
    if a.chunk not in m["chunks"]:
        raise SystemExit(f"{a.qid}: round {a.round} has no set {a.chunk} ({list(m['chunks'])})")
    errs = check_file(q, r, m["chunks"][a.chunk], q.judged / a.qid / f"{stem}_{a.chunk}.json", f"{stem} {a.chunk}")
    for e in errs:
        print(e)
    print(f"{a.qid} {stem} {a.chunk}: {len(errs)} errors")
    sys.exit(1 if errs else 0)


def cmd_compare(q: QSet, a) -> None:
    first = q.first_verdicts(a.qid)
    m = _round(q, a.qid, a.round)
    stem, pre = round_tags(a.round)
    second = {}
    for c in m["chunks"]:
        p = q.judged / a.qid / f"{stem}_{c}.json"
        if not p.exists():
            raise SystemExit(f"{a.qid}: no {p}")
        second |= {v["doc_id"]: v for v in load_verdicts(p)}
    rows = []
    for d, v2 in second.items():
        v1 = first[d]
        rows.append({"doc_id": d, "first": "keep" if v1.get("keep") else "drop",
                     "second": "keep" if v2.get("keep") else "drop", "agree": bool(v1.get("keep")) == bool(v2.get("keep")),
                     "first_partial_only": partial_only(v1),
                     "first_hops": sorted((str(h.get("hop")) for h in v1.get("hops") or []), key=natural_key),
                     "second_hops": sorted((str(h.get("hop")) for h in v2.get("hops") or []), key=natural_key)})
    dis = [x["doc_id"] for x in rows if not x["agree"]]
    wd = q.work / a.qid
    clear(wd, rf"adj_{pre}[a-z]+\.md")
    items = []
    for d in dis:
        pair = [first[d], second[d]]
        if int(sha(f"ab|{q.sid}|{a.qid}|{d}"), 16) % 2:
            pair.reverse()
        items.append({"doc_id": d, "A": pair[0], "B": pair[1]})
    adj_file = f"adj_verdicts{'' if a.round == 1 else f'_r{a.round}'}.json"
    parts = {}
    if dis:
        written = write_parts(q, wd, f"adj_{pre}".rstrip("_"), dis,
                              f"# {a.qid} adjudication, round {a.round}: documents two judges disagree on. The two "
                              f"verdicts are in {wd / adj_file}.", phase2_notes(q, q.row(a.qid), dis))
        parts = {Path(n).stem[len("adj_"):]: ds for n, ds in written.items()}
    write_json(wd / adj_file, items)
    res = {"qid": a.qid, "round": a.round, "second_judged": len(rows), "disagree": len(dis),
           "first_drop_second_keep": sum(1 for x in rows if not x["agree"] and x["first"] == "drop"),
           "first_keep_second_drop": sum(1 for x in rows if not x["agree"] and x["first"] == "keep"),
           "hop_differences": sum(1 for x in rows if x["agree"] and x["first_hops"] != x["second_hops"]),
           "disagreements": dis, "adj_parts": list(parts), "adj_verdicts": adj_file}
    write_json(q.judged / a.qid / f"{stem}_compare.json", res | {"parts": parts, "rows": rows})
    print(json.dumps(res))


def adj_parts(q: QSet, qid: str) -> dict[str, list[str]]:
    """{part: [docs]} of every compared round."""
    out = {}
    for rnd in q.rounds(qid):
        p = q.judged / qid / f"{round_tags(rnd)[0]}_compare.json"
        if p.exists():
            out |= json.loads(p.read_text()).get("parts") or {}
    return out


def cmd_adj_check(q: QSet, a) -> None:
    r = q.row(a.qid)
    parts = adj_parts(q, a.qid)
    if a.part not in parts:
        raise SystemExit(f"{a.qid}: no adjudication part {a.part!r} ({sorted(parts)})")
    errs = check_file(q, r, parts[a.part], q.judged / a.qid / f"adjudicated_{a.part}.json", f"adjudicated {a.part}")
    for e in errs:
        print(e)
    print(f"{a.qid} adjudicated {a.part}: {len(errs)} errors")
    sys.exit(1 if errs else 0)


# ----------------------------------------------------------------------------- build
def evidence_quotes(row: dict, hid: str, d: str) -> list[str]:
    for h in row["hops"]:
        if h["id"] == hid:
            return [x for e in h["evidence"] if e["doc_id"] == d for x in e["quotes"]]
    return []


def hop_rows(row: dict, per_hop: dict) -> list[dict]:
    return [{"id": h["id"], "type": h["type"], "role": h["role"], "candidate": h.get("candidate"), "clue": h["clue"],
             "derivation": h["derivation"], "inputs": h["inputs"],
             "docs": dict(sorted(per_hop.get(h["id"], {}).items(), key=lambda kv: natural_key(kv[0])))}
            for h in row["hops"]]


def write_outputs(q: QSet, out: Path, qrels: list[dict], hops: list[dict], rejections: list[dict], stats: dict,
                  order: list[str]) -> None:
    """Merge rebuilt questions into the existing output files and write the TREC file from the result."""
    built = {r["id"] for r in qrels}
    prev_q = {r["id"]: r for r in read_jsonl(out / "qrels.jsonl")}
    prev_h = {r["id"]: r for r in read_jsonl(out / "qrels_hops.jsonl")}
    prev_r = [r for r in read_jsonl(out / "rejections.jsonl") if r["id"] not in built]
    prev_s = json.loads((out / "stats.json").read_text()).get("questions", {}) if (out / "stats.json").exists() else {}
    prev_q |= {r["id"]: r for r in qrels}
    prev_h |= {r["id"]: r for r in hops}
    prev_s |= stats
    pos = {x: i for i, x in enumerate(order)}
    ids = sorted(prev_q, key=lambda x: (pos.get(x, len(pos)), natural_key(x)))
    write_jsonl(out / "qrels.jsonl", [prev_q[x] for x in ids])
    write_jsonl(out / "qrels_hops.jsonl", [prev_h[x] for x in ids if x in prev_h])
    write_jsonl(out / "rejections.jsonl", sorted(prev_r + rejections, key=lambda r: (pos.get(r["id"], len(pos)), natural_key(r["doc_id"]))))
    lines = []
    for x in ids:
        hm = {h["id"]: h["docs"] for h in prev_h.get(x, {}).get("hops", [])}
        for d in prev_q[x]["qrels"]:
            rel = 1
            if q.cfg["trec_graded"] and any(v.get(d, {}).get("supports") == "full" for v in hm.values()):
                rel = 2
            lines.append(f"{x} 0 {d} {rel}\n")
    tmp = out / ".qrels.trec.tmp"
    tmp.write_text("".join(lines))
    tmp.replace(out / "qrels.trec")
    tot = Counter()
    for s in prev_s.values():
        for k, v in s.items():
            if isinstance(v, int) and not isinstance(v, bool):
                tot[k] += v
    write_json(out / "stats.json", {"set": q.sid, "questions_built": len(ids), "totals": dict(tot),
                                    "uncovered_hops": {x: s["uncovered_hops"] for x, s in prev_s.items() if s.get("uncovered_hops")},
                                    "questions": {x: prev_s[x] for x in ids if x in prev_s}})


def cmd_build(q: QSet, a) -> None:
    import qrels_state  # noqa: E402  (imports this module; loaded lazily to avoid a cycle)

    inv = q.inventory()
    qids = a.qids or list(inv)
    incomplete = {x: s["blocking"] for x in qids if not (s := qrels_state.state(q, x))["complete"]}
    if incomplete and not a.skip_incomplete:
        for x, why in incomplete.items():
            print(f"{x}: judging incomplete: {why}")
        raise SystemExit(f"{len(incomplete)} questions incomplete; finish them or pass --skip-incomplete")
    qids = [x for x in qids if x not in incomplete]
    qrels, hops, rej, stats = [], [], [], {}
    for x in qids:
        r = inv[x]
        pool = q.full_pool(r)
        first, adj = q.first_verdicts(x), q.adjudicated(x)
        second_seen = {d for m in q.rounds(x).values() for ch in m["chunks"].values() for d in ch}
        seed = q.seed_docs(r)
        per_hop = defaultdict(dict)
        for d, hs in r["fixed"].items():
            for hid, sup in hs.items():
                per_hop[hid][d] = {"supports": sup, "source": "stage2", "quotes": evidence_quotes(r, hid, d)}
        kept, dropped, src = set(r["fixed"]), {}, Counter()
        for d in q.to_judge(r):
            v = adj.get(d) or first.get(d)
            if v is None:
                raise SystemExit(f"{x}: {d} not judged")
            if v.get("keep"):
                kept.add(d)
                src["adjudicated" if d in adj else "judged"] += 1
                for h in v["hops"]:
                    per_hop[str(h["hop"])].setdefault(d, {"supports": h["supports"],
                                                          "source": "adjudicated" if d in adj else "judged",
                                                          "quotes": [h["quote"]]})
            elif q.cfg["keep_seed_docs"] and d in seed:
                kept.add(d)
                src["seed"] += 1
                for hid in seed[d]:
                    per_hop[hid].setdefault(d, {"supports": "partial", "source": "seed", "quotes": []})
            else:
                dropped[d] = v
                rej.append({"id": x, "doc_id": d, "reason": v.get("reason"), "note": v.get("note"),
                            "provenance": pool.get(d, []), "adjudicated": d in adj, "second_judged": d in second_seen})
        uncovered = [h["id"] for h in r["hops"] if not h["derivation"] and not per_hop.get(h["id"])]
        rh = {}
        cf = q.recall_file(x, "candidates")
        if cf.exists():        # how deep the kept documents reach in each recall list: a kept tail says recall was short
            for hid, h in json.loads(cf.read_text())["hops"].items():
                ids = [c["doc_id"] for c in h["candidates"]]
                tail = ids[-max(1, len(ids) // 4):] if ids else []
                rh[hid] = {"candidates": len(ids), "kept": sum(d in kept for d in ids),
                           "kept_for_this_hop": sum(d in per_hop.get(hid, {}) for d in ids),
                           "tail_kept_for_this_hop": sum(d in per_hop.get(hid, {}) for d in tail), "tail": len(tail)}
        qrels.append({"id": x, "set": q.sid, "question": r["question"], "answer": r["answer"],
                      "answer_type": r["answer_type"], "qrels": sorted(kept, key=natural_key)})
        hops.append({"id": x, "set": q.sid, "hops": hop_rows(r, per_hop)})
        prov_kept = Counter(p.split(":")[0] for d in kept for p in pool.get(d, []))
        stats[x] = {"pool": len(pool), "fixed": len(r["fixed"]), "judged": len(q.to_judge(r)),
                    "not_in_corpus": len(q.missing(x)), "kept": len(kept), "dropped": len(dropped),
                    "kept_by_source": {"stage2": len(r["fixed"]), **src}, "kept_by_provenance": dict(prov_kept),
                    "drop_reasons": dict(Counter(v.get("reason") for v in dropped.values())),
                    "second_judged": len(second_seen), "adjudicated": len(adj),
                    "derivation_hops": [h["id"] for h in r["hops"] if h["derivation"]],
                    "uncovered_hops": uncovered, "recall_hops": rh}
    out = q.dir / "phase1"
    write_outputs(q, out, qrels, hops, rej, stats, list(inv))
    for x, s in stats.items():
        print(f"{x}: pool {s['pool']} -> kept {s['kept']} (stage2 {s['fixed']}, judged {s['kept'] - s['fixed']}); "
              f"dropped {s['dropped']} {s['drop_reasons']}{'  UNCOVERED ' + str(s['uncovered_hops']) if s['uncovered_hops'] else ''}")
    print(json.dumps({"set": q.sid, "built": len(qids), "skipped_incomplete": sorted(incomplete), "out": str(out)}))


# ----------------------------------------------------------------------------- verify
def read_trec(path: Path) -> dict[str, set[str]]:
    out = defaultdict(set)
    if path.exists():
        for line in path.read_text().splitlines():
            p = line.split()
            if len(p) == 4:
                out[p[0]].add(p[2])
    return out


def cmd_verify(q: QSet, a) -> None:
    """Read independently of the inventory, from the Stage 2 records and the output files: every question has qrels;
    every accepted Stage 2 evidence document of a live hop is kept for that hop; every live factual hop keeps a document
    (derivation hops are exempt, and their listed inputs must be live and grounded); every kept document supports a hop;
    every recorded quote is verbatim in its document; exact duplicates are identical to their parents; qrels.trec matches."""
    out = q.dir / a.phase
    if not (out / "qrels.jsonl").exists():
        raise SystemExit(f"no {out / 'qrels.jsonl'}: build first")
    final = q.final_records()
    qrels = {r["id"]: set(r["qrels"]) for r in read_jsonl(out / "qrels.jsonl")}
    hmaps = {r["id"]: {h["id"]: h for h in r["hops"]} for r in read_jsonl(out / "qrels_hops.jsonl")}
    trec = read_trec(out / "qrels.trec")
    keep_seeds = q.cfg["keep_seed_docs"]
    bad, n_ev, n_quotes, n_deriv = [], 0, 0, 0
    for rec in final:
        x = rec["record_id"]
        if x not in qrels:
            bad.append(f"{x}: no qrels")
            continue
        kept, hm = qrels[x], hmaps.get(x, {})
        hops = [(hop_id(h, i), h) for i, h in enumerate(all_hops(rec), 1)]
        live = {hid: h for hid, h in hops if not is_out(h)}
        union = set()
        for hid, h in live.items():
            docs = (hm.get(hid) or {}).get("docs") or {}
            union |= set(docs)
            for e in h.get("evidence") or []:
                n_ev += 1
                d = e.get("doc_id")
                if d not in kept:
                    bad.append(f"{x}: hop {hid} accepted evidence {d} not in the qrels")
                elif d not in docs:
                    bad.append(f"{x}: hop {hid} accepted evidence {d} not in the hop's documents")
            if is_derivation(h):
                n_deriv += 1
                for inp in h.get("inputs") or []:
                    inp = str(inp)
                    if inp not in live:
                        bad.append(f"{x}: derivation hop {hid} input {inp} is not a live hop")
                    elif not (hm.get(inp) or {}).get("docs") and not is_derivation(live[inp]):
                        bad.append(f"{x}: derivation hop {hid} input {inp} has no kept document")
            elif not docs:
                bad.append(f"{x}: live hop {hid} has no kept document")
            for d, info in docs.items():
                if d not in kept:
                    bad.append(f"{x}: hop {hid} names {d}, which is not in the qrels")
                text = q.text(d)
                if text is None:
                    bad.append(f"{x}: hop {hid} document {d} not found in the corpus")
                    continue
                for qt in info.get("quotes") or []:
                    n_quotes += 1
                    if not is_verbatim(text, qt):
                        bad.append(f"{x}: hop {hid} {d}: quote not verbatim: {qt[:80]!r}")
                if info.get("source") in ("stage2", "judged", "adjudicated", "near_dup") and not info.get("quotes"):
                    bad.append(f"{x}: hop {hid} {d} ({info.get('source')}) has no quote")
                if info.get("source") == "exact_dup":
                    pt = q.text(info.get("parent") or "")
                    if pt is None or pt.strip() != text.strip():
                        bad.append(f"{x}: hop {hid} {d} is recorded as an exact duplicate of {info.get('parent')} "
                                   f"but the texts differ")
        for hid in hm:
            if hid not in live:
                bad.append(f"{x}: hop map {hid} is not a live hop")
        for d in sorted(kept - union, key=natural_key):
            bad.append(f"{x}: {d} is in the qrels but supports no hop")
        if trec.get(x, set()) != kept:
            bad.append(f"{x}: qrels.trec differs from qrels.jsonl")
        if keep_seeds:
            s1 = q.stage1_record(rec.get("set") or q.sid, x)
            for i, h in enumerate(R.hops(s1) if s1 else [], 1):
                if str(i) in live:
                    for d in R.hop_doc_ids(h):
                        if d not in kept and q.text(d) is not None:
                            bad.append(f"{x}: Stage 1 cited document {d} (hop {i}) not in the qrels (keep_seed_docs)")
    extra = set(qrels) - {r["record_id"] for r in final}
    for x in sorted(extra, key=natural_key):
        bad.append(f"{x}: has qrels but is not in {q.run.final_file(q.sid).name}")
    for b in bad:
        print(b)
    print(json.dumps({"set": q.sid, "phase": a.phase, "questions": len(final), "with_qrels": len(set(qrels) - extra),
                      "pairs": sum(len(v) for v in qrels.values()), "evidence_citations": n_ev, "quotes_checked": n_quotes,
                      "derivation_hops_exempt": n_deriv, "problems": len(bad)}))
    sys.exit(1 if bad else 0)


# ----------------------------------------------------------------------------- main
def default_set(run: Run) -> str:
    sets = sorted(p.name[:-len("_final.jsonl")] for p in (run.root / "final").glob("*_final.jsonl"))
    if len(sets) != 1:
        raise SystemExit(f"pass --set (Stage 2 outputs found for {sets or 'no set'})")
    return sets[0]


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="run directory (default: $CMASS_RUN or the current directory)")
    ap.add_argument("--set", dest="sid", help="the set whose final/<set>_final.jsonl is built")
    ap.add_argument("--phase2", type=int, metavar="N", help="judge round N of the duplicate expansion")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("pool"); s.add_argument("qids", nargs="*")
    s = sp.add_parser("show"); s.add_argument("qid")
    s = sp.add_parser("std-check"); s.add_argument("qid")
    s = sp.add_parser("recall"); s.add_argument("qid"); s.add_argument("--k", type=int)
    s = sp.add_parser("fetch"); s.add_argument("qids", nargs="*")
    s = sp.add_parser("chunks"); s.add_argument("qid")
    s = sp.add_parser("unjudged"); s.add_argument("qid")
    s = sp.add_parser("check"); s.add_argument("qid"); s.add_argument("chunks", nargs="*")
    s = sp.add_parser("second"); s.add_argument("qid"); s.add_argument("--round", type=int, required=True)
    s.add_argument("--drops-only", action="store_true")
    s = sp.add_parser("second-check"); s.add_argument("qid"); s.add_argument("chunk")
    s.add_argument("--round", type=int, required=True)
    s = sp.add_parser("compare"); s.add_argument("qid"); s.add_argument("--round", type=int, required=True)
    s = sp.add_parser("adj-check"); s.add_argument("qid"); s.add_argument("part")
    s = sp.add_parser("build"); s.add_argument("qids", nargs="*"); s.add_argument("--skip-incomplete", action="store_true")
    s = sp.add_parser("verify"); s.add_argument("--phase", default="phase1", choices=("phase1", "phase2"))
    return ap


PHASE2_OK = {"show", "chunks", "unjudged", "check", "second", "second-check", "compare", "adj-check", "fetch", "std-check"}


def main(argv=None) -> None:
    a = parser().parse_args(argv)
    run = Run(a.run)
    if a.phase2 is not None and a.cmd not in PHASE2_OK:
        raise SystemExit(f"--phase2 does not apply to {a.cmd}")
    q = QSet(run, a.sid or default_set(run), a.phase2)
    {"pool": cmd_pool, "show": cmd_show, "std-check": cmd_std_check, "recall": cmd_recall, "fetch": cmd_fetch,
     "chunks": cmd_chunks, "unjudged": cmd_unjudged, "check": cmd_check, "second": cmd_second,
     "second-check": cmd_second_check, "compare": cmd_compare, "adj-check": cmd_adj_check, "build": cmd_build,
     "verify": cmd_verify}[a.cmd](q, a)


if __name__ == "__main__":
    main()
