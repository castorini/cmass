#!/usr/bin/env python3
"""Validate Stage 2 audit files (work/<set>/<qid>.audit.json) against the schema and the documents.

  validate.py                    # every audit file of every configured set
  validate.py SET ['glob']       # one set, optional glob on record_id
  validate.py SET QID

Exit status 1 if any ERROR. Every quote must occur verbatim in its cited document (case-sensitive;
whitespace runs may differ); a document not yet cached is fetched once. The schema is documented in
stage2/SCHEMA.md.
"""
import fnmatch
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cmass.corpus import Corpus  # noqa: E402
from cmass.records import answer_type, gold_answer, hop_doc_ids, hops as rec_hops, question_text  # noqa: E402
from cmass.run import Run  # noqa: E402
from cmass.text import is_verbatim, norm_ws, split_spans  # noqa: E402

HOP_TYPES = {"required", "confirmatory", "derivation"}
HOP_ROLES = {"inclusion", "exclusion"}
HOP_STATUS = {"grounded", "partially_grounded", "ungrounded", "derivation_ok", "derivation_wrong", "dropped"}
ADDED_STATUS = {"grounded", "partially_grounded", "ungrounded"}
CON_STATUS = {"covered", "partially_covered", "not_covered"}
SOURCES = {"original", "other_hop", "search"}
SUPPORTS = {"full", "partial"}
ANSWER = {"derives_gold", "partial", "does_not_derive"}
VERDICTS = {"pass", "pass_with_fixes", "fail"}
CAND_ROLES = {"answer", "member", "rival"}
CAND_STATUS = {"qualifies", "excluded", "unresolved"}
BASES = {"unique_by_constraints", "list_document", "fixed_domain", "three_way_search"}
FAIL_REASONS = {"ungrounded", "incorrect_question", "incorrect_answer", "unresolved_alternative", "incomplete_set",
                "unsupported_member", "inconsistent_evidence", "contamination"}


def _same_answer(a, b) -> bool:
    if isinstance(a, list) or isinstance(b, list):
        la = sorted(norm_ws(str(x)).lower() for x in (a if isinstance(a, list) else [a]))
        lb = sorted(norm_ws(str(x)).lower() for x in (b if isinstance(b, list) else [b]))
        return la == lb
    return norm_ws(str(a)).lower() == norm_ws(str(b)).lower()


