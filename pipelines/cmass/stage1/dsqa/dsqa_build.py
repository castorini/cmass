#!/usr/bin/env python3
"""The deterministic steps of DSQA Stage 1, in the order they were run (README.md). Each reads agent outputs and
earlier lists from <run>/stage1/<set>/ and writes the next list; none trusts an agent's verdict beyond the fields
named below, and every snippet check reads the document text itself.

  dsqa_build.py gate                 project/ -> supported.jsonl, released.jsonl      (consistency gate)
  dsqa_build.py audited              strict/ -> audited.jsonl                          (strict audit keeps)
  dsqa_build.py extreme              extreme/ -> extreme.jsonl                         (extreme keeps, required hops)
  dsqa_build.py xcheck               xcheck/ against extreme/: agreement report         (optional)
  dsqa_build.py polish-verify        polish/ -> polish_verification.json               (verbatim, light repair)
  dsqa_build.py labels               doc_labels.json                                   (each cited document's title line)
  dsqa_build.py consolidate-targets  consolidate_targets.json                          (who may share documents)
  dsqa_build.py consolidate-verify   consolidate/ -> consolidation_verification.json
  dsqa_build.py evidence             evidence.jsonl                                    (hop evidence after polish + consolidation)
  dsqa_build.py coherence            coherence/ -> after_coherence.json
  dsqa_build.py apply-fixes          annotator/ -> after_annotator.json, applied.json, fix_verification.json
  dsqa_build.py verbatim             verbatim.json                                     (snippets not verbatim)
  dsqa_build.py issues               issues.json                                       (for the operator's flag selection)
  dsqa_build.py final                adjudicate/, table_repair/, table_decisions.json -> final.jsonl
  dsqa_build.py verify-final         final_snippet_failures.json (exit 1 on any failure)
  dsqa_build.py near-misses          near_misses.jsonl
  dsqa_build.py inputs               <run>/inputs/<set>.jsonl, <run>/inputs/<set>_near_misses.jsonl  (Stage 2 input)

Options: --set SET (default dsqa). Run inside a run directory (or set $CMASS_RUN). Every command also records its
aggregate counts in stage1/<set>/counts.json.
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsqa_common import (ANSWER_TYPE, DEFAULT_SET, S1, SET, SINGLE, Run, contained, corpus, labels, load_json,  # noqa: E402
                         nkey, norm, polished, write_json, write_jsonl)

TYPES = (SINGLE, SET)


def by_type(rows, key="answer_type"):
    c = Counter(r.get(key) for r in rows)
    return {t: c[t] for t in TYPES}


def need(s1: S1, *names):
    for n in names:
        if not s1.path(n).exists():
            raise SystemExit(f"{s1.path(n)} is missing: run the step that writes it first (README.md)")


# ---------------------------------------------------------------------------------------------- 2. consistency gate
def gate(s1: S1):
    """build_dsqa_final.py: keep a self-reported SUPPORTED projection only if it lists at least one hop,
    matches_gold is true, and every listed hop is grounded with at least one doc id and a non-empty snippet."""
    rows = list(s1.outputs("project").values())
    gold = s1.tasks()

    def consistent(r):
        h = r.get("hops") or []
        return bool(h) and bool(r.get("matches_gold")) and all(
            x.get("grounded") and (x.get("doc_ids") or []) and (x.get("snippet") or "").strip() for x in h)

    def record(r):
        g = gold.get(r["record_id"], {})
        return {"record_id": r["record_id"], "answer_type": r.get("answer_type", ""),
                "problem_category": g.get("problem_category", ""), "problem": g.get("question", ""),
                "answer": g.get("answer", ""), "derived_answer": r.get("derived_answer", ""),
                "n_hops": len(r.get("hops") or []),
                "hops": [{"clue": h.get("clue", ""), "supporting_doc_ids": h.get("doc_ids") or [],
                          "snippet": h.get("snippet", "")} for h in (r.get("hops") or [])]}

    sup = [r for r in rows if r.get("verdict") == "SUPPORTED"]
    keep = [r for r in sup if consistent(r)]
    write_jsonl(s1.path("supported.jsonl"), [record(r) for r in sorted(sup, key=lambda x: nkey(x["record_id"]))])
    write_jsonl(s1.path("released.jsonl"), [record(r) for r in sorted(keep, key=lambda x: nkey(x["record_id"]))])
    bad = [r for r in sup if not consistent(r)]
    v = Counter(r.get("verdict") for r in rows)
    c = {"judged": len(rows), "of_tasks": len(gold), "SUPPORTED": v["SUPPORTED"], "PARTIAL": v["PARTIAL"], "NOT": v["NOT"],
         "released": len(keep), "supported_by_type": by_type(sup), "released_by_type": by_type(keep),
         "gate_dropped": len(bad)}
    why = Counter("no hops or no gold match" if not r.get("hops") or not r.get("matches_gold") else
                  "lists a hop it marked ungrounded" if any(not x.get("grounded") for x in r["hops"]) else
                  "a grounded hop without a doc id or snippet" for r in bad)
    c["gate_dropped_by_reason"] = dict(why)
    s1.counts("gate", c)
    print(f"judged={len(rows)}/{len(gold)} SUPPORTED={len(sup)} PARTIAL={v['PARTIAL']} NOT={v['NOT']} "
          f"released={len(keep)} (Single {c['released_by_type'][SINGLE]}, Set {c['released_by_type'][SET]})")


# ---------------------------------------------------------------------------------------------- 3. strict audit
def audited(s1: S1):
    """build_dsqa_audited.py: keep a released question only when its strict audit kept it (keep is checked to equal
    the AND of the four checks by dsqa_state.py); hops come from the audit, which may cite replacement documents.
    Audits of ids outside the released list are ignored."""
    need(s1, "released.jsonl")
    rel = s1.jsonl("released.jsonl")
    runs = s1.outputs("strict")
    kept, dropped, unaudited = [], [], []
    for r in rel:
        a = runs.get(r["record_id"])
        if a is None:
            unaudited.append(r["record_id"])
            continue
        if not a.get("keep"):
            dropped.append(r["record_id"])
            continue
        hops = [{"clue": h.get("clue", ""), "supporting_doc_ids": h.get("doc_ids") or [], "snippet": h.get("snippet", "")}
                for h in (a.get("hops") or [])]
        if not hops:                                   # kept but no hop list: fall back to the released hops
            hops = r["hops"]
        kept.append({**{k: r[k] for k in ("record_id", "answer_type", "problem_category", "problem", "answer",
                                          "derived_answer")}, "n_hops": len(hops), "hops": hops})
    kept.sort(key=lambda x: nkey(x["record_id"]))
    write_jsonl(s1.path("audited.jsonl"), kept)
    aud = [runs[r["record_id"]] for r in rel if r["record_id"] in runs]
    fails = {k: sum(1 for a in aud if not a.get(k)) for k in ("constraints_covered", "all_grounded", "answer_ok", "member_valid")}
    ignored = sorted(set(runs) - {r["record_id"] for r in rel}, key=nkey)
    c = {"released": len(rel), "audited": len(aud), "kept": len(kept), "dropped": len(dropped), "unaudited": len(unaudited),
         "kept_by_type": by_type(kept), "failed_check": fails, "ignored_audits_outside_released": len(ignored),
         "median_hops": statistics.median([r["n_hops"] for r in kept]) if kept else 0,
         "distinct_docs": len({d for r in kept for h in r["hops"] for d in h["supporting_doc_ids"]})}
    s1.counts("audited", c)
    print(f"audited={len(aud)}/{len(rel)} keep={len(kept)} dropped={len(dropped)} unaudited={len(unaudited)} fails={fails}")


# ---------------------------------------------------------------------------------------------- 4. extreme audit
def extreme(s1: S1):
    """build_extreme_final.py: keep a question iff its extreme audit kept it (every atom covered, every hop grounded);
    the record carries REQUIRED hops only, with the auditor's re-verified doc ids and fresh snippets and the audited
    list's clue for that index. Confirmatory hops are verified but omitted."""
    need(s1, "audited.jsonl")
    aud = s1.by_id("audited.jsonl")
    runs = s1.outputs("extreme")
    kept, dropped = [], []
    for qid in sorted(aud, key=nkey):
        a = runs.get(qid)
        if a is None:
            continue
        src = aud[qid]
        if not a.get("keep"):
            dropped.append((qid, a))
            continue
        hops = []
        for h in a.get("hops") or []:
            if not h.get("required"):
                continue
            clue = src["hops"][h["index"] - 1]["clue"] if h["index"] - 1 < len(src["hops"]) else ""
            hops.append({"clue": clue, "supporting_doc_ids": h.get("doc_ids") or [], "snippet": h.get("snippet", "")})
        kept.append({"record_id": qid, "answer_type": src["answer_type"], "problem_category": src["problem_category"],
                     "problem": src["problem"], "answer": src["answer"], "derived_answer": src["derived_answer"],
                     "n_hops": len(hops), "hops": hops})
    write_jsonl(s1.path("extreme.jsonl"), kept)
    n_conf = sum(1 for a in runs.values() if a.get("keep") for h in a.get("hops") or [] if not h.get("required"))
    c = {"input": len(aud), "judged": len([q for q in aud if q in runs]), "kept": len(kept), "dropped": len(dropped),
         "dropped_ungrounded_hop": sum(1 for _, a in dropped if not a.get("all_hops_grounded")),
         "dropped_uncovered_atom": sum(1 for _, a in dropped if not a.get("all_atoms_covered")),
         "required_hops_released": sum(r["n_hops"] for r in kept), "confirmatory_hops_omitted": n_conf,
         "kept_by_type": by_type(kept)}
    s1.counts("extreme", c)
    print(f"judged={c['judged']}/{len(aud)} kept={len(kept)} dropped={len(dropped)} "
          f"(ungrounded {c['dropped_ungrounded_hop']}, uncovered {c['dropped_uncovered_atom']}) required hops={c['required_hops_released']}")


