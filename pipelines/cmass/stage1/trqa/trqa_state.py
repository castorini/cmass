#!/usr/bin/env python3
"""Where every TRQA question stands in Stage 1, decided from the files on disk alone (never from an agent's
report), and the validator of every agent output. The Workflow scripts run it before and after each agent.

  trqa_state.py summary SET                         # counts by next step and by outcome
  trqa_state.py list SET --next project[,strict] [--json]   # questions whose next step is one of these
  trqa_state.py state SET QID [QID ...] [--json]    # one question: next step, files, validation errors
  trqa_state.py input SET STEP QID                  # the input an agent of STEP reads, as JSON
  trqa_state.py check SET STEP QID                  # validate one output file (exit 1 on errors)

All take --run RUN (default $CMASS_RUN or the current directory).

Steps, in the order the real run used them (README.md):
  project       agent    stage1/<set>/project/<qid>.json       total-recall projection, SUPPORTED / PARTIAL / NOT
  strict        agent    stage1/<set>/strict/<qid>.json        strict audit of the SUPPORTED ones (keep)
  extreme       agent    stage1/<set>/extreme/<qid>.json       gate 1: atomization + full-document re-grounding
  completeness  agent    stage1/<set>/completeness/<qid>.json  gate 2: adversarial hunt for missing members
  coherence     agent    stage1/<set>/coherence/<qid>.json     gate 3: cross-hop consistency + recomputation
  annotator     agent    stage1/<set>/annotator/<qid>.json     gate 4: human-annotator read, hop fixes
Between them the deterministic builders: collect_verdicts.py, build_strict_inputs.py, build_strict_list.py
(next step "build_strict_inputs" / "build_strict_list" / "backfill" means: run that script, or write the
hand-made backfill it asks for). A question that fails a step is "done" with that step as its stage.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from cmass.run import Run, read_jsonl  # noqa: E402

STEPS = ("project", "strict", "extreme", "completeness", "coherence", "annotator")
GATES = ("extreme", "completeness", "coherence", "annotator")
VERDICTS = ("SUPPORTED", "PARTIAL", "NOT")
PROJECT_FIELDS = ("record_id", "subset", "verdict", "operator", "target_attribute", "entity_set_description",
                  "n_entities_expected", "n_entities_recovered", "computed_value", "gold_answer", "matches_gold",
                  "recall_complete", "evidence", "rationale")


class Stage1:
    """Paths and loaders of one set's Stage 1 files."""

    def __init__(self, run: Run, sid: str):
        self.run, self.sid = run, sid
        self.dir = run.stage1_dir(sid)
        self._idx: dict[str, dict] = {}

    def file(self, step: str, qid: str) -> Path:
        return self.run.stage1_file(self.sid, step, qid)

    def list_file(self, name: str) -> Path:
        return self.dir / name

    def backfill_file(self, qid: str) -> Path:
        return self.dir / "strict_backfill" / f"{qid}.json"

    def load(self, step: str, qid: str):
        p = self.file(step, qid)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError as e:
            return {"_error": f"{p.name} is not valid JSON: {e}"}

    def tasks(self) -> dict[str, dict]:
        if "tasks" not in self._idx:
            self._idx["tasks"] = {t["record_id"]: t for t in self.run.tasks(self.sid)}
        return self._idx["tasks"]

    def index(self, name: str) -> dict[str, dict]:
        """A deterministic list (strict_inputs.jsonl, gate_inputs.jsonl, ...) keyed by record id."""
        if name not in self._idx:
            self._idx[name] = {str(r.get("record_id") or r.get("stem")): r for r in read_jsonl(self.list_file(name))}
        return self._idx[name]


def _bools(d: dict, keys, errs: list) -> None:
    for k in keys:
        if not isinstance(d.get(k), bool):
            errs.append(f"{k} must be true or false")


