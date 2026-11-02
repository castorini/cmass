#!/usr/bin/env python3
"""DSQA expansion: the candidate selection that added DSQA questions the Stage 1 funnel had dropped, as a third
Stage 2 input set (inputs/<set>_expansion.jsonl). It ran later than the rest of Stage 1, during the Stage 2 audit,
once Stage 2 required every member of a set answer to be grounded (README.md, "Expansion").

  dsqa_expansion.py candidates   set answers of 2-5 members and single answers outside the Stage 2 inputs
                                 -> expansion/candidates.json, expansion/single_candidates.json
  dsqa_expansion.py sweep        one corpus search per set member (name + question keywords, 50 hits; resumable)
                                 -> expansion/presence.jsonl
  dsqa_expansion.py summary      per-question presence, ranked -> expansion/ranked.json, expansion/RANKED.md
  dsqa_expansion.py pilot        the set-answer pilot: up to 4 per drop reason in RANKED order, 15 in all
                                 -> expansion/pilot.json
  dsqa_expansion.py records      pilot sets + every single candidate -> <run>/inputs/<set>_expansion.jsonl

Candidates are the DSQA tasks outside the Stage 2 inputs (the final list and its near misses) whose original
projection got far enough: set answers that reached at least PARTIAL and split into 2-5 members, and single answers
that reached at least the released list. Set answers are swept first: a member is "present" when its cleaned name
occurs in one of the 50 documents its search returns, "in context" when such a document also contains a keyword
of the question. Members that are bare values (years, numbers, dates) are not searched. Presence is necessary,
not sufficient: the Stage 2 audit still grounds every constraint and every member from scratch.
Options: --set SET (default dsqa), --pilot N (15), --per-reason K (4). Run inside a run directory.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsqa_common import (ANSWER_TYPE, DEFAULT_SET, S1, SET, Run, corpus, extreme_why, load_json, nkey,  # noqa: E402
                         read_jsonl, write_json, write_jsonl)

HITS = 50
STAGE_ORDER = ["passed strict audit (170)", "released (258)", "projector SUPPORTED", "projector PARTIAL"]
# The drop-reason groups of RANKED.md, in its order (the pilot walks them in this order).
WHY_ORDER = ["uncovered clause only (source)", "uncovered clause only (other)", "ungrounded hop + uncovered clause",
             "ungrounded hop", "released (258)", "projector SUPPORTED", "projector PARTIAL"]
STOP = set("""a an the of in on at to for from by with and or not no than then that which who whom whose what when where
why how is are was were be been being has have had do does did this these those it its as per into over under
between among during before after above below up down out about any all each every both either neither other
such only own same so too very can will just should would could may might must also there their they them
list name give provide please according use using based source data year years number which ones one two
three four five more most less least many much top total including include included""".split())


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[‐-―−]", "-", s).replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", s).strip()


def members(answer: str) -> list[str]:
    """A set answer's members as the sweep split them: one per line when the answer has several lines, else split on
    ';' and on commas outside parentheses, with a final 'X and Y' split too."""
    a = str(answer).strip().rstrip(".")
    lines = [re.sub(r"^\s*(\*|-|\d+\.)\s*", "", ln).strip() for ln in a.splitlines() if ln.strip()]
    parts = lines if len(lines) > 1 else [p.strip() for p in re.split(r";|,(?![^()]*\))", a) if p.strip()]
    if parts and re.search(r"\band\b", parts[-1]) and not re.search(r"\(", parts[-1]):
        last = parts.pop()
        parts += [p.strip() for p in re.split(r"\band\b", last) if p.strip()]
    return [p for p in parts if p]


def clean_member(m: str) -> str:
    m = re.sub(r"\([^)]*\)", " ", m)        # parenthetical detail
    m = m.split(":")[0]                     # "<name>: <value>" keeps the name
    m = re.sub(r"\s+-\s+.*$", "", m)        # "<name> - <other name>" keeps the first name
    return re.sub(r"\s+", " ", m).strip(" .,'\"")


def is_value(m: str) -> bool:
    return bool(re.fullmatch(r"[\d\s.,%$€£–\-/:]+|(january|february|march|april|may|june|july|august|september|october|"
                             r"november|december)\s+\d{4}|\d{4}s?", m.strip().lower()))


def keywords(q: str, k: int = 4) -> list[str]:
    toks = [t for t in re.findall(r"[A-Za-z][A-Za-z'\-]{3,}", q) if t.lower() not in STOP]
    seen, out = set(), []
    for t in sorted(toks, key=len, reverse=True):
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:k]


def X(s1: S1) -> Path:
    return s1.path("expansion")


# ---------------------------------------------------------------------------------------------- candidates
def candidates(s1: S1):
    root = s1.run.root / "inputs"
    ins = [root / f"{s1.sid}.jsonl", root / f"{s1.sid}_near_misses.jsonl"]
    if not all(p.exists() for p in ins):
        raise SystemExit(f"the Stage 2 inputs are missing ({ins[0].name}, {ins[1].name}): run dsqa_build.py inputs first")
    ours = {r["record_id"] for p in ins for r in read_jsonl(p)}
    aud = set(s1.by_id("audited.jsonl"))
    rel = set(s1.by_id("released.jsonl"))
    proj, strict, ext = s1.outputs("project"), s1.outputs("strict"), s1.outputs("extreme")

    def stage_of(q):
        if q in ours:
            return "in our audit"
        if q in aud:
            return "passed strict audit (170)"
        if q in rel:
            return "released (258)"
        return f"projector {str((proj.get(q) or {}).get('verdict')).upper()}"

    def drop_reason(q, stage):
        """Why the original pipeline dropped it, from its own records."""
        if stage == "passed strict audit (170)":
            a = ext.get(q)
            return ("extreme audit: " + extreme_why(a)[:140]) if a is not None and not a.get("keep") else \
                "cut after the extreme audit (coherence, annotator or adjudication)"
        if stage == "released (258)":
            r = strict.get(q)
            if r is None:
                return "strict audit"
            f = r.get("failures") or r.get("reasons") or r.get("reason") or r.get("summary") or ""
            return "strict audit: " + (json.dumps(f, ensure_ascii=False) if not isinstance(f, str) else f)[:400]
        p = proj.get(q) or {}
        return "projector: " + str(p.get("reason") or p.get("failed_clues") or "")[:400]

    sets, singles = [], []
    for t in sorted(s1.run.tasks(s1.sid), key=lambda t: nkey(t["record_id"])):
        q, stage = t["record_id"], stage_of(t["record_id"])
        problem = t.get("question", "")
        if t.get("dsqa_answer_type") != SET:
            if stage in ("passed strict audit (170)", "released (258)"):
                singles.append({"qid": q, "stage": stage, "category": t.get("problem_category", ""), "question": problem,
                                "answer": t["answer"], "drop_reason": drop_reason(q, stage)})
            continue
        if stage not in STAGE_ORDER:
            continue
        ms = members(t["answer"])
        if not 2 <= len(ms) <= 5:
            continue
        reason = drop_reason(q, stage)
        sets.append({"qid": q, "stage": stage, "category": t.get("problem_category", ""), "question": problem,
                     "answer": t["answer"], "members": ms, "drop_reason": reason,
                     "source_clause": bool(re.search(r"source|attribution|according to|use .{0,40}\.(com|org|gov)|https?://",
                                                     reason + " " + problem, re.I)),
                     "keywords": keywords(problem)})
    write_json(X(s1) / "candidates.json", sets)
    write_json(X(s1) / "single_candidates.json", singles)
    s1.counts("expansion_candidates", {"set_candidates": len(sets), "by_stage": dict(Counter(c["stage"] for c in sets)),
                                       "single_candidates": len(singles),
                                       "single_by_stage": dict(Counter(c["stage"] for c in singles))})
    print(f"{len(sets)} set candidates {dict(Counter(c['stage'] for c in sets))}; {len(singles)} single candidates")


# ---------------------------------------------------------------------------------------------- sweep
def sweep(s1: S1):
    cands = load_json(X(s1) / "candidates.json")
    if cands is None:
        raise SystemExit("expansion/candidates.json is missing: run candidates first")
    cands.sort(key=lambda c: (STAGE_ORDER.index(c["stage"]), len(c["members"])))
    out = X(s1) / "presence.jsonl"
    done = {(r["qid"], r["member"]) for r in read_jsonl(out)}
    cp = corpus(s1.run)
    n = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a") as f:
        for c in cands:
            for m in c["members"]:
                if (c["qid"], m) in done:
                    continue
                name = clean_member(m)
                if not name or is_value(name):
                    f.write(json.dumps({"qid": c["qid"], "member": m, "name": name, "value": True}, ensure_ascii=False) + "\n")
                    continue
                query = " ".join([name] + c["keywords"])
                try:
                    hits = cp.search(query, HITS)
                except Exception as e:                  # a failed request is not an absence: leave it for the next run
                    print("no result for", c["qid"], name, str(e)[:200])
                    continue
                needle, kws = norm(name), [norm(k) for k in c["keywords"]]
                present, context, first = [], [], None
                for h in hits:
                    text = norm(cp.get_document(h["docid"]) or "")
                    if needle in text:
                        present.append(h["docid"])
                        first = first or h["rank"]
                        if any(k in text for k in kws):
                            context.append(h["docid"])
                f.write(json.dumps({"qid": c["qid"], "member": m, "name": name, "value": False, "query": query,
                                    "hits_read": len(hits), "present": bool(present), "in_context": bool(context),
                                    "first_rank": first, "n_present": len(present), "n_context": len(context),
                                    "docs": context[:5] or present[:5]}, ensure_ascii=False) + "\n")
                f.flush()
                n += 1
    s1.counts("expansion_sweep", {"rows": len(read_jsonl(out)), "searched_this_run": n})
    print(f"swept {n} members this run")


# ---------------------------------------------------------------------------------------------- summary, pilot
def why_of(s1: S1, r: dict, ext: dict) -> str:
    """The drop-reason group: for questions that passed the strict audit, how the extreme audit failed them (an
    uncovered clause is a source clause when an uncovered atom mentions a source, attribution or 'according to');
    otherwise the stage the question reached."""
    if r["stage"] != "passed strict audit (170)":
        return r["stage"]
    a = ext.get(r["qid"]) or {}
    g, cov = a.get("all_hops_grounded"), a.get("all_atoms_covered")
    if not g and not cov:
        return "ungrounded hop + uncovered clause"
    if not g:
        return "ungrounded hop"
    un = " ".join(map(str, a.get("uncovered_atoms") or []))
    return "uncovered clause only (source)" if re.search(r"source|attribution|according to", un, re.I) else \
        "uncovered clause only (other)"


def summary(s1: S1):
    cands = {c["qid"]: c for c in load_json(X(s1) / "candidates.json", [])}
    by = {}
    for r in read_jsonl(X(s1) / "presence.jsonl"):
        by.setdefault(r["qid"], {})[r["member"]] = r
    ext = s1.outputs("extreme")
    rows = []
    for q, c in cands.items():
        got = by.get(q, {})
        named = [got[m] for m in c["members"] if m in got and not got[m]["value"]]
        values = sum(1 for m in c["members"] if m in got and got[m]["value"])
        swept = len(named) + values == len(c["members"])
        rows.append({**{k: c[k] for k in ("qid", "stage", "category", "question", "answer", "drop_reason", "source_clause")},
                     "n_members": len(c["members"]), "value_members": values, "swept": swept,
                     "present": sum(r["present"] for r in named), "in_context": sum(r["in_context"] for r in named),
                     "named": len(named), "all_present": swept and all(r["present"] for r in named),
                     "all_in_context": swept and all(r["in_context"] for r in named)})
    rows.sort(key=lambda r: (not r["all_in_context"], not r["all_present"], STAGE_ORDER.index(r["stage"]),
                             -r["in_context"] / max(r["named"], 1), r["n_members"]))
    for r in rows:
        r["why"] = why_of(s1, r, ext)
    write_json(X(s1) / "ranked.json", rows)
    listed = sorted([r for r in rows if r["all_present"]], key=lambda r: (WHY_ORDER.index(r["why"]), r["n_members"]))
    lines = ["# DSQA expansion candidates (presence sweep)", "",
             f"{len(rows)} set answers of 2-5 members outside the Stage 2 inputs that reached at least PARTIAL in the "
             f"projection; {len(listed)} have every member present in the corpus (name found in one of {HITS} documents).", "",
             "| qid | why the original pipeline dropped it | members | question |", "|---|---|--:|---|"]
    for r in listed:
        lines.append(f"| {r['qid']} | {r['why']} | {r['n_members']} | {r['question'][:120].replace('|', '/')} |")
    rest = [r["qid"] for r in rows if not r["all_present"]]
    lines += ["", f"Not all members present ({len(rest)}): " + ", ".join(rest)]
    (X(s1) / "RANKED.md").write_text("\n".join(lines) + "\n")
    s1.counts("expansion_summary", {"candidates": len(rows), "swept": sum(r["swept"] for r in rows),
                                    "all_present": len(listed), "all_in_context": sum(r["all_in_context"] for r in rows),
                                    "by_why": dict(Counter(r["why"] for r in listed))})
    print(f"{len(rows)} candidates, {len(listed)} with every member present, "
          f"{sum(r['all_in_context'] for r in rows)} with every member in context")


def pilot(s1: S1, size: int = 15, per_reason: int = 4):
    """The set-answer pilot as run: walk RANKED.md's list (every member present; drop-reason groups in WHY_ORDER,
    fewer members first) and take up to per_reason questions from each group until size are chosen."""
    rows = load_json(X(s1) / "ranked.json")
    if rows is None:
        raise SystemExit("expansion/ranked.json is missing: run summary first")
    listed = sorted([r for r in rows if r["all_present"]], key=lambda r: (WHY_ORDER.index(r["why"]), r["n_members"]))
    out, per = [], Counter()
    for r in listed:
        if len(out) >= size:
            break
        if per[r["why"]] >= per_reason:
            continue
        per[r["why"]] += 1
        out.append({"qid": r["qid"], "why": r["why"], "n_members": r["n_members"]})
    write_json(X(s1) / "pilot.json", out)
    s1.counts("expansion_pilot", {"pilot": len(out), "by_why": dict(per)})
    print(f"pilot: {len(out)} set answers {dict(per)}")


# ---------------------------------------------------------------------------------------------- records
def records(s1: S1):
    """Stage 2 records without hops (the audit writes every hop as an added hop), carrying as leads what the original
    expansion packet showed: the drop reason, the projector's verdict, member, note and hops, and the presence sweep."""
    pil = load_json(X(s1) / "pilot.json")
    singles = load_json(X(s1) / "single_candidates.json")
    if pil is None or singles is None:
        raise SystemExit("expansion/pilot.json or single_candidates.json is missing")
    ranked = {r["qid"]: r for r in load_json(X(s1) / "ranked.json", [])}
    cands = {c["qid"]: c for c in load_json(X(s1) / "candidates.json", [])}
    pres = {}
    for r in read_jsonl(X(s1) / "presence.jsonl"):
        pres.setdefault(r["qid"], {})[r["member"]] = r
    tasks, proj = s1.tasks(), s1.outputs("project")
    order = [p["qid"] for p in pil] + [c["qid"] for c in singles]
    single_c = {c["qid"]: c for c in singles}
    out = []
    for q in order:
        t, p = tasks[q], proj.get(q) or {}
        is_set = t.get("dsqa_answer_type") == SET
        c = cands.get(q) if is_set else single_c[q]
        leads = {"why_dropped": (ranked.get(q) or {}).get("why") if is_set else c["stage"],
                 "drop_reason": c["drop_reason"],
                 "projection": {"verdict": p.get("verdict"), "derived_answer": p.get("derived_answer"),
                                "reason": p.get("reason") or "",
                                "hops": [{"clue": h.get("clue"), "grounded": h.get("grounded"),
                                          "doc_ids": (h.get("doc_ids") or [])[:6], "snippet": h.get("snippet", "")}
                                         for h in p.get("hops") or []]}}
        if is_set:
            leads["members_as_split"] = c["members"]
            leads["presence"] = [{"member": m, **({"value": True} if (pres.get(q, {}).get(m) or {}).get("value") else
                                                  {k: (pres.get(q, {}).get(m) or {}).get(k) for k in ("present", "in_context", "docs")})}
                                 for m in c["members"]]
        out.append({"record_id": q, "question": t.get("question", ""), "answer": t["answer"],
                    "answer_type": ANSWER_TYPE.get(t.get("dsqa_answer_type"), t.get("answer_type")), "hops": [],
                    "source": t.get("source"), "problem_category": t.get("problem_category", ""),
                    "dsqa_answer_type": t.get("dsqa_answer_type"), "derived_answer": p.get("derived_answer"),
                    "expansion_leads": leads})
    path = s1.run.root / "inputs" / f"{s1.sid}_expansion.jsonl"
    write_jsonl(path, out)
    s1.counts("expansion_records", {"records": len(out), "sets": len(pil), "singles": len(singles)})
    print(f"wrote {path} ({len(out)}: {len(pil)} set answers, {len(singles)} single answers)")
    print("cmass.json sets entry:", {f"xp_{s1.sid}": {"input": f"inputs/{s1.sid}_expansion.jsonl", "family": "dsqa",
                                                      "group": "released"}})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["candidates", "sweep", "summary", "pilot", "records"])
    ap.add_argument("--set", default=DEFAULT_SET)
    ap.add_argument("--pilot", type=int, default=15)
    ap.add_argument("--per-reason", type=int, default=4)
    a = ap.parse_args()
    s1 = S1(Run(), a.set)
    if a.command == "pilot":
        pilot(s1, a.pilot, a.per_reason)
    else:
        {"candidates": candidates, "sweep": sweep, "summary": summary, "records": records}[a.command](s1)


if __name__ == "__main__":
    main()