def xcheck(s1: S1):
    """Agreement between the extreme audit and its cross-check (the same prompt re-run by a second model)."""
    a, b = s1.outputs("extreme"), s1.outputs("xcheck")
    both = [q for q in b if q in a]
    verdict = sum(a[q].get("keep") == b[q].get("keep") for q in both)
    flags = sum(a[q].get("all_atoms_covered") == b[q].get("all_atoms_covered") and
                a[q].get("all_hops_grounded") == b[q].get("all_hops_grounded") for q in both)
    s1.counts("xcheck", {"rejudged": len(both), "verdict_agree": verdict, "both_flags_agree": flags})
    print(f"cross-checked {len(both)}: verdicts agree {verdict}/{len(both)}, both gate flags agree {flags}/{len(both)}")


# ---------------------------------------------------------------------------------------------- 5. evidence presentation
def polish_verify(s1: S1):
    """verify_snippets.py: every polished snippet must occur verbatim (whitespace normalised; fragments joined by
    ' ... ') in the document it names. A snippet off only by the trailing punctuation an agent changed to close a
    sentence is repaired; any other failure falls back to the extreme audit's snippet for that hop (dsqa_common.polished).
    The document is not required to be one the extreme audit verified for the hop (as run)."""
    need(s1, "extreme.jsonl")
    cp = corpus(s1.run)
    status, repaired = {}, {}
    ext = s1.by_id("extreme.jsonl")
    for q, d in s1.outputs("polish").items():
        if q not in ext:
            continue
        for h in d.get("hops") or []:
            key = f"{q}/{h['index']}"
            doc = norm(cp.get_document(h["doc_id"]) or "")
            if not doc:
                status[key] = "failed:no-doc"
                continue
            if contained(h["snippet"], doc):
                status[key] = "ok"
                continue
            s, fixed = norm(h["snippet"]), None
            for cand in (s[:-1], s[:-1] + ",", s.rstrip(".") + ","):
                if contained(cand, doc):
                    fixed = cand
                    break
            if fixed:
                status[key], repaired[key] = "repaired", fixed
            else:
                status[key] = "failed"
    write_json(s1.path("polish_verification.json"), {"status": status, "repaired": repaired})
    c = Counter(v.split(":")[0] for v in status.values())
    s1.counts("polish_verify", {"snippets": len(status), **dict(c)})
    print("verified", len(status), "snippets:", dict(c))