# ---------------------------------------------------------------------------------------------- validators
def check_project(d: dict, qid: str) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    missing = [k for k in PROJECT_FIELDS if k not in d]
    if missing:
        errs.append(f"missing fields: {', '.join(missing)}")
    if d.get("verdict") not in VERDICTS:
        errs.append(f"verdict must be one of {VERDICTS}")
    _bools(d, ("matches_gold", "recall_complete"), errs)
    if "evidence" in d and not isinstance(d["evidence"], list):
        errs.append("evidence must be a list of {entity, attribute_value, doc_id}")
    elif any(not isinstance(e, dict) or "doc_id" not in e or "entity" not in e for e in d.get("evidence") or []):
        warns.append("an evidence entry lacks entity or doc_id")
    for k in ("n_entities_expected", "n_entities_recovered"):
        if k in d and d[k] is not None and not isinstance(d[k], int):
            warns.append(f"{k} is not an integer")
    if str(d.get("record_id")) != qid:
        warns.append(f"record_id {d.get('record_id')!r} is not the file's question id")
    return errs, warns


def check_strict(d: dict, qid: str) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    _bools(d, ("constraints_covered", "all_grounded", "aggregate_ok", "complete", "keep"), errs)
    if not errs and d["keep"] != (d["constraints_covered"] and d["all_grounded"] and d["aggregate_ok"] and d["complete"]):
        errs.append("keep must equal constraints_covered AND all_grounded AND aggregate_ok AND complete")
    if not isinstance(d.get("hops"), list):
        errs.append("hops must be a list")
    else:
        odd = [i for i, h in enumerate(d["hops"], 1) if not isinstance(h, dict) or not isinstance(h.get("clue"), str)]
        if odd:
            warns.append(f"hop(s) {odd} lack the {{clue, grounded, doc_ids, snippet}} shape: build_strict_list.py "
                         "will ask for a hand-written backfill if the question is kept")
    for k in ("failures", "completeness_note", "reason"):
        if k not in d:
            warns.append(f"no {k}")
    if str(d.get("stem")) != qid:
        warns.append(f"stem {d.get('stem')!r} is not the file's question id")
    return errs, warns


def check_extreme(d: dict, qid: str, inp: dict | None) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    _bools(d, ("all_atoms_covered", "all_hops_grounded", "keep"), errs)
    if not errs and d["keep"] != (d["all_atoms_covered"] and d["all_hops_grounded"]):
        errs.append("keep must equal all_atoms_covered AND all_hops_grounded")
    hops = d.get("hops")
    if not isinstance(hops, list):
        return errs + ["hops must be a list"], warns
    for h in hops:
        if not isinstance(h, dict) or not isinstance(h.get("index"), int):
            errs.append("every hop needs an integer index")
            continue
        if h.get("kind") not in ("evidence", "derivation"):
            errs.append(f"hop {h['index']}: kind must be evidence or derivation")
        if not isinstance(h.get("grounded"), bool):
            errs.append(f"hop {h['index']}: grounded must be true or false")
        if d.get("keep") is True and h.get("kind") == "evidence" and not (h.get("doc_id") and h.get("snippet")):
            errs.append(f"hop {h['index']}: a passing evidence hop needs doc_id and a fresh verbatim snippet")
    if d.get("keep") is True and inp:
        want = {h["index"] for h in inp.get("hops") or []}
        have = {h.get("index") for h in hops if isinstance(h, dict)}
        if want - have:
            errs.append(f"input hop(s) {sorted(want - have)} missing from hops")
    for k in ("atoms", "uncovered_atoms", "ungrounded_hops", "miskinded_hops", "reason"):
        if k not in d:
            warns.append(f"no {k}")
    if str(d.get("qid")) != qid:
        warns.append(f"qid {d.get('qid')!r} is not the file's question id")
    return errs, warns


def check_completeness(d: dict, qid: str) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    _bools(d, ("set_complete", "aggregate_unchanged", "keep"), errs)
    if not errs and d["keep"] != (d["set_complete"] or d["aggregate_unchanged"]):
        errs.append("keep must equal set_complete OR aggregate_unchanged")
    if not isinstance(d.get("extra_members", []), list):
        errs.append("extra_members must be a list")
    if not isinstance(d.get("searches_run"), int):
        warns.append("searches_run is not an integer")
    elif d["searches_run"] < 8 and d.get("keep") is False:
        warns.append("fewer than 8 searches: the guide says keep=false is not justified on completeness grounds")
    if str(d.get("qid")) != qid:
        warns.append(f"qid {d.get('qid')!r} is not the file's question id")
    return errs, warns


