"""Shared definitions for the BrowseComp-Plus Stage 1 port (README.md): the steps, where their files live in
the run directory, the deterministic rules that turn agent outputs into lists, and the output validators.

Standard library only. Every path is inside the run directory (../../cmass/run.py):

  tasks/<set>.jsonl                          Stage 0 (stage0_tasks.py)
  stage1/<set>/<step>/<qid>.json             one agent output per question per step (STEPS)
  stage1/<set>/external/<qid>.json           independently produced projections, supplied by the operator
  stage1/<set>/manual/<name>.json            a person's decisions (MANUAL, MANUAL.md)
  stage1/<set>/<list>.jsonl                  deterministic lists (bcp_build.py)
  stage1/<set>/<step>/<qid>.searchlog.jsonl  the step's searches for the question (cm.py --stage 1 --step <step>)
  inputs/<set>.jsonl                         Stage 2 input: the projected candidates
  inputs/<set>_near_misses.jsonl             Stage 2 input: the near misses
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPELINE = HERE.parent.parent                      # pipelines/cmass
sys.path.insert(0, str(PIPELINE))
from cmass.run import Run, read_jsonl, write_json, write_jsonl  # noqa: E402,F401

VERDICTS = ("PROJECTABLE", "PARTIAL", "NOT")
SKIP_VERDICT = "AUP_SKIP"                          # the label the original run gave a question with no projection

# step -> guide, and a one-line description. Each step writes stage1/<set>/<step>/<qid>.json.
STEPS = {
    "project": ("PROJECT.md", "decomposition, grounding and answerability verdict (every task)"),
    "scrutiny1": ("SCRUTINY.md", "scrutiny pass 1: per-hop support and necessity (every PROJECTABLE question)"),
    "scrutiny2": ("SCRUTINY.md", "scrutiny pass 2: support, necessity and comprehensiveness (every PROJECTABLE question)"),
    "coverage": ("COVERAGE.md", "tool-free coverage audit (all-hops-grounded questions and verified repairs)"),
    "external_audit": ("EXTERNAL_AUDIT.md", "retrieval-verified all-hop audit of independent projections"),
    "repair": ("REPAIR.md", "near-miss repair of the failing hops (phase A: one hop short, phase B: two)"),
    "strict_audit": ("EXPANSION.md", "strict audit of the unused PROJECTABLE questions"),
    "rejudge": ("EXPANSION.md", "lenient re-judging of the strict audit's failures"),
}

# a person's decisions, stage1/<set>/manual/<name>.json: {"<qid>": {...}} (MANUAL.md)
MANUAL = {
    "skips": "questions whose projection agent refused (content policy): {qid: {reason}}",
    "review": "the manual review of the coverage-audited set: {qid: {decision: drop|keep, reason}}",
    "repairs": "claimed repairs: {qid: {verified: bool, decision: accept|reject|'', reason}}",
    "overrides": "hop lists replaced by hand on a candidate: {qid: {hops: [...], reason}}",
    "expansion_trim": "expansion survivors removed by hand: {qid: {decision: drop, reason}}",
    "qrels_review": "candidates cut by the later manual qrels review (tier-1 near misses): {qid: {reason}}",
}

LISTS = ("verdicts", "projectable", "scrutiny_inputs", "all_hops_v1", "all_hops_v2", "comprehensive",
         "all_grounded", "all_grounded_rejected", "coverage_bundles", "coverage_final", "coverage_rejected",
         "reviewed", "external_candidates", "external_verified", "repair_pool", "repaired", "candidates",
         "expansion_pool", "expansion_survivors")


# ---------------------------------------------------------------------------------------------- paths
class Stage1:
    """The BCP Stage 1 files of one set inside a run directory."""

    def __init__(self, run: Run, sid: str):
        self.run, self.sid = run, sid
        self.dir = run.stage1_dir(sid)

    def out(self, step: str, qid: str) -> Path:
        return self.run.stage1_file(self.sid, step, qid)

    def outputs(self, step: str) -> dict[str, dict]:
        """Every parseable output of a step, by qid (unparseable files are left to the validator)."""
        d, res = self.dir / step, {}
        for p in sorted(d.glob("*.json")) if d.exists() else []:
            try:
                res[p.stem] = json.loads(p.read_text())
            except json.JSONDecodeError:
                pass
        return res

    def lst(self, name: str) -> Path:
        return self.dir / f"{name}.jsonl"

    def read(self, name: str) -> list[dict]:
        return read_jsonl(self.lst(name))

    def has(self, name: str) -> bool:
        return self.lst(name).exists()

    def write(self, name: str, rows: list[dict]) -> None:
        write_jsonl(self.lst(name), rows)

    def manual(self, name: str, required: bool = False) -> dict:
        p = self.dir / "manual" / f"{name}.json"
        if not p.exists():
            if required:
                raise SystemExit(f"{p} is missing: this is a manual step (MANUAL.md, '{name}'). Write the "
                                 "decisions file, or {} if nothing is decided, then rerun.")
            return {}
        d = json.loads(p.read_text())
        if not isinstance(d, dict):
            raise SystemExit(f"{p}: expected an object keyed by question id")
        return {str(k): (v if isinstance(v, dict) else {"reason": str(v)}) for k, v in d.items()}

    def external(self) -> dict[str, dict]:
        d, res = self.dir / "external", {}
        for p in sorted(d.glob("*.json")) if d.exists() else []:
            res[p.stem] = json.loads(p.read_text())
        return res

    def tasks(self) -> dict[str, dict]:
        return {t["record_id"]: t for t in self.run.tasks(self.sid)}

    def near_miss_file(self) -> Path:
        return self.run.root / "inputs" / f"{self.sid}_near_misses.jsonl"


def by_qid(rows: list[dict], key: str = "record_id") -> dict[str, dict]:
    return {str(r[key]): r for r in rows}


def qkey(q):
    """Sort key: numeric order when ids are numbers (BrowseComp-Plus query ids are), else text order."""
    return (0, int(q), "") if str(q).isdigit() else (1, 0, str(q))


def qsort(qids):
    return sorted(qids, key=qkey)


# ---------------------------------------------------------------------------------------------- rules
def decomposition(proj: dict) -> list[str]:
    """The full decomposition of a projection: discriminating hops, then redundant or unsupported ones
    (build_scrutiny_inputs.py). The early three-judge records stored their hops as `Traces` instead."""
    if "discriminating_hops" in proj:
        hops = [(h.get("clue") or "") for h in (proj.get("discriminating_hops") or [])]
        hops += [(h.get("clue") or "") for h in (proj.get("redundant_or_unsupported_hops") or [])]
    else:
        hops = [(t.get("clue") or "") for t in (proj.get("Traces") or [])]
    return [h for h in hops if h.strip()]


def projected_hops(proj: dict) -> list[dict]:
    """The grounded hops of a PROJECTABLE projection (build_projected_list.py)."""
    out = []
    for h in proj.get("discriminating_hops") or proj.get("hops") or proj.get("Traces") or []:
        ev = [{"doc_id": str(e.get("doc_id", "")).strip(), "snippet": e.get("snippet", "")}
              for e in (h.get("corpus_evidence") or []) if str(e.get("doc_id", "")).strip()]
        if h.get("clue"):
            out.append({"clue": h["clue"], "corpus_evidence": ev})
    return out


def union_hops(p1: dict, p2: dict | None) -> list[dict]:
    """The all-hops-grounded rule (build_all_grounded.py, build_coverage_final.py): hop i of pass 1's list is
    grounded when pass 1 or pass 2 (hop i, matched by position) found it supported; its documents are those of
    the pass(es) that supported it; it is redundant only when both passes judged it unnecessary. The clue
    text is pass 1's."""
    h2s = (p2 or {}).get("hops") or []
    out = []
    for i, h in enumerate(p1.get("hops") or []):
        hb = h2s[i] if i < len(h2s) else {}
        s1, s2 = bool(h.get("supported")), bool(hb.get("supported"))
        docs = list(dict.fromkeys(((h.get("doc_ids") or []) if s1 else []) + ((hb.get("doc_ids") or []) if s2 else [])))
        out.append({"index": i + 1, "clue": h.get("clue", ""), "grounded": s1 or s2,
                    "redundant": (h.get("necessary") is False) and (hb.get("necessary") is False),
                    "supporting_doc_ids": docs,
                    "note": (h.get("note") or "") if s1 else ((hb.get("note") or "") if s2 else (h.get("note") or hb.get("note") or "")),
                    "pass2_clue": hb.get("clue", "")})
    return out