NAV = re.compile(r"^(topics menu|you are here|skip to|menu|home|search|navigation|breadcrumb|share this|"
                 r"français|english|sign in|log in|subscribe|advertisement|cookie)", re.I)


def doc_labels(s1: S1):
    """build_doc_labels.py: a document's label is its own first substantive line (navigation boilerplate and lines
    under 12 characters skipped); up to six such leading lines are kept to detect two shards of one page."""
    need(s1, "extreme.jsonl")
    cp = corpus(s1.run)
    lab = labels(s1)
    docs = {d for r in s1.jsonl("extreme.jsonl") for h in r["hops"] for d in h["supporting_doc_ids"]}
    docs |= {h["doc_id"] for d in s1.outputs("polish").values() for h in d.get("hops") or [] if h.get("doc_id")}
    todo = sorted(d for d in docs if d not in lab or not lab[d].get("lines"))
    for d in todo:
        text = cp.get_document(d) or ""
        keep = []
        for line in [x.strip() for x in text.split("\n") if x.strip()][:14]:
            if NAV.match(line) or len(line) < 12:
                continue
            keep.append(re.sub(r"\s+", " ", line)[:120])
            if len(keep) == 6:
                break
        lab[d] = {"label": keep[0][:88] if keep else "", "lines": keep}
    write_json(s1.path("doc_labels.json"), lab)
    s1.counts("labels", {"documents": len(docs), "labelled": sum(1 for d in docs if lab[d].get("label"))})
    print(f"{len(docs)} distinct documents, {len(todo)} newly labelled")