def check(corpus: Corpus, sid: str, rec: dict, a: dict, default_type: str) -> list[str]:
    errs = []
    qid = rec["record_id"]

    def E(msg):
        errs.append(f"ERROR {sid}/{qid}: {msg}")

    def W(msg):
        errs.append(f"warn  {sid}/{qid}: {msg}")

    for k in ("constraints", "hops", "added_hops", "candidates", "answer_check", "verdict", "summary"):
        if k not in a:
            E(f"missing key {k!r}")
    if errs:
        return errs
    revision = int(a.get("revision") or 0)
    q = norm_ws(a.get("question") or question_text(rec))
    ans = a.get("answer", gold_answer(rec))
    atype = answer_type(rec, default_type)
    if revision == 0:
        if norm_ws(a.get("question") or question_text(rec)) != norm_ws(question_text(rec)):
            E("question differs from the record's although revision is 0; question changes go through a revision")
        if not _same_answer(ans, gold_answer(rec)):
            E("answer differs from the record's gold although revision is 0; answer changes go through a revision")
    else:
        rp = corpus.run.revision_file(sid, qid, revision)
        try:
            rv = json.loads(rp.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            rv = None
        if not rv or rv.get("outcome") not in ("evidence_only", "revised"):
            E(f"revision is {revision} but {rp} is missing, unreadable or not a successful repair")
        else:
            if norm_ws(a.get("question") or "") != norm_ws(rv.get("question") or ""):
                E(f"question differs from revision {revision}'s question (an audit of a revision states it in 'question')")
            if not _same_answer(ans, rv.get("answer", gold_answer(rec))):
                E(f"answer differs from revision {revision}'s answer (an audit of a revision states it in 'answer')")
    n = len(rec_hops(rec))

    added_ids = {h.get("id") for h in a["added_hops"]}
    valid_refs = set(range(1, n + 1)) | added_ids
    cids = set()
    for c in a["constraints"]:
        cid = c.get("id")
        if cid in cids:
            E(f"duplicate constraint id {cid}")
        cids.add(cid)
        spans = split_spans(c.get("text", ""))
        if not spans or any(norm_ws(s) not in q for s in spans):
            E(f"constraint {cid} text is not a verbatim span of the question: {c.get('text')!r}")
        if c.get("status") not in CON_STATUS:
            E(f"constraint {cid} bad status {c.get('status')!r}")
        refs = c.get("covered_by", [])
        bad = [r for r in refs if r not in valid_refs]
        if bad:
            E(f"constraint {cid} covered_by unknown hops {bad}")
        if c.get("status") == "covered" and not refs:
            E(f"constraint {cid} is 'covered' but covered_by is empty")
    if not a["constraints"]:
        E("no constraints listed")

    seen = sorted(h.get("index") for h in a["hops"] if isinstance(h.get("index"), int))
    if seen != list(range(1, n + 1)):
        E(f"hop indices {seen} do not match the record's hops 1..{n}")

    hop_status = {}

    def check_evidence(label, h, statuses, cited=frozenset()):
        st = h.get("status")
        if st not in statuses:
            E(f"{label} bad status {st!r}")
        if h.get("role", "inclusion") not in HOP_ROLES:
            E(f"{label} bad role {h.get('role')!r}")
        for c in h.get("constraints", []):
            if c not in cids:
                E(f"{label} refers to unknown constraint {c!r}")
        full = 0
        for ev in h.get("evidence", []):
            did = ev.get("doc_id")
            if ev.get("source") not in SOURCES:
                E(f"{label} evidence {did} bad source {ev.get('source')!r}")
            if ev.get("supports") not in SUPPORTS:
                E(f"{label} evidence {did} bad supports {ev.get('supports')!r}")
            if ev.get("source") == "original" and cited and did not in cited:
                E(f"{label} evidence {did} marked original but not cited by this hop")
            text = corpus.get_document(did) if did else None
            if text is None:
                E(f"{label} evidence document {did} not found")
                continue
            if not ev.get("quotes"):
                E(f"{label} evidence {did} has no quotes")
            for qt in ev.get("quotes", []):
                if len(qt.strip()) < 12:
                    W(f"{label} very short quote {qt!r}")
                if not is_verbatim(text, qt):
                    E(f"{label} quote not verbatim in {did}: {qt[:90]!r}")
            full += ev.get("supports") == "full"
        if st == "grounded" and full == 0:
            E(f"{label} is grounded but has no evidence with supports=full")
        if st in ("ungrounded", "partially_grounded") and not h.get("note"):
            E(f"{label} is {st} but has no note saying what is missing")
        for rj in h.get("rejected_docs", []):
            if not isinstance(rj, dict) or not rj.get("doc_id") or not rj.get("reason"):
                E(f"{label} rejected_docs entries must be {{'doc_id', 'reason'}}: {rj!r}")

    orig = {i: h for i, h in enumerate(rec_hops(rec), 1)}
    for h in a["hops"]:
        i = h.get("index")
        label = f"hop {i}"
        hop_status[i] = h.get("status")
        if h.get("type") not in HOP_TYPES:
            E(f"{label} bad type {h.get('type')!r}")
        if h.get("status") == "dropped":
            if revision == 0:
                E(f"{label} is 'dropped' but this audit is not of a revision")
            if not h.get("note"):
                E(f"{label} is dropped but has no note saying which removed constraint it served")
            continue
        if h.get("type") == "derivation":
            if h.get("status") not in ("derivation_ok", "derivation_wrong"):
                E(f"{label} derivation hop must be derivation_ok or derivation_wrong")
            if not h.get("note"):
                E(f"{label} derivation hop needs a note with the operation and its grounded inputs")
            continue
        check_evidence(label, h, HOP_STATUS - {"derivation_ok", "derivation_wrong", "dropped"},
                       frozenset(hop_doc_ids(orig.get(i, {}))))
    for h in a["added_hops"]:
        label = f"added hop {h.get('id')}"
        hop_status[h.get("id")] = h.get("status")
        if not re.fullmatch(r"A\d+", str(h.get("id") or "")) or "index" in h:
            E(f"{label} needs an id A<n> (and no index)")
        if not h.get("clue"):
            E(f"{label} has no clue")
        if not h.get("note"):
            E(f"{label} has no note on why it is needed")
        if h.get("type") not in HOP_TYPES - {"derivation"}:
            E(f"{label} bad type {h.get('type')!r}")
        if h.get("role") == "exclusion" and not h.get("candidate"):
            W(f"{label} is an exclusion hop but names no candidate it excludes")
        check_evidence(label, h, ADDED_STATUS)

    # ---------------------------------------------------------------- candidates and completeness
    cands = a["candidates"]
    if not isinstance(cands, list):
        E("candidates must be a list")
        cands = []
    for k, c in enumerate(cands, 1):
        lab = f"candidate {k} ({c.get('name')!r})"
        if c.get("role") not in CAND_ROLES:
            E(f"{lab} bad role {c.get('role')!r}")
        if c.get("status") not in CAND_STATUS:
            E(f"{lab} bad status {c.get('status')!r}")
        refs = c.get("hops", [])
        bad = [r for r in refs if r not in valid_refs]
        if bad:
            E(f"{lab} refers to unknown hops {bad}")
        grounded_refs = [r for r in refs if hop_status.get(r) in ("grounded", "derivation_ok")]
        if c.get("status") in ("qualifies", "excluded") and not grounded_refs:
            E(f"{lab} is {c.get('status')} but none of its hops is grounded")
        if c.get("status") == "excluded":
            ex = [h for h in a["hops"] + a["added_hops"] if (h.get("index") in refs or h.get("id") in refs)
                  and h.get("role") == "exclusion"]
            if not ex:
                E(f"{lab} is excluded but none of its hops has role 'exclusion'")
        if c.get("status") == "unresolved" and not c.get("note"):
            E(f"{lab} is unresolved but has no note saying what is missing")
    answers = [c for c in cands if c.get("role") in ("answer", "member")]
    rivals_q = [c for c in cands if c.get("role") == "rival" and c.get("status") == "qualifies"]
    unresolved = [c for c in cands if c.get("status") == "unresolved"]
    comp = a.get("completeness") or {}
    if atype in ("set", "aggregate"):
        if not comp:
            E(f"{atype} answer needs a completeness block")
        elif comp.get("basis") not in BASES - {"unique_by_constraints"}:
            E(f"{atype} completeness.basis must be list_document, fixed_domain or three_way_search")
    if comp:
        if comp.get("basis") not in BASES:
            E(f"completeness.basis must be one of {sorted(BASES)}")
        if comp.get("basis") in ("list_document",) and not comp.get("doc_ids"):
            E("completeness by list_document needs the doc_ids that list the members or give the set size")
        if comp.get("basis") == "three_way_search":
            qs = comp.get("queries") or {}
            missing = [r for r in ("list", "named", "category") if not qs.get(r)]
            if missing:
                E(f"three_way_search completeness needs queries for every route; missing {missing}")
        if not comp.get("note"):
            W("completeness has no note")

    # ---------------------------------------------------------------- contamination, answer, verdict
    con = a.get("contamination") or {}
    contaminated = bool(con.get("leak") or con.get("single_document"))
    ac = a["answer_check"]
    if ac.get("status") not in ANSWER:
        E(f"answer_check bad status {ac.get('status')!r}")
    v = a["verdict"]
    if v not in VERDICTS:
        E(f"bad verdict {v!r}")
    reasons = a.get("fail_reasons", [])
    bad = [r for r in reasons if r not in FAIL_REASONS]
    if bad:
        E(f"unknown fail_reasons {bad}; allowed {sorted(FAIL_REASONS)}")

    statuses = [h.get("status") for h in a["hops"]] + [h.get("status") for h in a["added_hops"]]
    clean = (all(s in ("grounded", "derivation_ok", "dropped") for s in statuses)
             and all(c.get("status") == "covered" for c in a["constraints"])
             and ac.get("status") == "derives_gold"
             and not rivals_q and not unresolved and not contaminated
             and all(c.get("status") == "qualifies" for c in answers))
    if atype == "single" and not rivals_q and not answers:
        W("no candidate with role 'answer': record the answer and every rival considered in candidates")
    fixed = bool(a["added_hops"]) or any(
        all(not (e.get("source") == "original" and e.get("supports") == "full") for e in h.get("evidence", []))
        for h in a["hops"] if h.get("status") == "grounded")
    if v == "pass" and (not clean or fixed):
        E("verdict 'pass' needs every constraint covered, every hop grounded by its own cited documents, "
          "the answer derived and unique (or the set complete), and no added hops")
    if v == "pass_with_fixes" and not clean:
        E("verdict 'pass_with_fixes' but something is still uncovered, ungrounded, unresolved or underived")
    if v == "fail" and not reasons:
        E("verdict 'fail' needs fail_reasons")
    if v != "fail" and reasons:
        E("fail_reasons given but the verdict is not 'fail'")
    if v == "fail" and clean:
        W("verdict 'fail' although every check passes")
    if rivals_q and "unresolved_alternative" not in reasons and atype == "single":
        W("a rival qualifies: the answer is not unique (fail_reasons should include unresolved_alternative)")
    if contaminated and "contamination" not in reasons:
        E("contamination recorded but 'contamination' is not among fail_reasons")
    return errs


def main():
    argv = sys.argv[1:]
    run = Run()
    corpus = Corpus(run)
    sets = [argv[0]] if argv else list(run.config.sets)
    pat = argv[1] if len(argv) > 1 else "*"
    n = nerr = 0
    for sid in sets:
        default_type = run.config.set(sid)["answer_type"]
        for rec in run.records(sid):
            if not fnmatch.fnmatch(rec["record_id"], pat):
                continue
            p = run.audit_file(sid, rec["record_id"])     # archived audits (<qid>.audit.r<N>.json) are not checked
            if not p.exists():
                continue
            n += 1
            try:
                a = json.loads(p.read_text())
            except json.JSONDecodeError as e:
                print(f"ERROR {sid}/{rec['record_id']}: invalid JSON: {e}")
                nerr += 1
                continue
            for line in check(corpus, sid, rec, a, default_type):
                print(line)
                nerr += line.startswith("ERROR")
    print(f"checked {n} audit file(s); {nerr} error(s)")
    sys.exit(1 if nerr else 0)


if __name__ == "__main__":
    main()