def check_coherence(d: dict, qid: str) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    _bools(d, ("cross_hop_consistent", "aggregate_matches_gold", "jointly_justifies", "keep"), errs)
    if "attribution_ok" in d and not isinstance(d["attribution_ok"], bool):
        errs.append("attribution_ok must be true or false")
    if not errs and d["keep"] != (d["cross_hop_consistent"] and d["aggregate_matches_gold"] and d["jointly_justifies"]
                                  and d.get("attribution_ok", True)):
        errs.append("keep must equal cross_hop_consistent AND aggregate_matches_gold AND jointly_justifies AND attribution_ok")
    if not isinstance(d.get("failures", []), list):
        errs.append("failures must be a list")
    if str(d.get("qid")) != qid:
        warns.append(f"qid {d.get('qid')!r} is not the file's question id")
    return errs, warns


def check_annotator(d: dict, qid: str, extreme: dict | None) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    _bools(d, ("keep",), errs)
    hops = d.get("hops")
    if not isinstance(hops, list):
        return errs + ["hops must be a list"], warns
    for h in hops:
        if not isinstance(h, dict) or not isinstance(h.get("index"), int) or h.get("verdict") not in ("keep", "fix", "drop"):
            errs.append("every hop needs an integer index and a verdict keep / fix / drop")
    if not errs and d["keep"] != all(h["verdict"] in ("keep", "fix") for h in hops):
        errs.append("keep must be true exactly when no hop is 'drop'")
    if extreme:
        want = {h.get("index") for h in extreme.get("hops") or [] if isinstance(h, dict)}
        have = {h.get("index") for h in hops if isinstance(h, dict)}
        if want - have:
            errs.append(f"every hop index must appear: missing {sorted(want - have)}")
    if str(d.get("qid")) != qid:
        warns.append(f"qid {d.get('qid')!r} is not the file's question id")
    return errs, warns


def validate(s1: Stage1, step: str, qid: str, d: dict | None = None) -> tuple[list[str], list[str]]:
    d = s1.load(step, qid) if d is None else d
    if d is None:
        return ["no output yet"], []
    if "_error" in d:
        return [d["_error"]], []
    if not isinstance(d, dict):
        return ["the output must be a JSON object"], []
    if step == "project":
        return check_project(d, qid)
    if step == "strict":
        return check_strict(d, qid)
    if step == "extreme":
        return check_extreme(d, qid, s1.index("gate_inputs.jsonl").get(qid))
    if step == "completeness":
        return check_completeness(d, qid)
    if step == "coherence":
        return check_coherence(d, qid)
    if step == "annotator":
        return check_annotator(d, qid, s1.load("extreme", qid))
    raise SystemExit(f"unknown step {step!r}; one of {STEPS}")


def needs_backfill(strict: dict) -> bool:
    return any(not isinstance(h, dict) or not isinstance(h.get("clue"), str) for h in strict.get("hops") or [])