def consolidate_targets(s1: S1):
    """Which questions the consolidation agents see (as run): a question with a hop whose verified documents
    include another hop's current document ("simple": the hop could switch to it), or whose hops rest on two
    different documents that are shards of one page (a shared leading content line, "same_page")."""
    need(s1, "extreme.jsonl", "doc_labels.json")
    cur, lab = polished(s1), labels(s1)
    lines = {k: set(v.get("lines") or []) for k, v in lab.items()}
    simple, same = [], []
    for q, r in s1.by_id("extreme.jsonl").items():
        curd = {i: cur[q][i]["doc_id"] for i in range(1, len(r["hops"]) + 1)}
        hs = []
        for i, h in enumerate(r["hops"], 1):
            others = {curd[j] for j in curd if j != i}
            sw = [d for d in h["supporting_doc_ids"] if d in others and d != curd[i]]
            if sw:
                hs.append({"index": i, "current": curd[i], "switch_to": sw})
        if hs:
            simple.append({"qid": q, "hops": hs})
        docs = sorted(set(curd.values()))
        pair = {x for a in docs for b in docs if a < b and lines.get(a) and lines.get(b) and lines[a] & lines[b] for x in (a, b)}
        if pair:
            same.append({"qid": q, "docs": sorted(pair), "hops": sorted(i for i, d in curd.items() if d in pair)})
    targets = sorted({x["qid"] for x in simple} | {x["qid"] for x in same}, key=nkey)
    write_json(s1.path("consolidate_targets.json"), {"simple": simple, "same_page": same, "targets": targets})
    s1.counts("consolidate_targets", {"simple": len(simple), "same_page": len(same), "targets": len(targets)})
    print(f"consolidation targets: {len(targets)} ({len(simple)} with a switchable hop, {len(same)} with shards of one page)")


def consolidate_verify(s1: S1):
    """verify_consolidated.py: a consolidated hop must cite a document the extreme audit verified for that hop and
    its snippet must occur there verbatim; otherwise it reverts to its pre-consolidation evidence. Outputs for
    questions that were not targets have no input and are discarded."""
    need(s1, "consolidate_targets.json")
    targets = set((load_json(s1.path("consolidate_targets.json"), {}) or {}).get("targets", []))
    allowed = {r["record_id"]: {i: set(h["supporting_doc_ids"]) for i, h in enumerate(r["hops"], 1)}
               for r in s1.jsonl("extreme.jsonl")}
    cp = corpus(s1.run)
    res = {}
    for q, d in s1.outputs("consolidate").items():
        if q not in targets:
            res[f"{q}/*"] = "discarded:no-input"
            continue
        for h in d.get("hops") or []:
            key = f"{q}/{h['index']}"
            if h.get("doc_id") not in allowed.get(q, {}).get(h["index"], set()):
                res[key] = "reverted:doc-not-audit-verified"
            elif contained(h.get("snippet", ""), cp.get_document(h["doc_id"]) or ""):
                res[key] = "moved" if h.get("changed") else "kept"
            else:
                res[key] = "reverted:not-verbatim"
    write_json(s1.path("consolidation_verification.json"), res)
    c = Counter(v.split(":")[0] for v in res.values())
    s1.counts("consolidate_verify", {"hops": len(res), **dict(c), **{k: v for k, v in Counter(res.values()).items() if ":" in k}})
    print("consolidated hops checked:", len(res), dict(c))


