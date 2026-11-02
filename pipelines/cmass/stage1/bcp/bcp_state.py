#!/usr/bin/env python3
"""What each BrowseComp-Plus Stage 1 agent step still has to do, its inputs, and checks of its outputs.

  bcp_state.py SET --summary                          # targets, done, invalid and pending per step
  bcp_state.py SET STEP --pending [--phase A|B] [--json] [--brief]   # questions whose STEP output is missing or invalid
  bcp_state.py SET STEP QID [--json]                  # one question's state for STEP
  bcp_state.py SET STEP QID --input                   # write the agent's input file and print its path
  bcp_state.py SET STEP QID --check                   # validate the agent's output; exit 1 on any error

STEP is one of project, scrutiny1, scrutiny2, coverage, external_audit, repair, strict_audit, rejudge
(bcp_common.STEPS). Run inside the run directory or pass --run. The Workflow scripts call this before and
after every agent: progress is read from disk, never from an agent's report.

Targets of each step (built by bcp_build.py, in README order):
  project         every task (tasks/<set>.jsonl) not listed in manual/skips.json
  scrutiny1/2     every PROJECTABLE question (scrutiny_inputs.jsonl)
  coverage        coverage_bundles.jsonl (all-hops-grounded questions, then verified repairs)
  external_audit  external_candidates.jsonl
  repair          repair_pool.jsonl (phase A: one failing hop, or an independent near miss; phase B: two)
  strict_audit    expansion_pool.jsonl
  rejudge         the strict audit's non-qualifiers
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bcp_common import STEPS, Run, Stage1, by_qid, qsort, validate, write_json  # noqa: E402


def strict_qualifies(out: dict) -> bool:
    """The strict audit's verdict, recomputed: every hop SUPPORTED and no uncovered phrase."""
    hops = out.get("hops") or []
    return bool(hops) and all(h.get("verdict") == "SUPPORTED" for h in hops) and not out.get("uncovered_phrases")


