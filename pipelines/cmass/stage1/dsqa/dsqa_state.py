#!/usr/bin/env python3
"""State of the DSQA Stage 1 agent steps: which questions each step must still process, their input packets, and
whether each output on disk is valid. The workflow (stage1.js) runs this before and after every agent, so it
resumes from disk and never trusts an agent's report.

  dsqa_state.py                              # every agent step: counts per status
  dsqa_state.py STEP [QID ...] [--set dsqa]  # one step: one line per question
  dsqa_state.py STEP ... --json [--pending]  # {"step", "questions": [{qid, status, packet, output, errors}]}

STEP is one of: project, strict, extreme, xcheck, polish, consolidate, coherence, annotator, adjudicate,
table_repair (README.md gives the order and what each one is). A question's status is
  pending       eligible, no output yet          invalid   an output exists and fails the checks (errors)
  done          a valid output exists            not_eligible   named on the command line but not eligible
Eligibility comes from the deterministic list the previous step wrote (dsqa_build.py) or, for adjudicate, from
the operator's flags.json. The packet (stage1/<set>/packets/<step>/<qid>.json) is rewritten for every eligible
question each time this runs; it holds exactly the fields the original agents read. xcheck (the extreme audit's
cross-check by a second model) has no default worklist: name its questions.
Run inside a run directory (or set $CMASS_RUN).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsqa_common import DEFAULT_SET, S1, Run, labels, load_json, nkey, polished, task_view, write_json  # noqa: E402

GUIDES = {"project": "PROJECT.md", "strict": "STRICT_AUDIT.md", "extreme": "EXTREME_AUDIT.md",
          "xcheck": "EXTREME_AUDIT.md", "polish": "POLISH.md", "consolidate": "CONSOLIDATE.md",
          "coherence": "COHERENCE.md", "annotator": "ANNOTATOR.md", "adjudicate": "ADJUDICATE.md",
          "table_repair": "TABLE_REPAIR.md"}
STEPS = list(GUIDES)


# ---------------------------------------------------------------------------------------------- eligibility
def eligible(s1: S1, step: str) -> tuple[list[str], str]:
    """(qids in order, blocking message). A missing upstream list blocks the step."""
    need = {"strict": ("released.jsonl", "dsqa_build.py gate"), "extreme": ("audited.jsonl", "dsqa_build.py audited"),
            "polish": ("extreme.jsonl", "dsqa_build.py extreme"),
            "consolidate": ("consolidate_targets.json", "dsqa_build.py consolidate-targets"),
            "coherence": ("evidence.jsonl", "dsqa_build.py evidence"),
            "annotator": ("after_coherence.json", "dsqa_build.py coherence"),
            "adjudicate": ("flags.json", "the operator's flag selection (README, step 9)"),
            "table_repair": ("verbatim.json", "dsqa_build.py verbatim")}
    if step == "project":
        return [t["record_id"] for t in s1.run.tasks(s1.sid)], ""
    if step == "xcheck":
        return [q for q in s1.outputs("extreme")], ""
    if step in need and not s1.path(need[step][0]).exists():
        return [], f"{need[step][0]} is missing: run {need[step][1]} first"
    if step == "strict":
        return list(s1.by_id("released.jsonl")), ""
    if step == "extreme":
        return list(s1.by_id("audited.jsonl")), ""
    if step == "polish":
        return list(s1.by_id("extreme.jsonl")), ""
    if step == "consolidate":
        return list((load_json(s1.path("consolidate_targets.json"), {}) or {}).get("targets", [])), ""
    if step == "coherence":
        return [r["qid"] for r in s1.jsonl("evidence.jsonl")], ""
    if step == "annotator":
        return s1.ids("after_coherence.json"), ""
    if step == "adjudicate":
        return sorted(load_json(s1.path("flags.json"), {}) or {}, key=nkey), ""
    if step == "table_repair":
        if not s1.path("applied.json").exists():
            return [], "applied.json is missing: run dsqa_build.py apply-fixes first"
        verb = load_json(s1.path("verbatim.json"), {}) or {}
        adj = s1.outputs("adjudicate")
        return [q for q in sorted(verb, key=nkey) if verb[q] and (adj.get(q) or {}).get("decision") == "keep_with_note"], ""
    raise SystemExit(f"unknown step {step!r}; one of {STEPS}")


# ---------------------------------------------------------------------------------------------- packets
def packets(s1: S1, step: str, qids: list[str]) -> dict:
    """{qid: packet} with the original input fields of each step."""
    out = {}
    if step == "project":
        tasks = s1.tasks()
        return {q: task_view(tasks[q]) for q in qids}
    if step == "strict":                                     # build_audit_inputs.py
        rel, proj = s1.by_id("released.jsonl"), s1.outputs("project")
        for q in qids:
            r = rel[q]
            out[q] = {"record_id": q, "answer_type": r["answer_type"], "problem_category": r["problem_category"],
                      "problem": r["problem"], "gold_answer": r["answer"], "claimed_answer": r["derived_answer"],
                      "self_reported_failed_clues": (proj.get(q) or {}).get("failed_clues") or [], "hops": r["hops"]}
        return out
    if step in ("extreme", "xcheck"):
        aud = s1.by_id("audited.jsonl")
        for q in qids:
            r = aud[q]
            out[q] = {"qid": q, "answer_type": r["answer_type"], "problem": r["problem"], "gold_answer": r["answer"],
                      "derived_answer": r["derived_answer"],
                      "hops": [{"index": i, "clue": h["clue"], "doc_ids": h["supporting_doc_ids"], "snippet": h["snippet"]}
                               for i, h in enumerate(r["hops"], 1)]}
        return out
    if step == "polish":
        ext = s1.by_id("extreme.jsonl")
        for q in qids:
            r = ext[q]
            out[q] = {"qid": q, "problem": r["problem"], "gold_answer": r["answer"], "derived_answer": r["derived_answer"],
                      "answer_type": r["answer_type"],
                      "hops": [{"index": i, "clue": h["clue"], "doc_ids": h["supporting_doc_ids"], "current_snippet": h["snippet"]}
                               for i, h in enumerate(r["hops"], 1)]}
        return out
    if step == "consolidate":
        ext, cur, lab = s1.by_id("extreme.jsonl"), polished(s1), labels(s1)
        for q in qids:
            r = ext[q]
            out[q] = {"qid": q, "problem": r["problem"], "gold_answer": r["answer"], "derived_answer": r["derived_answer"],
                      "hops": [{"index": i, "clue": h["clue"], "current_doc": cur[q][i]["doc_id"],
                                "current_snippet": cur[q][i]["snippet"], "verified_docs": h["supporting_doc_ids"],
                                "doc_titles": {d: (lab.get(d) or {}).get("label", "") for d in h["supporting_doc_ids"]}}
                               for i, h in enumerate(r["hops"], 1)]}
        return out
    if step in ("coherence", "annotator"):
        ev = {r["qid"]: r for r in s1.jsonl("evidence.jsonl")}
        return {q: ev[q] for q in qids}
    if step in ("adjudicate", "table_repair"):
        ext, app = s1.by_id("extreme.jsonl"), load_json(s1.path("applied.json"), {}) or {}
        flags, verb = load_json(s1.path("flags.json"), {}) or {}, load_json(s1.path("verbatim.json"), {}) or {}
        for q in qids:
            r = ext[q]
            p = {"qid": q, "problem": r["problem"], "gold_answer": r["answer"], "derived_answer": r["derived_answer"],
                 "answer_type": r["answer_type"], "hops": app.get(q, [])}
            if step == "adjudicate":
                p.update({"flagged_issues": flags.get(q, []), "snippets_not_verbatim": verb.get(q, [])})
            else:
                p["repair_hops"] = verb.get(q, [])
            out[q] = p
        return out
    raise SystemExit(f"unknown step {step!r}")


# ---------------------------------------------------------------------------------------------- validation
def _bools(o, keys, E):
    for k in keys:
        if not isinstance(o.get(k), bool):
            E(f"{k} must be true or false")


def _indices(o, n, E, what="hops"):
    got = sorted(h.get("index") for h in o.get(what) or [] if isinstance(h, dict) and isinstance(h.get("index"), int))
    if got != list(range(1, n + 1)):
        E(f"{what} must cover every hop index 1..{n} exactly once (got {got})")


def _strs(o, k, E):
    v = o.get(k, [])
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        E(f"{k} must be a list of strings")


def validate(step: str, o: dict, p: dict) -> list[str]:
    errs = []
    E = errs.append
    if not isinstance(o, dict):
        return ["the output is not a JSON object"]
    idk, qid = ("record_id", p["record_id"]) if step in ("project", "strict") else ("qid", p["qid"])
    if o.get(idk) != qid:
        E(f"{idk} must be {qid!r}")
    n = len(p.get("hops") or [])
    if step == "project":
        if o.get("answer_type") != p["answer_type"]:
            E(f"answer_type must be {p['answer_type']!r} (copied from the packet)")
        _bools(o, ("all_hops_grounded", "matches_gold"), E)
        if "complete" in o and not isinstance(o["complete"], bool):
            E("complete must be true or false")
        if o.get("verdict") not in ("SUPPORTED", "PARTIAL", "NOT"):
            E("verdict must be SUPPORTED, PARTIAL or NOT")
        for i, h in enumerate(o.get("hops") or [], 1):
            if not isinstance(h, dict) or not str(h.get("clue") or "").strip() or not isinstance(h.get("grounded"), bool):
                E(f"hop {i} needs a clue and grounded true/false")
            elif "doc_ids" in h:
                _strs(h, "doc_ids", lambda m, i=i: E(f"hop {i}: {m}"))
        if not isinstance(o.get("hops", []), list):
            E("hops must be a list")
        _strs(o, "failed_clues", E)
    elif step == "strict":
        _bools(o, ("constraints_covered", "all_grounded", "answer_ok", "member_valid", "keep"), E)
        if not errs and o["keep"] != all(o[k] for k in ("constraints_covered", "all_grounded", "answer_ok", "member_valid")):
            E("keep must equal constraints_covered AND all_grounded AND answer_ok AND member_valid")
        for i, h in enumerate(o.get("hops") or [], 1):
            if not isinstance(h, dict) or "clue" not in h or not isinstance(h.get("grounded"), bool):
                E(f"hop {i} needs clue and grounded true/false")
        _strs(o, "uncovered_constraints", E)
        _strs(o, "failures", E)
    elif step in ("extreme", "xcheck"):
        _bools(o, ("all_atoms_covered", "all_hops_grounded", "keep"), E)
        if errs:
            return errs
        if o["keep"] != (o["all_atoms_covered"] and o["all_hops_grounded"]):
            E("keep must equal all_atoms_covered AND all_hops_grounded")
        _indices(o, n, E)
        for h in o.get("hops") or []:
            if not isinstance(h.get("grounded"), bool) or not isinstance(h.get("required"), bool):
                E(f"hop {h.get('index')}: grounded and required must be true or false")
            elif h["grounded"] and (not h.get("doc_ids") or not str(h.get("snippet") or "").strip()):
                E(f"hop {h.get('index')} is grounded: give the doc_ids that ground it and a fresh verbatim snippet")
        bad = sorted(h.get("index") for h in o.get("hops") or [] if h.get("grounded") is False)
        if sorted(o.get("ungrounded_hops") or []) != bad:
            E(f"ungrounded_hops must list exactly the hops with grounded false ({bad})")
        atoms = o.get("atoms") or []
        if any(not isinstance(x, dict) or not isinstance(x.get("hop_index"), int) or not (0 <= x["hop_index"] <= n)
               for x in atoms):
            E(f"every atom needs hop_index 0 (uncovered) or 1..{n}")
        uncovered = bool(o.get("uncovered_atoms")) or any(x.get("hop_index") == 0 for x in atoms if isinstance(x, dict))
        if o["all_atoms_covered"] == uncovered:
            E("all_atoms_covered must be false exactly when some atom is uncovered (hop_index 0, listed in uncovered_atoms)")
    elif step in ("polish", "consolidate"):
        _indices(o, n, E)
        for h in o.get("hops") or []:
            if not str(h.get("doc_id") or "").strip() or not str(h.get("snippet") or "").strip():
                E(f"hop {h.get('index')} needs doc_id and snippet")
            if step == "consolidate" and not isinstance(h.get("changed"), bool):
                E(f"hop {h.get('index')}: changed must be true or false")
    elif step == "coherence":
        _bools(o, ("attribution_ok", "cross_hop_consistent", "jointly_justifies", "keep"), E)
        if not errs and o["keep"] != (o["attribution_ok"] and o["cross_hop_consistent"] and o["jointly_justifies"]):
            E("keep must equal attribution_ok AND cross_hop_consistent AND jointly_justifies")
        for f in o.get("failures") or []:
            if not isinstance(f, dict) or not f.get("pattern") or not f.get("detail"):
                E("every failure needs a pattern and a detail")
    elif step == "annotator":
        _bools(o, ("keep",), E)
        _indices(o, n, E)
        for h in o.get("hops") or []:
            if h.get("verdict") not in ("keep", "fix", "drop"):
                E(f"hop {h.get('index')}: verdict must be keep, fix or drop")
        if not errs and o["keep"] != all(h["verdict"] != "drop" for h in o["hops"]):
            E("keep must be false exactly when some hop's verdict is drop")
        for x in o.get("issues") or []:
            if not isinstance(x, dict) or not x.get("kind") or not x.get("detail"):
                E("every issue needs a kind and a detail")
    elif step == "adjudicate":
        if o.get("decision") not in ("drop", "keep_with_note"):
            E("decision must be drop or keep_with_note")
        if not str(o.get("rationale") or "").strip():
            E("rationale is required")
        if o.get("decision") == "keep_with_note" and not str(o.get("annotator_note") or "").strip():
            E("keep_with_note needs an annotator_note")
        _strs(o, "fatal_flags", E)
        _strs(o, "survivable_flags", E)
    elif step == "table_repair":
        want = sorted(p.get("repair_hops") or [])
        got = sorted(h.get("index") for h in o.get("hops") or [] if isinstance(h, dict))
        if got != want:
            E(f"hops must be exactly the repair hops {want} (got {got})")
        for h in o.get("hops") or []:
            if not isinstance(h.get("possible"), bool):
                E(f"hop {h.get('index')}: possible must be true or false")
            elif h["possible"] and (not str(h.get("doc_id") or "").strip() or not str(h.get("snippet") or "").strip()):
                E(f"hop {h.get('index')}: a possible repair needs doc_id and snippet")
            elif not h["possible"] and not str(h.get("note") or "").strip():
                E(f"hop {h.get('index')}: explain in note why no verbatim passage can carry it")
    return errs


# ---------------------------------------------------------------------------------------------- report
def state(s1: S1, step: str, qids: list[str] | None = None) -> dict:
    elig, blocked = eligible(s1, step)
    if step == "xcheck" and not qids:
        elig = []
    pk = packets(s1, step, elig) if elig else {}
    rows = []
    for q in (qids or elig):
        out = s1.out(step, q)
        row = {"qid": q, "status": "", "packet": str(s1.packet(step, q)), "output": str(out), "errors": []}
        if q not in pk:
            row["status"] = "not_eligible"
            rows.append(row)
            continue
        if load_json(s1.packet(step, q)) != pk[q]:
            write_json(s1.packet(step, q), pk[q])
        if not out.exists():
            row["status"] = "pending"
        else:
            o = load_json(out)
            row["errors"] = ["the output is not valid JSON"] if o is None else validate(step, o, pk[q])
            row["status"] = "invalid" if row["errors"] else "done"
        rows.append(row)
    warn = []
    if step == "adjudicate" and s1.path("verbatim.json").exists():
        flags = load_json(s1.path("flags.json"), {}) or {}
        verb = load_json(s1.path("verbatim.json"), {}) or {}
        warn = [f"{q} has snippets that are not verbatim but is not in flags.json" for q in verb if verb[q] and q not in flags]
    return {"step": step, "set": s1.sid, "guide": GUIDES[step], "blocked": blocked, "warnings": warn, "questions": rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", nargs="?", choices=STEPS)
    ap.add_argument("qids", nargs="*")
    ap.add_argument("--set", default=DEFAULT_SET)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pending", action="store_true", help="only questions whose status is pending or invalid")
    a = ap.parse_args()
    s1 = S1(Run(), a.set)
    if not a.step:
        for step in STEPS:
            st = state(s1, step)
            c = Counter(r["status"] for r in st["questions"])
            print(f"{step:<13} " + (f"blocked: {st['blocked']}" if st["blocked"] and not c else
                                    ", ".join(f"{k} {n}" for k, n in sorted(c.items())) or "nothing eligible"))
        return
    st = state(s1, a.step, a.qids or None)
    if a.pending:
        st["questions"] = [r for r in st["questions"] if r["status"] in ("pending", "invalid")]
    if a.json:
        print(json.dumps(st, ensure_ascii=False))
        return
    if st["blocked"]:
        print("blocked:", st["blocked"])
    for w in st["warnings"]:
        print("warning:", w)
    for r in st["questions"]:
        print(f"{r['qid']:<14} {r['status']:<12} {'; '.join(r['errors'])[:150]}")
    c = Counter(r["status"] for r in st["questions"])
    print(f"({len(st['questions'])} questions) " + ", ".join(f"{k} {n}" for k, n in sorted(c.items())))


if __name__ == "__main__":
    main()