def evidence(s1: S1):
    """The evidence the coherence and annotator agents read (as run): per hop, the consolidated document and snippet
    where consolidation verified (moved or kept), else the polished one where it verified, else the extreme audit's;
    with the document's label and every document the extreme audit verified for the hop."""
    need(s1, "extreme.jsonl")
    cur, lab = polished(s1), labels(s1)
    con = s1.outputs("consolidate")
    cv = load_json(s1.path("consolidation_verification.json"), {}) or {}
    out = []
    for r in s1.jsonl("extreme.jsonl"):
        q = r["record_id"]
        ch = {h.get("index"): h for h in (con.get(q) or {}).get("hops") or []}
        hops = []
        for i, h in enumerate(r["hops"], 1):
            c = ch.get(i)
            if c is not None and cv.get(f"{q}/{i}") in ("moved", "kept"):
                d, s = c["doc_id"], c["snippet"]
            else:
                d, s = cur[q][i]["doc_id"], cur[q][i]["snippet"]
            hops.append({"index": i, "clue": h["clue"], "doc_id": d, "doc_title": (lab.get(d) or {}).get("label", ""),
                         "snippet": s, "all_verified_docs": h["supporting_doc_ids"]})
        out.append({"qid": q, "answer_type": r["answer_type"], "problem": r["problem"], "gold_answer": r["answer"],
                    "derived_answer": r["derived_answer"], "hops": hops})
    write_jsonl(s1.path("evidence.jsonl"), out)
    docs = [len({h["doc_id"] for h in r["hops"]}) for r in out]
    before = [len({h["doc_id"] for h in cur[r["qid"]].values()}) for r in out]
    s1.counts("evidence", {"questions": len(out), "hops": sum(len(r["hops"]) for r in out),
                           "documents_shown_before_consolidation": sum(before), "documents_shown": sum(docs),
                           "single_document_questions_before": sum(1 for n in before if n == 1),
                           "single_document_questions": sum(1 for n in docs if n == 1)})
    print(f"evidence for {len(out)} questions: {sum(before)} documents shown before consolidation, {sum(docs)} after")


# ---------------------------------------------------------------------------------------------- 6-7. coherence, annotator
def coherence(s1: S1):
    need(s1, "evidence.jsonl")
    ids = [r["qid"] for r in s1.jsonl("evidence.jsonl")]
    runs = s1.outputs("coherence")
    missing = [q for q in ids if q not in runs]
    if missing:
        raise SystemExit(f"{len(missing)} questions have no coherence output yet (dsqa_state.py coherence)")
    keep = [q for q in ids if runs[q].get("keep")]
    write_json(s1.path("after_coherence.json"), keep)
    pats = Counter(p for q in ids if not runs[q].get("keep") for p in {f.get("pattern") for f in runs[q].get("failures") or []})
    s1.counts("coherence", {"judged": len(ids), "kept": len(keep), "dropped": len(ids) - len(keep),
                            "dropped_by_pattern_questions": dict(pats.most_common()),
                            "new_pattern_reported": sum(1 for q in ids if str(runs[q].get("new_pattern") or "").strip())})
    print(f"coherence: {len(keep)}/{len(ids)} kept; patterns among the dropped: {dict(pats.most_common())}")