class State:
    def __init__(self, run: Run, sid: str):
        self.st = Stage1(run, sid)
        self._cache: dict[str, dict] = {}

    # ------------------------------------------------------------------ targets and inputs
    def targets(self, step: str, phase: str | None = None) -> dict[str, dict]:
        """qid -> the agent's input for every question STEP applies to."""
        key = f"{step}:{phase}"
        if key in self._cache:
            return self._cache[key]
        st = self.st
        res: dict[str, dict] = {}
        if step == "project":
            skips = st.manual("skips")
            for q, t in st.tasks().items():
                if q not in skips:
                    res[q] = {"record_id": q, "question": t.get("question", ""), "answer": t.get("answer", ""),
                              "hints": t.get("hints") or []}
        elif step in ("scrutiny1", "scrutiny2"):
            res = {r["qid"]: r for r in st.read("scrutiny_inputs")}
        elif step == "coverage":
            res = {r["qid"]: r for r in st.read("coverage_bundles")}
        elif step == "external_audit":
            res = {r["qid"]: {"qid": r["qid"], "question": r["question"], "answer": r["answer"], "hops": r["hops"]}
                   for r in st.read("external_candidates")}
        elif step == "repair":
            res = {r["qid"]: r for r in st.read("repair_pool") if phase is None or r.get("phase") == phase}
        elif step == "strict_audit":
            res = {r["qid"]: r for r in st.read("expansion_pool")}
        elif step == "rejudge":
            pool = by_qid(st.read("expansion_pool"), "qid")
            for q, out in st.outputs("strict_audit").items():
                if q in pool and not validate("strict_audit", q, out, pool[q]) and not strict_qualifies(out):
                    res[q] = {**pool[q], "strict_audit": {"hops": out["hops"], "uncovered_phrases": out["uncovered_phrases"],
                                                          "reason": out.get("reason", "")}}
        else:
            raise SystemExit(f"unknown step {step!r}; one of {sorted(STEPS)}")
        self._cache[key] = res
        return res

    def input_path(self, step: str, qid: str) -> Path:
        return self.st.dir / "inputs" / step / f"{qid}.json"

    # ------------------------------------------------------------------ state
    def state(self, step: str, qid: str) -> dict:
        tg = self.targets(step)
        out_p = self.st.out(step, qid)
        s = {"set": self.st.sid, "step": step, "qid": str(qid), "status": "", "output": str(out_p),
             "input": str(self.input_path(step, qid)), "guide": STEPS[step][0], "n_hops": 0, "errors": [], "detail": ""}
        if qid not in tg:
            s.update(status="not_a_target", detail=f"{qid} is not a target of {step} (see bcp_state.py --help)")
            return s
        inp = tg[qid]
        s["n_hops"] = len(inp.get("hops") or [])
        if not out_p.exists():
            s.update(status="pending", detail="no output yet")
            return s
        try:
            out = json.loads(out_p.read_text())
        except json.JSONDecodeError as e:
            s.update(status="invalid", errors=[f"not valid JSON: {e}"], detail="the output file is not valid JSON")
            return s
        errs = validate(step, qid, out, inp)
        s.update(status="invalid" if errs else "done", errors=errs,
                 detail=f"{len(errs)} error(s)" if errs else "output valid")
        return s

    def write_input(self, step: str, qid: str) -> Path:
        tg = self.targets(step)
        if qid not in tg:
            raise SystemExit(f"{qid} is not a target of {step}")
        p = self.input_path(step, qid)
        write_json(p, tg[qid])
        return p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", help="run directory (default: $CMASS_RUN or the current directory)")
    ap.add_argument("set")
    ap.add_argument("step", nargs="?")
    ap.add_argument("qid", nargs="?")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--phase", choices=["A", "B"], help="repair: only this phase's pool")
    ap.add_argument("--input", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--brief", action="store_true", help="with --pending: qid, step and status only")
    a = ap.parse_args()
    S = State(Run(a.run), a.set)

    if a.summary:
        rows = []
        for step in STEPS:
            tg = S.targets(step)
            sts = [S.state(step, q)["status"] for q in tg]
            rows.append({"step": step, "targets": len(tg), "done": sts.count("done"),
                         "invalid": sts.count("invalid"), "pending": sts.count("pending")})
        if a.json:
            print(json.dumps({"steps": rows}, indent=1))
        else:
            print(f"{'step':<15}{'targets':>8}{'done':>7}{'invalid':>9}{'pending':>9}")
            for r in rows:
                print(f"{r['step']:<15}{r['targets']:>8}{r['done']:>7}{r['invalid']:>9}{r['pending']:>9}")
        return
    if not a.step:
        ap.error("give a STEP (or --summary)")
    if a.pending:
        qs = []
        for q in qsort(S.targets(a.step, a.phase)):
            s = S.state(a.step, q)
            if s["status"] in ("pending", "invalid"):
                qs.append({"set": a.set, "qid": q, "next": a.step, "status": s["status"]} if a.brief else s)
        if a.json:
            print(json.dumps({"questions": qs}, ensure_ascii=False))
        else:
            for s in qs:
                print(f"{s['qid']}\t{s['status']}")
            print(f"({len(qs)} question(s) pending for {a.step})")
        return
    if not a.qid:
        ap.error("give a QID (or --pending)")
    if a.input:
        print(S.write_input(a.step, a.qid))
        return
    s = S.state(a.step, a.qid)
    if a.check:
        if s["status"] == "done":
            print(f"OK: {s['output']}")
            return
        print(f"{s['status'].upper()}: {s['detail']}")
        for e in s["errors"]:
            print(f"ERROR {e}")
        sys.exit(1)
    if a.json:
        print(json.dumps({"questions": [s]}, ensure_ascii=False))
    else:
        for k, v in s.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