def all_grounded(p1: dict, p2: dict | None, mode: str = "union") -> bool:
    """mode union (the rule that ran), pass1 (pass 1 alone) or both (both passes must agree)."""
    h2s = (p2 or {}).get("hops") or []
    hops = p1.get("hops") or []
    if not hops:
        return False
    for i, h in enumerate(hops):
        s1, s2 = bool(h.get("supported")), bool((h2s[i] if i < len(h2s) else {}).get("supported"))
        if not {"union": s1 or s2, "pass1": s1, "both": s1 and s2}[mode]:
            return False
    return True


def release_hops(hops: list[dict]) -> list[dict]:
    """The hop shape of the released candidate list: {clue, redundant, supporting_doc_ids}."""
    return [{"clue": h.get("clue", ""), "redundant": bool(h.get("redundant", False)),
             "supporting_doc_ids": list(h.get("supporting_doc_ids") or [])} for h in hops]


# ---------------------------------------------------------------------------------------------- validators
def _t(v, kind) -> bool:
    return {"str": isinstance(v, str), "bool": isinstance(v, bool), "int": isinstance(v, int) and not isinstance(v, bool),
            "list": isinstance(v, list), "dict": isinstance(v, dict)}[kind]


def _need(obj: dict, fields: dict, where: str, errs: list[str]) -> None:
    for k, kind in fields.items():
        if k not in obj:
            errs.append(f"{where}: missing field {k!r}")
        elif not _t(obj[k], kind):
            errs.append(f"{where}: {k!r} must be a {kind}")