def apply_fixes(s1: S1):
    """apply_fixes.py: an annotator repair is applied only where it verifies: its document must be one the extreme
    audit verified for the hop and its snippet must occur there verbatim. A reworded clue rides with verified
    evidence only. Questions with a 'drop' hop (keep false) are out."""
    need(s1, "after_coherence.json", "evidence.jsonl")
    ids = s1.ids("after_coherence.json")
    runs = s1.outputs("annotator")
    missing = [q for q in ids if q not in runs]
    if missing:
        raise SystemExit(f"{len(missing)} questions have no annotator output yet (dsqa_state.py annotator)")
    allowed = {r["record_id"]: {i: set(h["supporting_doc_ids"]) for i, h in enumerate(r["hops"], 1)}
               for r in s1.jsonl("extreme.jsonl")}
    ev = {r["qid"]: {h["index"]: h for h in r["hops"]} for r in s1.jsonl("evidence.jsonl")}
    keep_q = [q for q in ids if runs[q].get("keep")]
    cp = corpus(s1.run)
    applied, verif = {}, {}
    for q in keep_q:
        out = []
        for h in runs[q].get("hops") or []:
            i = h["index"]
            p = ev[q].get(i, {})
            clue, docid, snip = p.get("clue", ""), p.get("doc_id", ""), p.get("snippet", "")
            if h.get("verdict") == "fix":
                nd, ns = h.get("doc_id") or docid, h.get("snippet") or snip
                if nd in allowed.get(q, {}).get(i, set()) and contained(ns, cp.get_document(nd) or ""):
                    docid, snip = nd, ns
                    verif[f"{q}/{i}"] = "fix-applied"
                    if h.get("clue"):
                        clue = h["clue"]
                else:
                    verif[f"{q}/{i}"] = "fix-rejected"
            else:
                verif[f"{q}/{i}"] = h.get("verdict", "keep")
            out.append({"index": i, "clue": clue, "doc_id": docid, "snippet": snip,
                        "all_verified_docs": sorted(allowed.get(q, {}).get(i, set()))})
        applied[q] = out
    write_json(s1.path("after_annotator.json"), keep_q)
    write_json(s1.path("applied.json"), applied)
    write_json(s1.path("fix_verification.json"), verif)
    hv = Counter(h["verdict"] for q in ids for h in runs[q].get("hops") or [])
    s1.counts("annotator", {"judged": len(ids), "kept": len(keep_q), "dropped": len(ids) - len(keep_q),
                            "hop_verdicts_all": dict(hv), "survivor_hop_outcomes": dict(Counter(verif.values()))})
    print(f"annotator: {len(keep_q)}/{len(ids)} kept | hop outcomes among survivors: {dict(Counter(verif.values()))}")


def verbatim(s1: S1):
    """The adjudication inputs' snippets_not_verbatim: hops of the surviving questions whose snippet does not occur
    verbatim in the document it cites."""
    need(s1, "applied.json")
    cp = corpus(s1.run)
    res = {}
    for q, hs in (load_json(s1.path("applied.json"), {}) or {}).items():
        bad = [h["index"] for h in hs if not contained(h["snippet"], cp.get_document(h["doc_id"]) or "")]
        if bad:
            res[q] = bad
    write_json(s1.path("verbatim.json"), res)
    s1.counts("verbatim", {"questions_with_non_verbatim_snippets": len(res), "hops": sum(len(v) for v in res.values())})
    print(f"{len(res)} question(s) with snippets that are not verbatim: {sum(len(v) for v in res.values())} hop(s)")


def issues(s1: S1):
    """Every issue the annotator agents recorded for the surviving questions, for the operator's manual selection of
    serious flags (README, step 9). Writes issues.json; the operator writes flags.json from it."""
    need(s1, "after_annotator.json")
    runs = s1.outputs("annotator")
    verb = load_json(s1.path("verbatim.json"), {}) or {}
    out = {q: {"issues": runs[q].get("issues") or [], "snippets_not_verbatim": verb.get(q, [])}
           for q in s1.ids("after_annotator.json")}
    write_json(s1.path("issues.json"), out)
    print(f"issues.json: {sum(len(v['issues']) for v in out.values())} issues over {len(out)} questions; "
          f"{sum(1 for v in out.values() if v['snippets_not_verbatim'])} with snippets that are not verbatim")


