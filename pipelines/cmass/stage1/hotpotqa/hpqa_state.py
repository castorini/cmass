#!/usr/bin/env python3
"""Where every HotpotQA Stage 1 question stands, decided from the files on disk alone, and the check of
each agent's output file. The workflows (contamination.js, gates.js) run it before and after every
agent, so they resume from disk and never trust an agent's own report.

  hpqa_state.py [--run RUN] list SET [QID ...] [--next STEP[,STEP]] [--brief] [--json]
  hpqa_state.py [--run RUN] check SET QID STEP        # STEP: contamination extreme uniqueness coherence annotator
  hpqa_state.py [--run RUN] summary SET [--json]      # the funnel, per origin (nanoknow, ranked batch N)

A question is in Stage 1 when it is a row of stage1/<set>/selected.jsonl. Its next step is the first of
these that is not settled, in the order the procedure ran:

  select / collect   no screen input yet (select_questions.py nanoknow, or collect_docs.py for a batch)
  contamination      no valid stage1/<set>/contamination/<qid>.json        -> not clean: dropped
  (bridge only)      HotpotQA type comparison                              -> excluded
  stage_gates        clean bridge question without a gate input (select_questions.py stage-gates)
  extreme, uniqueness, coherence, annotator   no valid output              -> keep false: dropped
  done               every gate kept it: a survivor

A file counts only when `check` reports 0 errors for it; an invalid file leaves the step pending, so the
agent that wrote it is asked to fix it. A run file means the question was judged, not that it passed:
the verdict field decides (clean for the screen, keep for the gates). `check` also prints warnings: a
quote or snippet it cannot find verbatim in a cached copy of the cited document (documents not yet
cached are not fetched), and length limits the guides state.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hpqa_common import GATES, STEPS, file, fragments, load_json, natural_key, run_of, selected, verbatim_any  # noqa: E402
from cmass.corpus import Corpus  # noqa: E402

LEAK_KINDS = ("poses-question-and-answers-it", "quiz-or-answer-key-page", "packaged-qa-for-final-hop", "none")
VERDICTS = ("keep", "fix", "drop")
S, B, I, L = "str", "bool", "int", "list"
FIELDS = {   # the original structured-output schemas: (required, optional)
    "contamination": ({"qid": S, "leaked": B, "single_doc_answerable": B, "clean": B},
                      {"leak_doc": S, "leak_kind": S, "leak_evidence": S, "single_doc": S, "single_doc_evidence": S,
                       "hops_needed": I, "distinct_docs_needed": I, "reason": S}),
    "extreme": ({"qid": S, "all_atoms_covered": B, "all_hops_grounded": B, "keep": B},
                {"atoms": L, "hops": L, "uncovered_atoms": L, "ungrounded_hops": L, "reason": S}),
    "uniqueness": ({"qid": S, "answer_unique": B, "gold_supported": B, "keep": B},
                   {"searches_run": I, "rivals": L, "corpus_answer": S, "reason": S}),
    "coherence": ({"qid": S, "cross_hop_consistent": B, "jointly_derives_answer": B, "keep": B},
                  {"multi_document": B, "failures": L, "new_pattern": S, "reason": S}),
    "annotator": ({"qid": S, "keep": B, "hops": L}, {"issues": L, "reason": S}),
}
PASS_FIELD = {"contamination": "clean", **{g: "keep" for g in GATES}}


def typed(v, t: str) -> bool:
    if t == B:
        return isinstance(v, bool)
    if t == I:
        return isinstance(v, int) and not isinstance(v, bool)
    if t == L:
        return isinstance(v, list)
    return isinstance(v, str)


class Checker:
    def __init__(self, run, sid: str, qid: str, corpus: Corpus | None = None):
        self.run, self.sid, self.qid, self.corpus = run, sid, qid, corpus
        self.errors: list[str] = []
        self.warns: list[str] = []

    def err(self, m):
        self.errors.append(m)

    def warn(self, m):
        self.warns.append(m)

    def texts(self, doc_ids) -> tuple[list[str], list[str]]:
        """Cached texts of the documents, and the ids not cached (never fetched here)."""
        got, missing = [], []
        for d in doc_ids:
            t = self.corpus.cached_doc(d) if (self.corpus and d) else None
            (got.append(t) if t else missing.append(d))
        return got, missing

    def quote_warn(self, what: str, quote: str, doc_ids: list[str]) -> None:
        if not quote or not fragments(quote):
            return
        got, missing = self.texts([d for d in doc_ids if d])
        if got and not missing and not verbatim_any(quote, got):
            self.warn(f"{what}: not found verbatim in {', '.join(d for d in doc_ids if d)} (take it with cm.py quote)")

    def fields(self, step: str, out: dict) -> bool:
        req, opt = FIELDS[step]
        for k, t in req.items():
            if k not in out:
                self.err(f"missing field {k!r}")
            elif not typed(out[k], t):
                self.err(f"field {k!r} must be {t}")
        for k, t in opt.items():
            if k in out and out[k] is not None and not typed(out[k], t):
                self.err(f"field {k!r} must be {t}")
        extra = sorted(set(out) - set(req) - set(opt))
        if extra:
            self.warn(f"fields outside the schema: {extra}")
        if "qid" in out and str(out["qid"]) != self.qid:
            self.err(f"qid is {out['qid']!r}, expected {self.qid!r}")
        return not self.errors

    # ------------------------------------------------------------------ steps
    def check(self, step: str, out, inp: dict | None, extreme: dict | None) -> None:
        if out is None:
            self.err("no output file")
            return
        if not isinstance(out, dict) or "_error" in out:
            self.err(out.get("_error") if isinstance(out, dict) else "the file is not a JSON object")
            return
        if not self.fields(step, out):
            return
        getattr(self, f"c_{step}")(out, inp or {}, extreme or {})

    def c_contamination(self, o, inp, _):
        docs = list(inp.get("doc_ids") or [])
        if o["clean"] != (not o["leaked"] and not o["single_doc_answerable"]):
            self.err("clean must equal NOT leaked AND NOT single_doc_answerable")
        kind = o.get("leak_kind")
        if kind is not None and kind not in LEAK_KINDS:
            self.err(f"leak_kind must be one of {LEAK_KINDS}")
        if o["leaked"]:
            if kind in (None, "none"):
                self.err("leaked is true: leak_kind must name the kind of leak")
            if o.get("leak_doc") not in docs:
                self.err("leaked is true: leak_doc must be one of the question's documents (input doc_ids)")
            self.quote_warn("leak_evidence", o.get("leak_evidence") or "", [o.get("leak_doc")])
        elif kind not in (None, "none"):
            self.err("leaked is false: leak_kind must be 'none'")
        if o["single_doc_answerable"]:
            if o.get("single_doc") not in docs:
                self.err("single_doc_answerable is true: single_doc must be one of the question's documents")
            self.quote_warn("single_doc_evidence", o.get("single_doc_evidence") or "", [o.get("single_doc")])

    def c_extreme(self, o, inp, _):
        if o["keep"] != (o["all_atoms_covered"] and o["all_hops_grounded"]):
            self.err("keep must equal all_atoms_covered AND all_hops_grounded")
        hops = o.get("hops") or []
        if not hops:
            self.err("hops is empty: decompose the question into numbered hops")
        idx = []
        for n, h in enumerate(hops, 1):
            if not isinstance(h, dict):
                self.err(f"hops[{n}] is not an object")
                continue
            if not typed(h.get("index"), I) or not typed(h.get("clue"), S) or not h.get("clue", "").strip() \
                    or not typed(h.get("grounded"), B):
                self.err(f"hops[{n}] needs index (int), clue (text) and grounded (bool)")
                continue
            idx.append(h["index"])
            if h.get("grounded"):
                ids = list(h.get("doc_ids") or [])
                if not h.get("doc_id") or not h.get("snippet"):
                    self.err(f"hop {h['index']} is grounded: it needs doc_id and snippet")
                if not ids or (h.get("doc_id") and h["doc_id"] not in ids):
                    self.err(f"hop {h['index']} is grounded: doc_ids must list the grounding documents, doc_id among them")
                snip = h.get("snippet") or ""
                if len(snip) > 280:
                    self.warn(f"hop {h['index']}: snippet is {len(snip)} characters (the guide's limit is 280)")
                self.quote_warn(f"hop {h['index']} snippet", snip, ids or [h.get("doc_id")])
        if idx and idx != list(range(1, len(idx) + 1)):
            self.err("hop indices must be 1, 2, 3, ... in order")
        if not self.errors:
            grounded = all(h.get("grounded") for h in hops)
            if o["all_hops_grounded"] != grounded:
                self.err("all_hops_grounded must say whether every hop is grounded")
            ung = sorted(h["index"] for h in hops if not h.get("grounded"))
            if "ungrounded_hops" in o and sorted(o.get("ungrounded_hops") or []) != ung:
                self.err(f"ungrounded_hops must list the hops with grounded false: {ung}")
            if o["all_atoms_covered"] != (not o.get("uncovered_atoms")):
                self.err("all_atoms_covered must be true exactly when uncovered_atoms is empty")
            for a in o.get("atoms") or []:
                if not isinstance(a, dict) or not typed(a.get("atom"), S) or not typed(a.get("hop_index"), I):
                    self.err("each atom needs atom (text) and hop_index (int)")
                    break
                if a["hop_index"] and a["hop_index"] not in idx:
                    self.err(f"atom {a['atom'][:40]!r}: hop_index {a['hop_index']} is not a hop (0 = uncovered)")
            if not any(h.get("is_bridge") for h in hops):
                self.warn("no hop is marked is_bridge")

    def c_uniqueness(self, o, inp, _):
        if o["keep"] != (o["answer_unique"] and o["gold_supported"]):
            self.err("keep must equal answer_unique AND gold_supported")
        n = o.get("searches_run")
        if o["answer_unique"] and (n is None or n < 6):
            self.err("answer_unique is true but searches_run is under 6: fewer than 6 searches do not justify uniqueness")
        if n is not None and n > 15:
            self.warn(f"searches_run is {n}; the guide allows up to 15")
        for x in o.get("rivals") or []:
            if not isinstance(x, dict) or not typed(x.get("candidate"), S) or not typed(x.get("why_not_excluded"), S):
                self.err("each rival needs candidate and why_not_excluded")
                break

    def c_coherence(self, o, inp, _):
        if o["keep"] != (o["cross_hop_consistent"] and o["jointly_derives_answer"]):
            self.err("keep must equal cross_hop_consistent AND jointly_derives_answer")
        for f in o.get("failures") or []:
            if not isinstance(f, dict) or not typed(f.get("pattern"), S) or not typed(f.get("detail"), S):
                self.err("each failure needs pattern and detail")
                break
        if not o["keep"] and not (o.get("failures") or o.get("new_pattern")):
            self.warn("keep is false but no failure is recorded")

    def c_annotator(self, o, inp, ex):
        hops = o.get("hops") or []
        ehops = {h.get("index"): h for h in (ex.get("hops") or []) if isinstance(h, dict)}
        seen = []
        for h in hops:
            if not isinstance(h, dict) or not typed(h.get("index"), I) or h.get("verdict") not in VERDICTS:
                self.err(f"each hop needs index (int) and verdict ({' / '.join(VERDICTS)})")
                return
            seen.append(h["index"])
            if h["verdict"] == "fix":
                snip = h.get("snippet") or ""
                if len(snip) > 300:
                    self.warn(f"hop {h['index']}: fixed snippet is {len(snip)} characters (the guide's limit is 300)")
                e = ehops.get(h["index"]) or {}
                allowed = list(dict.fromkeys([e.get("doc_id")] + list(e.get("doc_ids") or [])))
                if h.get("doc_id") and h["doc_id"] not in allowed:
                    self.warn(f"hop {h['index']}: the fix cites {h['doc_id']}, which the extreme audit did not verify for "
                              "this hop; the build will keep the extreme audit's evidence")
                self.quote_warn(f"hop {h['index']} fixed snippet", snip, [h.get("doc_id") or e.get("doc_id")])
        if sorted(seen) != sorted(ehops):
            self.err(f"every hop of the extreme audit must appear exactly once: expected {sorted(ehops)}, got {sorted(seen)}")
        if o["keep"] != all(h.get("verdict") in ("keep", "fix") for h in hops):
            self.err("keep must be true exactly when every hop ends at keep or fix")
        for x in o.get("issues") or []:
            if not isinstance(x, dict) or not typed(x.get("kind"), S) or not typed(x.get("detail"), S):
                self.err("each issue needs kind and detail")
                break


def check_output(run, sid, qid, step, corpus=None) -> Checker:
    c = Checker(run, sid, qid, corpus)
    inp = load_json(file(run, sid, "gate_input" if step in GATES else "input", qid))
    ex = load_json(file(run, sid, "extreme", qid)) if step == "annotator" else None
    c.check(step, load_json(file(run, sid, step, qid)), inp if isinstance(inp, dict) else None,
            ex if isinstance(ex, dict) else None)
    return c


# ---------------------------------------------------------------------------------------------- state
def question_state(run, sid, row: dict) -> dict:
    q = row["record_id"]
    st = {"set": sid, "qid": q, "origin": row.get("origin") or "", "batch": int(row.get("batch") or 0),
          "hotpot_type": row.get("hotpot_type") or "", "next": "", "stage": "", "detail": "", "errors": [],
          "input_file": str(file(run, sid, "input", q)), "gate_input_file": str(file(run, sid, "gate_input", q)),
          "output_file": "", "extreme_file": str(file(run, sid, "extreme", q))}
    inp = load_json(file(run, sid, "input", q))
    if inp is None:
        st["next"] = "collect" if row.get("origin") == "ranked" else "select"
        st["detail"] = "no screen input yet"
        return st
    if not isinstance(inp, dict) or "_error" in inp:
        st.update(next="error", detail="the screen input is not valid JSON")
        return st
    for step in STEPS:
        if step == "extreme":
            if row.get("hotpot_type") != "bridge":
                st.update(next="excluded", stage="comparison", detail="bridge questions only (HotpotQA's type field)")
                return st
            if not file(run, sid, "gate_input", q).exists():
                st.update(next="stage_gates", detail="clean: run select_questions.py stage-gates")
                return st
        p = file(run, sid, step, q)
        if not p.exists():
            st.update(next=step, output_file=str(p))
            return st
        c = check_output(run, sid, q, step)
        if c.errors:
            st.update(next=step, output_file=str(p), errors=c.errors[:8],
                      detail=f"{p.name} fails the check ({len(c.errors)} errors): {c.errors[0]}")
            return st
        out = load_json(p)
        if not out.get(PASS_FIELD[step]):
            st.update(next="dropped", stage=step, detail=str(out.get("reason") or "")[:300])
            return st
    st.update(next="done", stage="survivor")
    return st


def states(run, sid, qids=None) -> list[dict]:
    sel = selected(run, sid)
    rows = [sel[q] for q in qids] if qids else list(sel.values())
    return [question_state(run, sid, r) for r in rows]


def group_of(st) -> str:
    return "nanoknow" if st["origin"] == "nanoknow" else f"ranked batch {st['batch']}"


def summary(run, sid) -> dict:
    groups = defaultdict(Counter)
    for st in states(run, sid):
        g = groups[group_of(st)]
        g["selected"] += 1
        q = st["qid"]
        cx = load_json(file(run, sid, "contamination", q)) if st["next"] not in ("select", "collect") else None
        if isinstance(cx, dict) and "_error" not in cx and st["next"] != "contamination":
            g["screened"] += 1
            g["clean"] += bool(cx.get("clean"))
            g["leaked"] += bool(cx.get("leaked"))
            g["single_doc"] += bool(cx.get("single_doc_answerable"))
            g["leaked_and_single_doc"] += bool(cx.get("leaked") and cx.get("single_doc_answerable"))
        if st["next"] == "excluded":
            g["clean_comparison_excluded"] += 1
        for s in GATES:
            if st["next"] in ("excluded", "stage_gates", s) or st["stage"] == "contamination":
                break
            out = load_json(file(run, sid, s, q))
            if not isinstance(out, dict) or "_error" in out:
                break
            g[f"{s}_judged"] += 1
            if not out.get("keep"):
                break
            g[f"{s}_kept"] += 1
        if st["next"] == "dropped":
            g[f"dropped_{st['stage']}"] += 1
        if st["next"] == "done":
            g["survivors"] += 1
        if st["next"] not in ("done", "dropped", "excluded"):
            g[f"pending_{st['next']}"] += 1
    order = sorted(groups, key=lambda k: (k != "nanoknow", natural_key(k)))
    return {k: dict(groups[k]) for k in order}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="run directory (default: $CMASS_RUN or the current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    li = sub.add_parser("list")
    li.add_argument("set")
    li.add_argument("qids", nargs="*")
    li.add_argument("--next", help="only questions whose next step is one of these (comma-separated)")
    li.add_argument("--brief", action="store_true")
    li.add_argument("--json", action="store_true")
    ch = sub.add_parser("check")
    ch.add_argument("set")
    ch.add_argument("qid")
    ch.add_argument("step", choices=STEPS)
    su = sub.add_parser("summary")
    su.add_argument("set")
    su.add_argument("--json", action="store_true")
    a = ap.parse_args()
    run = run_of(a.run)
    run.config.set(a.set)

    if a.cmd == "check":
        c = check_output(run, a.set, a.qid, a.step, Corpus(run))
        for e in c.errors:
            print(f"ERROR {a.set}/{a.qid} {a.step}: {e}")
        for w in c.warns:
            print(f"warn  {a.set}/{a.qid} {a.step}: {w}")
        print(f"{a.set}/{a.qid} {a.step}: {len(c.errors)} errors, {len(c.warns)} warnings")
        sys.exit(1 if c.errors else 0)

    if a.cmd == "summary":
        s = summary(run, a.set)
        if a.json:
            print(json.dumps(s, indent=1))
            return
        for g, c in s.items():
            print(f"{g}: " + ", ".join(f"{k} {v}" for k, v in c.items()))
        return

    sel = selected(run, a.set)
    missing = [q for q in a.qids if q not in sel]
    if missing:
        sys.exit(f"not selected for Stage 1 (stage1/{a.set}/selected.jsonl): {missing}")
    sts = states(run, a.set, a.qids or None)
    if a.next:
        want = set(a.next.split(","))
        sts = [s for s in sts if s["next"] in want]
    if a.brief:
        sts = [{"set": s["set"], "qid": s["qid"], "next": s["next"]} for s in sts]
    if a.json:
        print(json.dumps({"questions": sts}, ensure_ascii=False))
        return
    for s in sts:
        print(f"{s['set']}/{s['qid']}: {s['next']}" + (f" ({s['stage']})" if s.get("stage") else "")
              + (f" - {s['detail']}" if s.get("detail") else ""))
    print(dict(Counter(s["next"] for s in sts)))


if __name__ == "__main__":
    main()