def validate(step: str, qid: str, out: dict, inp: dict | None) -> list[str]:
    """Structural checks of one agent output against its input. Aggregate flags an agent reports are not
    trusted: bcp_build.py recomputes every decision from the per-hop fields, as the original builders did."""
    errs: list[str] = []
    if not isinstance(out, dict):
        return ["the output is not a JSON object"]
    idk = "record_id" if step == "project" else "qid"
    if str(out.get(idk, "")) != str(qid):
        errs.append(f"{idk} must be {qid!r}")
    n_in = len((inp or {}).get("hops") or [])
    if step == "project":
        _need(out, {"verdict": "str", "answer_uniquely_inferable": "bool", "discriminating_hops": "list",
                    "redundant_or_unsupported_hops": "list", "reasoning_path": "str", "rationale": "str"}, "output", errs)
        if out.get("verdict") not in VERDICTS:
            errs.append(f"verdict must be one of {VERDICTS}")
        for i, h in enumerate(out.get("discriminating_hops") or [], 1):
            _need(h, {"clue": "str", "corpus_evidence": "list"}, f"discriminating_hops[{i}]", errs)
            for j, e in enumerate(h.get("corpus_evidence") or [], 1):
                _need(e, {"doc_id": "str", "snippet": "str"}, f"discriminating_hops[{i}].corpus_evidence[{j}]", errs)
        for i, h in enumerate(out.get("redundant_or_unsupported_hops") or [], 1):
            _need(h, {"clue": "str", "status": "str"}, f"redundant_or_unsupported_hops[{i}]", errs)
            if h.get("status") not in ("redundant", "unsupported"):
                errs.append(f"redundant_or_unsupported_hops[{i}].status must be redundant or unsupported")
        if out.get("verdict") == "PROJECTABLE" and not decomposition(out):
            errs.append("a PROJECTABLE verdict needs at least one hop")
    elif step in ("scrutiny1", "scrutiny2"):
        f = {"n_hops": "int", "all_supported": "bool", "all_necessary": "bool", "keep": "bool", "hops": "list"}
        if step == "scrutiny2":
            f.update({"comprehensive": "bool", "missing_fact": "str"})
        _need(out, f, "output", errs)
        hops = out.get("hops") or []
        if len(hops) != n_in:
            errs.append(f"hops has {len(hops)} entries; the input lists {n_in} (one entry per input hop, same order)")
        for i, h in enumerate(hops, 1):
            _need(h, {"clue": "str", "supported": "bool", "necessary": "bool", "doc_ids": "list", "note": "str"}, f"hops[{i}]", errs)
            if h.get("supported") and not h.get("doc_ids"):
                errs.append(f"hops[{i}] is supported but lists no doc_ids")
    elif step == "coverage":
        _need(out, {"coverage_ok": "bool", "uncovered_phrases": "list", "keep": "bool", "reason": "str"}, "output", errs)
        if out.get("coverage_ok") != (not out.get("uncovered_phrases")):
            errs.append("coverage_ok must be true exactly when uncovered_phrases is empty")
    elif step == "external_audit":
        _need(out, {"all_hops_present": "bool", "chain_ok": "bool", "all_hops_grounded": "bool", "keep": "bool",
                    "reason": "str", "per_hop": "list"}, "output", errs)
        if len(out.get("per_hop") or []) != n_in:
            errs.append(f"per_hop has {len(out.get('per_hop') or [])} entries; the input lists {n_in}")
        for i, h in enumerate(out.get("per_hop") or [], 1):
            _need(h, {"clue": "str", "grounded": "bool", "doc_used": "str", "why": "str"}, f"per_hop[{i}]", errs)
    elif step == "repair":
        _need(out, {"repaired_all": "bool", "fixed": "list", "unfixable": "list"}, "output", errs)
        failing = {h["index"] for h in (inp or {}).get("hops") or [] if h.get("FAILING")}
        seen = set()
        for i, h in enumerate(out.get("fixed") or [], 1):
            _need(h, {"index": "int", "clue": "str", "doc_id": "str", "snippet": "str"}, f"fixed[{i}]", errs)
            seen.add(h.get("index"))
        for i, h in enumerate(out.get("unfixable") or [], 1):
            _need(h, {"index": "int", "clue": "str", "why": "str"}, f"unfixable[{i}]", errs)
            seen.add(h.get("index"))
        if inp is not None and seen != failing:
            errs.append(f"fixed + unfixable must name exactly the FAILING hops {sorted(failing)}; got {sorted(x for x in seen if x is not None)}")
        fixed = {h.get("index") for h in out.get("fixed") or []}
        if inp is not None and out.get("repaired_all") != (failing <= fixed):
            errs.append("repaired_all must be true exactly when every FAILING hop is in fixed")
    elif step == "strict_audit":
        _need(out, {"hops": "list", "uncovered_phrases": "list", "coverage_ok": "bool", "all_hops_grounded": "bool",
                    "qualifies": "bool", "reason": "str"}, "output", errs)
        if len(out.get("hops") or []) != n_in:
            errs.append(f"hops has {len(out.get('hops') or [])} entries; the input lists {n_in}")
        for i, h in enumerate(out.get("hops") or [], 1):
            _need(h, {"index": "int", "clue": "str", "verdict": "str", "doc_ids": "list", "snippet": "str",
                      "rescued": "bool"}, f"hops[{i}]", errs)
            if h.get("verdict") not in ("SUPPORTED", "PARTIAL", "UNSUPPORTED"):
                errs.append(f"hops[{i}].verdict must be SUPPORTED, PARTIAL or UNSUPPORTED")
    elif step == "rejudge":
        _need(out, {"hops": "list", "outcome": "str", "reason": "str"}, "output", errs)
        if out.get("outcome") not in ("survive", "fail_core", "fail_coverage"):
            errs.append("outcome must be survive, fail_core or fail_coverage")
        if len(out.get("hops") or []) != n_in:
            errs.append(f"hops has {len(out.get('hops') or [])} entries; the input lists {n_in}")
        for i, h in enumerate(out.get("hops") or [], 1):
            _need(h, {"index": "int", "clue": "str", "fully_grounded": "bool", "basis": "str", "missing_qualifier": "str",
                      "snippet": "str", "supporting_doc_ids": "list"}, f"hops[{i}]", errs)
    else:
        errs.append(f"unknown step {step!r}")
    return errs