# ---------------------------------------------------------------------------------------------- 10-11. final list
def final(s1: S1):
    """The final list (as run): the questions that survived the annotator pass, minus those the adjudication dropped
    and those the operator dropped after the table-quote repair (table_decisions.json); hops carry the applied
    evidence: the shown document first, then every other document the extreme audit verified for the hop; a question
    kept with a note carries it as annotator_note."""
    need(s1, "applied.json", "after_annotator.json")
    applied = load_json(s1.path("applied.json"), {}) or {}
    flags = load_json(s1.path("flags.json"), None)
    if flags is None:
        raise SystemExit("flags.json is missing: the operator's flag selection comes first (README, step 9); "
                         "write {} if no question is flagged")
    adj = s1.outputs("adjudicate")
    missing = [q for q in flags if q not in adj]
    if missing:
        raise SystemExit(f"{len(missing)} flagged question(s) have no adjudication yet (dsqa_state.py adjudicate)")
    verb = load_json(s1.path("verbatim.json"), {}) or {}
    repair_q = [q for q in verb if verb[q] and (adj.get(q) or {}).get("decision") == "keep_with_note"]
    reps = s1.outputs("table_repair")
    dec = load_json(s1.path("table_decisions.json"), {}) or {}
    for q in repair_q:
        if q not in reps:
            raise SystemExit(f"{q}: no table-quote repair yet (dsqa_state.py table_repair)")
        if (dec.get(q) or {}).get("decision") not in ("drop", "keep"):
            raise SystemExit(f"{q}: table_decisions.json needs the operator's decision (drop or keep, with a reason)")
    drop = {q for q, a in adj.items() if a.get("decision") == "drop" and q in flags}
    drop |= {q for q in repair_q if dec[q]["decision"] == "drop"}
    cp = corpus(s1.run)
    for q in repair_q:
        if dec[q]["decision"] != "keep":
            continue
        fixes = {h["index"]: h for h in reps[q].get("hops") or []}
        for h in applied[q]:
            f = fixes.get(h["index"])
            if f is None:
                continue
            if not f.get("possible") or not contained(f.get("snippet", ""), cp.get_document(f.get("doc_id", "")) or ""):
                raise SystemExit(f"{q}/{h['index']}: kept, but its repair is impossible or not verbatim")
            h["doc_id"], h["snippet"] = f["doc_id"], f["snippet"]
    ext = s1.by_id("extreme.jsonl")
    rows = []
    for q in sorted(applied, key=nkey):
        if q in drop:
            continue
        e = ext[q]
        hops = [{"clue": h["clue"], "supporting_doc_ids": [h["doc_id"]] + [d for d in h["all_verified_docs"] if d != h["doc_id"]],
                 "snippet": h["snippet"]} for h in applied[q]]
        r = {k: e[k] for k in ("record_id", "answer_type", "problem_category", "problem", "answer", "derived_answer")}
        r["n_hops"], r["hops"] = len(hops), hops
        note = (adj.get(q) or {}).get("annotator_note") if (adj.get(q) or {}).get("decision") == "keep_with_note" else ""
        if note:
            r["annotator_note"] = note
        rows.append(r)
    write_jsonl(s1.path("final.jsonl"), rows)
    s1.counts("final", {"survivors_of_annotator": len(applied), "flagged": len(flags),
                        "adjudication_dropped": len({q for q, a in adj.items() if a.get("decision") == "drop" and q in flags}),
                        "table_repair_questions": len(repair_q),
                        "table_repair_dropped": sum(1 for q in repair_q if dec[q]["decision"] == "drop"),
                        "final": len(rows), "final_by_type": by_type(rows), "hops": sum(r["n_hops"] for r in rows),
                        "with_annotator_note": sum(1 for r in rows if r.get("annotator_note"))})
    print(f"final: {len(rows)} questions, {sum(r['n_hops'] for r in rows)} hops "
          f"({sum(1 for r in rows if r.get('annotator_note'))} with an annotator note)")


def verify_final(s1: S1):
    """verify_final.py: every snippet of the final list, whatever step produced it, must occur verbatim in the first
    document its hop cites."""
    need(s1, "final.jsonl")
    cp = corpus(s1.run)
    bad, n = [], 0
    for r in s1.jsonl("final.jsonl"):
        for i, h in enumerate(r["hops"], 1):
            n += 1
            d = (h["supporting_doc_ids"] or [""])[0]
            if not contained(h["snippet"], cp.get_document(d) or "" if d else ""):
                bad.append({"qid": r["record_id"], "hop": i, "doc": d})
    write_json(s1.path("final_snippet_failures.json"), bad)
    s1.counts("verify_final", {"snippets": n, "verbatim": n - len(bad), "not_verbatim": len(bad)})
    print(f"checked {n} snippets | verbatim {n - len(bad)} | NOT verbatim {len(bad)}")
    if bad:
        sys.exit(1)