# ---------------------------------------------------------------------------------------------- state
def state(s1: Stage1, qid: str) -> dict:
    st = {"set": s1.sid, "qid": qid, "next": "", "stage": "", "outcome": "", "detail": "", "out_file": "",
          "input_cmd": "", "extreme_file": str(s1.file("extreme", qid)), "errors": [], "warnings": []}
    tool = f"python3 {Path(__file__).resolve()} --run {s1.run.root}"

    def pending(step, errs, warns, detail=""):
        st.update(next=step, stage=step, out_file=str(s1.file(step, qid)),
                  input_cmd=f"{tool} input {s1.sid} {step} {qid}", errors=errs, warnings=warns,
                  detail=detail or ("; ".join(errs) if errs else ""))
        return st

    def done(stage, outcome, detail=""):
        st.update(next="done", stage=stage, outcome=outcome, detail=detail)
        return st

    if qid not in s1.tasks():
        return done("task", "error", f"{qid} is not a task of set {s1.sid}") | {"next": "error"}
    for step in STEPS:
        if step == "strict" and qid not in s1.index("strict_inputs.jsonl"):
            st.update(next="build_strict_inputs", stage="strict", detail="run build_strict_inputs.py")
            return st
        if step == "extreme" and qid not in s1.index("gate_inputs.jsonl"):
            strict = s1.load("strict", qid) or {}
            if needs_backfill(strict) and not s1.backfill_file(qid).exists():
                st.update(next="backfill", stage="strict",
                          detail=f"hand-write {s1.backfill_file(qid)} (README.md, manual step), then build_strict_list.py")
            else:
                st.update(next="build_strict_list", stage="strict", detail="run build_strict_list.py")
            return st
        errs, warns = validate(s1, step, qid)
        if errs:
            return pending(step, errs, warns)
        d = s1.load(step, qid)
        if step == "project":
            if d["verdict"] != "SUPPORTED":
                return done("project", d["verdict"])
            continue
        if not d["keep"]:
            return done(step, "dropped", str(d.get("reason") or "")[:300])
        st["warnings"] += warns
    return done("annotator", "kept")


def all_states(s1: Stage1) -> list[dict]:
    return [state(s1, q) for q in s1.tasks()]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run")
    sub = p.add_subparsers(dest="cmd", required=True)
    sm = sub.add_parser("summary")
    sm.add_argument("set")
    ls = sub.add_parser("list")
    ls.add_argument("set")
    ls.add_argument("--next", required=True, help="comma-separated next steps")
    ls.add_argument("--json", action="store_true")
    s = sub.add_parser("state")
    s.add_argument("set")
    s.add_argument("qids", nargs="+")
    s.add_argument("--json", action="store_true")
    i = sub.add_parser("input")
    i.add_argument("set")
    i.add_argument("step", choices=STEPS)
    i.add_argument("qid")
    c = sub.add_parser("check")
    c.add_argument("set")
    c.add_argument("step", choices=STEPS)
    c.add_argument("qid")
    a = p.parse_args()
    s1 = Stage1(Run(a.run), a.set)

    if a.cmd == "summary":
        sts = all_states(s1)
        print(f"{len(sts)} tasks in set {a.set}")
        print("next:", dict(Counter(x["next"] for x in sts)))
        print("outcome:", dict(Counter(f"{x['stage']}:{x['outcome']}" for x in sts if x["next"] == "done")))
    elif a.cmd == "list":
        want = set(a.next.split(","))
        rows = [x for x in all_states(s1) if x["next"] in want]
        if a.json:
            print(json.dumps({"questions": [{"set": x["set"], "qid": x["qid"], "next": x["next"]} for x in rows]}))
        else:
            for x in rows:
                print(f"{x['qid']}\t{x['next']}\t{x['detail'][:120]}")
            print(f"({len(rows)} questions)")
    elif a.cmd == "state":
        rows = [state(s1, q) for q in a.qids]
        if a.json:
            print(json.dumps({"questions": rows}, ensure_ascii=False))
        else:
            for x in rows:
                print(json.dumps(x, ensure_ascii=False, indent=1))
    elif a.cmd == "input":
        if a.step == "project":
            row = s1.tasks().get(a.qid)
        elif a.step == "strict":
            row = s1.index("strict_inputs.jsonl").get(a.qid)
        else:
            row = s1.index("gate_inputs.jsonl").get(a.qid)
        if row is None:
            raise SystemExit(f"no {a.step} input for {a.qid}: {state(s1, a.qid)['detail']}")
        print(json.dumps(row, ensure_ascii=False, indent=1))
    elif a.cmd == "check":
        errs, warns = validate(s1, a.step, a.qid)
        for e in errs:
            print("ERROR:", e)
        for w in warns:
            print("warn:", w)
        print(f"{len(errs)} error(s), {len(warns)} warning(s)")
        sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