# ---------------------------------------------------------------------------------------------- 12. near misses
def near_misses(s1: S1):
    """build_near_miss.py (DSQA part): every question of the extreme list that is not in the final list, labelled with
    the gate that dropped it; stage_rank 1 = dropped at the last gate. As run, any adjudicated question that is not
    final counts as an adjudication drop (that includes a question dropped after the table-quote repair), with the
    adjudicator's rationale as its reason; the hops are the extreme list's."""
    need(s1, "extreme.jsonl", "final.jsonl")
    final_ids = {r["record_id"] for r in s1.jsonl("final.jsonl")}
    coh, ann, adj = s1.outputs("coherence"), s1.outputs("annotator"), s1.outputs("adjudicate")
    rows = []
    for q, rec in s1.by_id("extreme.jsonl").items():
        if q in final_ids:
            continue
        if q in adj and not (adj[q].get("keep") or adj[q].get("verdict") == "keep_with_note"):
            gate_, rank, why = "adjudication", 1, adj[q].get("reason") or adj[q].get("rationale") or ""
        elif q in ann and not ann[q].get("keep"):
            gate_, rank, why = "annotator", 2, ann[q].get("reason") or ""
        elif q in coh and not coh[q].get("keep"):
            gate_, rank, why = "coherence", 3, coh[q].get("reason") or ""
        else:
            gate_, rank, why = "after-extreme (not reached / other)", 4, ""
        r = {"dataset": "dsqa", "id": q, "failed_gate": gate_, "stage_rank": rank, "reason": str(why)[:600],
             "question": rec.get("problem"), "answer": rec.get("answer"), "answer_type": rec.get("answer_type"),
             "n_hops": rec.get("n_hops"), "hops": rec.get("hops")}
        if gate_ == "coherence":
            r["failure_patterns"] = [f.get("pattern") for f in (coh[q].get("failures") or [])]
        rows.append(r)
    rows.sort(key=lambda r: (r["stage_rank"], nkey(r["id"])))
    write_jsonl(s1.path("near_misses.jsonl"), rows)
    c = Counter(r["failed_gate"] for r in rows)
    s1.counts("near_misses", {"near_misses": len(rows), "by_gate": dict(c)})
    print(f"near misses: {len(rows)} {dict(c)}")


# ---------------------------------------------------------------------------------------------- 13. Stage 2 inputs
def candidate(rec: dict, task: dict) -> dict:
    hops = []
    for h in rec.get("hops") or []:
        docs = list(h.get("supporting_doc_ids") or [])
        hops.append({"clue": h.get("clue", ""), "doc_id": docs[0] if docs else "", "supporting_doc_ids": docs[1:],
                     "snippet": h.get("snippet", ""), "kind": "required", "redundant": False})
    out = {"record_id": rec.get("record_id") or rec.get("id"), "question": rec.get("problem") or rec.get("question"),
           "answer": rec.get("answer"), "answer_type": ANSWER_TYPE.get(rec.get("answer_type"), rec.get("answer_type")),
           "hops": hops, "source": task.get("source"), "problem_category": task.get("problem_category", ""),
           "dsqa_answer_type": rec.get("answer_type")}
    for k in ("derived_answer", "annotator_note", "failed_gate", "stage_rank", "reason", "failure_patterns"):
        if rec.get(k) not in (None, ""):
            out[k] = rec[k]
    return out


def inputs(s1: S1):
    need(s1, "final.jsonl", "near_misses.jsonl")
    tasks = s1.tasks()
    root = s1.run.root / "inputs"
    fin = [candidate(r, tasks.get(r["record_id"], {})) for r in s1.jsonl("final.jsonl")]
    nm = [candidate(r, tasks.get(r["id"], {})) for r in s1.jsonl("near_misses.jsonl")]
    write_jsonl(root / f"{s1.sid}.jsonl", fin)
    write_jsonl(root / f"{s1.sid}_near_misses.jsonl", nm)
    s1.counts("inputs", {"candidates": len(fin), "near_misses": len(nm)})
    print(f"wrote {root / (s1.sid + '.jsonl')} ({len(fin)}) and {root / (s1.sid + '_near_misses.jsonl')} ({len(nm)})")
    print("cmass.json sets entries:", {s1.sid: {"input": f"inputs/{s1.sid}.jsonl", "family": "dsqa", "group": "released"},
                                       f"nm_{s1.sid}": {"input": f"inputs/{s1.sid}_near_misses.jsonl", "family": "dsqa",
                                                        "group": "near_miss"}})


COMMANDS = {"gate": gate, "audited": audited, "extreme": extreme, "xcheck": xcheck, "polish-verify": polish_verify,
            "labels": doc_labels, "consolidate-targets": consolidate_targets, "consolidate-verify": consolidate_verify,
            "evidence": evidence, "coherence": coherence, "apply-fixes": apply_fixes, "verbatim": verbatim,
            "issues": issues, "final": final, "verify-final": verify_final, "near-misses": near_misses, "inputs": inputs}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=list(COMMANDS))
    ap.add_argument("--set", default=DEFAULT_SET)
    a = ap.parse_args()
    COMMANDS[a.command](S1(Run(), a.set))


if __name__ == "__main__":
    main()
