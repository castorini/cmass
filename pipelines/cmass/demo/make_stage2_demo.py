#!/usr/bin/env python3
"""Extend the demo run (make_demo_run.py) with invented Stage 2 records, so every deterministic Stage 2 tool
runs end to end offline: audits with second-reader blocks, revision attempts, hunter findings and verifier
rulings, a review store, and final-check results.

  python3 pipelines/cmass/demo/make_stage2_demo.py [RUN_DIR]      # default: demo/run (gitignored)
  cd pipelines/cmass/demo/run
  python3 ../../tools/stage2_state.py          # where each question stands
  python3 ../../tools/finalize.py --partial    # final/ and excluded/ records

The questions cover every path of the Stage 2 flow (stage2/README.md):

  demo/demo-1     passes; the hunter's rival is excluded by an audit hop; accepted; final check passed
  demo/demo-2     a set answer, closed by a list document; accepted; final check passed
  demo/demo-3     passes the checks, rejected by the reviewer                      -> excluded (human)
  demo/demo-4     a verified refutation (a second keeper fits) -> revision r1 adds one qualifier; re-audited,
                  re-hunted and accepted in the rewrites table; final check passed  -> final (narrowed)
  demo/demo-5     one document supplies every fact                                 -> excluded (audit)
  demo/demo-6     accepted, then the final check finds an unrecorded rival -> evidence-only revision r1;
                  re-audited, re-hunted, re-accepted; final check passed            -> final (final check repaired)
  demo/demo-7     audited, not yet hunted                                          -> still in progress
  nm_demo/demo-nm-1  the corpus contradicts the answer; the repair is unsuccessful -> excluded (revision)
  nm_demo/demo-nm-2  the same question as demo-1 in a second input list; accepted -> included once (duplicate)
  nm_demo/demo-nm-3  no answer                                                     -> excluded (validation)

SYNTHETIC CONTENT ONLY: every place, person and document is invented (the island nation of Oskeria).
Never put a real benchmark item here.
"""
import json
import sys
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import make_demo_run  # noqa: E402

D = "demo"
NM = "nm_demo"

# ------------------------------------------------------------------------------------------- candidates
NEW_CANDIDATES = [
    {"record_id": "demo-4", "answer_type": "single",
     "question": "Which keeper of the Cape Vardequist lighthouse logged every ship that rounded the cape?",
     "answer": "Tomas Hale",
     "hops": [{"clue": "Tomas Hale kept the lighthouse at Cape Vardequist.", "doc_id": "demo_doc_009",
               "supporting_doc_ids": ["demo_doc_009"], "snippet": "Tomas Hale kept the lighthouse at Cape Vardequist"},
              {"clue": "Tomas Hale logged every ship that rounded the cape.", "doc_id": "demo_doc_009",
               "supporting_doc_ids": ["demo_doc_009"], "snippet": "logged every ship that rounded the cape"}]},
    {"record_id": "demo-5", "answer_type": "single",
     "question": "In what year did Davin Kesh found the Port Ilse Conservatory?", "answer": "1880",
     "hops": [{"clue": "Davin Kesh founded the Port Ilse Conservatory in 1880.", "doc_id": "demo_doc_008",
               "supporting_doc_ids": ["demo_doc_008"]}]},
    {"record_id": "demo-6", "answer_type": "single",
     "question": "Who directed the Velmaran Conservatory until 1901?", "answer": "Ilsa Renvaar",
     "hops": [{"clue": "Ilsa Renvaar directed the Velmaran Conservatory until her death in 1901.",
               "doc_id": "demo_doc_007", "supporting_doc_ids": ["demo_doc_007"],
               "snippet": "directed it until her death in 1901"}]},
    {"record_id": "demo-7", "answer_type": "single",
     "question": "Which river joins the Ardune below Port Ilse?", "answer": "Selm",
     "hops": [{"clue": "The Selm joins the Ardune below Port Ilse.", "doc_id": "demo_doc_003",
               "supporting_doc_ids": ["demo_doc_003"], "snippet": "joining the Ardune below Port Ilse"}]},
]
NEW_NEAR_MISSES = [
    {"record_id": "demo-nm-2", "answer_type": "single",
     "question": make_demo_run.CANDIDATES[0]["question"], "answer": "Ardune",
     "hops": deepcopy(make_demo_run.CANDIDATES[0]["hops"])},
    {"record_id": "demo-nm-3", "answer_type": "single",
     "question": "Which ferry crosses from Velmaran to Port Ilse?", "answer": "",
     "hops": [{"clue": "A ferry runs from Velmaran to Port Ilse.", "doc_id": "demo_doc_011"}]},
]

# Searches each question's agents ran (logged through the corpus, as cm.py search --set --qid does).
SEARCHES = {
    (D, "demo-1"): ["river runs through Velmaran", "rivers of Oskeria", "Selm Velmaran"],
    (D, "demo-2"): ["rivers rise Morrowfen hills", "rivers of Oskeria longer than twenty kilometres", "Selm source"],
    (D, "demo-3"): ["Lanterns of Morrowfen composer", "Velmaran Conservatory founded"],
    (D, "demo-4"): ["Cape Vardequist lighthouse keeper", "logged every ship cape", "keeper succeeded Tomas Hale",
                    "Vardequist lighthouse 1890s"],
    (D, "demo-5"): ["Port Ilse Conservatory founded"],
    (D, "demo-6"): ["Velmaran Conservatory director", "Velmaran Conservatory director after 1901", "Oren Vasse"],
    (D, "demo-7"): ["river joins the Ardune", "tributary Ardune Port Ilse"],
    (NM, "demo-nm-1"): ["first atlas Oskerian coast", "Tomas Hale atlas", "Velmaran Geographical Society atlas"],
    (NM, "demo-nm-2"): ["capital of Oskeria river"],
}


# ------------------------------------------------------------------------------------------- builders
def ev(doc, quote, source="original", supports="full"):
    return {"doc_id": doc, "source": source, "supports": supports, "quotes": [quote] if isinstance(quote, str) else quote}


def hop(i, constraints, evidence, status="grounded", typ="required", note="", rejected=None, role="inclusion"):
    return {"index": i, "type": typ, "role": role, "constraints": constraints, "status": status, "evidence": evidence,
            "rejected_docs": rejected or [], "clue_issue": "", "note": note}


def added(aid, clue, constraints, evidence, note, role="inclusion", candidate=None, typ="required"):
    h = {"id": aid, "clue": clue, "type": typ, "role": role, "constraints": constraints, "status": "grounded",
         "evidence": evidence, "rejected_docs": [], "note": note}
    if candidate:
        h["candidate"] = candidate
    return h


def con(cid, text, covered_by, status="covered", note=""):
    return {"id": cid, "text": text, "covered_by": covered_by, "status": status, "note": note}


def cand(name, role, status, hops, note="", **kw):
    return {"name": name, "role": role, "status": status, "hops": hops, "note": note, **kw}


def audit(sid, qid, question, answer, constraints, hops, added_hops, candidates, verdict, summary,
          completeness=None, fail_reasons=(), contamination=None, answer_status="derives_gold", chain="", revision=0):
    a = {"record_id": qid, "set": sid, "revision": revision, "question": question, "answer": answer,
         "auditor": "auditor agent (stage2/AUDIT.md), demo", "constraints": constraints, "hops": hops,
         "added_hops": added_hops, "candidates": candidates,
         "contamination": contamination or {"leak": False, "single_document": False, "doc_ids": [], "note": ""},
         "answer_check": {"status": answer_status, "note": chain}, "verdict": verdict,
         "fail_reasons": list(fail_reasons), "summary": summary,
         "verification": {"result": "confirmed", "changes": [], "note": "Second reader: every quote read in context."}}
    if completeness:
        a["completeness"] = completeness
    return a


def unique(note):
    return {"basis": "unique_by_constraints", "note": note}


def hunt(sid, qid, kind, question, answer, findings, n_searches, note, revision=0):
    return {"record_id": qid, "set": sid, "revision": revision, "kind": kind,
            "route": {"hunt": "all", "hunt_list": "list", "hunt_named": "named"}[kind],
            "question": question, "answer": answer, "findings": findings, "n_searches": n_searches, "note": note}


def finding(fid, kind, candidate, claim, evidence, excluded_by="", changes_answer=False, confidence="likely"):
    return {"id": fid, "kind": kind, "candidate": candidate, "claim": claim, "in_corpus": bool(evidence),
            "evidence": evidence, "web_lead": "", "excluded_by": excluded_by, "changes_answer": changes_answer,
            "confidence": confidence}


def verify(sid, qid, rulings, note, revision=0, hunts=("hunt",)):
    refuted = any(r["ruling"] == "refutes" for r in rulings)
    return {"record_id": qid, "set": sid, "revision": revision, "hunts": list(hunts), "rulings": rulings,
            "refuted": refuted, "blocking": [r["reason"] for r in rulings if r["ruling"] == "refutes"], "note": note}


def final_check(sid, qid, question, answer, members, hops, exclusions, closure, note, status="passed", gaps=(),
                repairable=False, revision=0, matches_source=True):
    return {"record_id": qid, "set": sid, "revision": revision, "status": status, "question": question,
            "answer": answer, "answer_matches_source": matches_source,
            "answer_set": [{"member": m, "aliases": [], "value": ""} for m in members], "ordered": False, "order_by": "",
            "hops": [{"id": h, "grounded": True, "problem": ""} for h in hops], "exclusions": exclusions,
            "closure": closure, "gaps": list(gaps), "repairable": repairable, "blocking": [], "note": note}


def excl(candidate, docs, grounded=True, problem=""):
    return {"candidate": candidate, "grounded": grounded, "docs": docs, "problem": problem}


# ------------------------------------------------------------------------------------------- the records
def records(cands):
    q = {r["record_id"]: r["question"] for r in cands}
    files = {}

    def put(rel, obj):
        files[rel] = obj

    # demo-1: the base demo's sample audit plus its second reader; a rival the audit already excludes
    a1 = deepcopy(make_demo_run.SAMPLE_AUDIT)
    a1["verification"] = {"result": "confirmed", "changes": [], "note": "Both exclusion hops read in context."}
    put("work/demo/demo-1.audit.json", a1)
    put("adversarial/demo/demo-1.hunt.json", hunt(D, "demo-1", "hunt", q["demo-1"], "Ardune", [
        finding("F1", "rival", "Selm", "The Selm is another river of the capital's region and may pass through it.",
                [{"doc_id": "demo_doc_003", "quotes": ["joining the Ardune below Port Ilse"]}],
                excluded_by="A1 (demo_doc_003: it does not pass through Velmaran)")], 5,
        "Searched river lists and each named river; only the Selm needed a check."))
    put("adversarial/demo/demo-1.verify.json", verify(D, "demo-1", [
        {"finding": "hunt:F1", "ruling": "does_not_refute", "docs_read": ["demo_doc_003"],
         "reason": "demo_doc_003 says the Selm does not pass through Velmaran; the audit's A1 records that exclusion."}],
        "No finding breaks the question."))
    put("final_check/demo/demo-1.json", final_check(D, "demo-1", q["demo-1"], "Ardune", ["Ardune"], ["1", "2", "A1", "A2"],
        [excl("Selm", ["demo_doc_003"]), excl("Varrow", ["demo_doc_004"])],
        {"basis": "list_document", "grounded": True, "docs": ["demo_doc_005"], "note": "Three rivers listed; two excluded."},
        "Every hop and exclusion reread in its document; no other river found."))

    # demo-2: a set answer closed by list documents
    put("work/demo/demo-2.audit.json", audit(D, "demo-2", q["demo-2"], ["Ardune", "Varrow"],
        [con("C1", "Which rivers of Oskeria", [1, 2, "A1"]),
         con("C2", "longer than twenty kilometres", ["A1"], note="The list document gives the length class."),
         con("C3", "rise in the Morrowfen hills", [1, 2, "A2"])],
        [hop(1, ["C1", "C3"], [ev("demo_doc_002", "The Ardune rises in the Morrowfen hills")]),
         hop(2, ["C1", "C3"], [ev("demo_doc_004", "The Varrow rises in the Morrowfen hills")])],
        [added("A1", "The Ardune and the Varrow are among Oskeria's rivers longer than twenty kilometres.", ["C1", "C2"],
               [ev("demo_doc_005", "The island has three rivers longer than twenty kilometres: the Ardune, the Selm and "
                                   "the Varrow.", "search")], "No hop established the length constraint."),
         added("A2", "The Selm rises on Mount Corran, not in the Morrowfen hills.", ["C3"],
               [ev("demo_doc_003", "The Selm rises on the slopes of Mount Corran", "search"),
                ev("demo_doc_012", "the Selm rises further west on Mount Corran", "search", "partial")],
               "Excludes the third river of the list.", role="exclusion", candidate="Selm")],
        [cand("Ardune", "member", "qualifies", [1, "A1"]), cand("Varrow", "member", "qualifies", [2, "A1"]),
         cand("Selm", "rival", "excluded", ["A2"], "Rises on Mount Corran.")],
        "pass_with_fixes", "Both members grounded; the audit added the length hop and the Selm's exclusion.",
        completeness={"basis": "list_document", "doc_ids": ["demo_doc_005", "demo_doc_012"],
                      "note": "demo_doc_005 lists the three long rivers; demo_doc_012 names the two that rise in the hills."},
        chain="Long rivers {Ardune, Selm, Varrow} minus the Selm (Mount Corran) = {Ardune, Varrow}."))
    put("adversarial/demo/demo-2.hunt.json", hunt(D, "demo-2", "hunt", q["demo-2"], ["Ardune", "Varrow"], [
        finding("F1", "missing_member", "Selm", "The list names a third long river; it may also rise in the hills.",
                [{"doc_id": "demo_doc_005", "quotes": ["the Ardune, the Selm and the Varrow"]}], excluded_by="A2")], 6,
        "Checked every river of the list document and searched each by name."))
    put("adversarial/demo/demo-2.verify.json", verify(D, "demo-2", [
        {"finding": "hunt:F1", "ruling": "does_not_refute", "docs_read": ["demo_doc_003", "demo_doc_012"],
         "reason": "Both documents place the Selm's source on Mount Corran; A2 records it."}], "The set stands."))
    put("final_check/demo/demo-2.json", final_check(D, "demo-2", q["demo-2"], ["Ardune", "Varrow"], ["Ardune", "Varrow"],
        ["1", "2", "A1", "A2"], [excl("Selm", ["demo_doc_003"])],
        {"basis": "list_document", "grounded": True, "docs": ["demo_doc_005", "demo_doc_012"],
         "note": "The list of long rivers is closed; each is checked."}, "Members, exclusion and closure reread."))

    # demo-3: clean, but rejected by the reviewer
    put("work/demo/demo-3.audit.json", audit(D, "demo-3", q["demo-3"], "1874",
        [con("C1", "the composer of the opera Lanterns of Morrowfen", [1]),
         con("C2", "found the Velmaran Conservatory", [2]), con("C3", "In what year", [2])],
        [hop(1, ["C1"], [ev("demo_doc_006", "Lanterns of Morrowfen, an opera in three acts, was composed by Ilsa Renvaar")]),
         hop(2, ["C2", "C3"], [ev("demo_doc_007", "Ilsa Renvaar founded the Velmaran Conservatory in 1874")],
             rejected=[{"doc_id": "demo_doc_008", "reason": "names the conservatory but not its founder"}])],
        [], [cand("1874", "answer", "qualifies", [1, 2])], "pass",
        "Both hops grounded by their own cited documents.", completeness=unique("A founding year is unique."),
        chain="Lanterns of Morrowfen -> Ilsa Renvaar -> founded the conservatory in 1874."))
    put("adversarial/demo/demo-3.hunt.json", hunt(D, "demo-3", "hunt", q["demo-3"], "1874", [], 3,
                                                  "No other founding year or composer found."))
    put("adversarial/demo/demo-3.verify.json", verify(D, "demo-3", [], "No findings to rule on."))

    # demo-4: a verified refutation, then one added qualifier (revision 1)
    put("work/demo/demo-4.audit.r0.json", audit(D, "demo-4", q["demo-4"], "Tomas Hale",
        [con("C1", "Which keeper of the Cape Vardequist lighthouse", [1]),
         con("C2", "logged every ship that rounded the cape", [2])],
        [hop(1, ["C1"], [ev("demo_doc_009", "Tomas Hale kept the lighthouse at Cape Vardequist from 1890 to 1921")]),
         hop(2, ["C2"], [ev("demo_doc_009", "Tomas Hale kept the lighthouse at Cape Vardequist from 1890 to 1921 and "
                                            "logged every ship that rounded the cape.")])],
        [], [cand("Tomas Hale", "answer", "qualifies", [1, 2])], "pass", "Both hops grounded by the cited document.",
        completeness=unique("Three searches found no other keeper."), chain="Hale kept the lighthouse and logged the ships."))
    put("adversarial/demo/demo-4.hunt.json", hunt(D, "demo-4", "hunt", q["demo-4"], "Tomas Hale", [
        finding("F1", "rival", "Edda Morlan", "Morlan kept the same lighthouse and also logged every ship.",
                [{"doc_id": "demo_doc_013", "quotes": ["Edda Morlan succeeded Tomas Hale as keeper of the Cape Vardequist "
                                                       "lighthouse in 1921 and, like him, logged every ship that rounded the cape"]}],
                changes_answer=True, confidence="certain")], 4, "A later keeper fits every constraint."))
    put("adversarial/demo/demo-4.verify.json", verify(D, "demo-4", [
        {"finding": "hunt:F1", "ruling": "refutes", "docs_read": ["demo_doc_013"],
         "reason": "demo_doc_013 states that Edda Morlan kept the Cape Vardequist lighthouse and logged every ship; "
                   "no constraint excludes her, so the answer is not unique."}], "One verified rival."))
    q4 = "Which keeper of the Cape Vardequist lighthouse in the 1890s logged every ship that rounded the cape?"
    put("revisions/demo/demo-4.r1.json", {
        "record_id": "demo-4", "set": D, "attempt": 1,
        "trigger": {"stage": "adversarial", "ref": "adversarial/demo/demo-4.verify.json",
                    "reasons": ["Edda Morlan fits every constraint (demo_doc_013)"], "evidence_only": False},
        "based_on_revision": 0,
        "evidence_retry": {"queries": ["keeper succeeded Tomas Hale", "Vardequist lighthouse 1890s"], "resolved": False,
                           "note": "Both keepers logged every ship; no evidence separates them under the current wording."},
        "outcome": "revised", "question": q4, "answer": "Tomas Hale",
        "edits": [{"kind": "add_qualifier", "from": "", "to": "in the 1890s",
                   "note": "Hale kept the lighthouse from 1890; Morlan only from 1921."}],
        "edit_rationale": "One grounded qualifier excludes the verified rival and keeps Hale the only keeper who fits.",
        "hop_changes": [{"hop": "A1", "change": "added", "note": "exclusion of Edda Morlan (demo_doc_013)"}],
        "trivial_as_written": False, "blocker": "", "note": ""})
    put("work/demo/demo-4.audit.json", audit(D, "demo-4", q4, "Tomas Hale",
        [con("C1", "Which keeper of the Cape Vardequist lighthouse in the 1890s", [1, "A1"],
             note="Hale's tenure 1890-1921 covers the decade."),
         con("C2", "logged every ship that rounded the cape", [2])],
        [hop(1, ["C1"], [ev("demo_doc_009", "Tomas Hale kept the lighthouse at Cape Vardequist from 1890 to 1921")]),
         hop(2, ["C2"], [ev("demo_doc_009", "Tomas Hale kept the lighthouse at Cape Vardequist from 1890 to 1921 and "
                                            "logged every ship that rounded the cape.")])],
        [added("A1", "Edda Morlan became keeper of the Cape Vardequist lighthouse only in 1921.", ["C1"],
               [ev("demo_doc_013", "Edda Morlan succeeded Tomas Hale as keeper of the Cape Vardequist lighthouse in 1921",
                   "search")], "Excludes the rival the verifier confirmed.", role="exclusion", candidate="Edda Morlan")],
        [cand("Tomas Hale", "answer", "qualifies", [1, 2]),
         cand("Edda Morlan", "rival", "excluded", ["A1"], "Keeper only from 1921.")],
        "pass_with_fixes", "Revision 1 adds 'in the 1890s'; Hale's tenure covers it and Morlan's starts in 1921.",
        completeness=unique("Two keepers are known; the qualifier leaves one."), revision=1,
        chain="Keeper in the 1890s -> Hale (1890-1921); he logged every ship."))
    put("adversarial/demo/demo-4.hunt.r1.json", hunt(D, "demo-4", "hunt", q4, "Tomas Hale", [], 4,
                                                     "No keeper other than Hale served in the 1890s.", revision=1))
    put("adversarial/demo/demo-4.verify.r1.json", verify(D, "demo-4", [], "No findings to rule on.", revision=1))
    put("final_check/demo/demo-4.r1.json", final_check(D, "demo-4", q4, "Tomas Hale", ["Tomas Hale"], ["1", "2", "A1"],
        [excl("Edda Morlan", ["demo_doc_013"])],
        {"basis": "unique_by_constraints", "grounded": True, "docs": [], "note": "One keeper in the 1890s."},
        "The added qualifier is grounded for Hale and excludes Morlan.", revision=1))

    # demo-5: one document supplies every fact
    put("work/demo/demo-5.audit.json", audit(D, "demo-5", q["demo-5"], "1880",
        [con("C1", "In what year", [1]), con("C2", "Davin Kesh found the Port Ilse Conservatory", [1])],
        [hop(1, ["C1", "C2"], [ev("demo_doc_008", "The Port Ilse Conservatory was founded in 1880 by Davin Kesh.")])],
        [], [cand("1880", "answer", "qualifies", [1])], "fail",
        "One sentence of demo_doc_008 states the founder, the conservatory and the year.",
        completeness=unique("A founding year."), fail_reasons=["contamination"],
        contamination={"leak": False, "single_document": True, "doc_ids": ["demo_doc_008"],
                       "note": "demo_doc_008 supplies every fact the question needs."},
        chain="Kesh founded it in 1880 (one document)."))

    # demo-6: accepted, the final check finds an unrecorded rival, one evidence-only repair
    rival = "After Ilsa Renvaar's death in 1901, the composer Oren Vasse directed the Velmaran Conservatory until 1930."
    a6 = audit(D, "demo-6", q["demo-6"], "Ilsa Renvaar",
               [con("C1", "Who directed the Velmaran Conservatory", [1]), con("C2", "until 1901", [1])],
               [hop(1, ["C1", "C2"], [ev("demo_doc_007", "Ilsa Renvaar founded the Velmaran Conservatory in 1874 and "
                                                         "directed it until her death in 1901.")])],
               [], [cand("Ilsa Renvaar", "answer", "qualifies", [1])], "pass", "The hop is grounded by its cited document.",
               completeness=unique("The directorship ended in 1901."), chain="Renvaar directed it until 1901.")
    put("work/demo/demo-6.audit.r0.json", a6)
    put("adversarial/demo/demo-6.hunt.json", hunt(D, "demo-6", "hunt", q["demo-6"], "Ilsa Renvaar", [], 3,
                                                  "No other director found."))
    put("adversarial/demo/demo-6.verify.json", verify(D, "demo-6", [], "No findings to rule on."))
    put("final_check/demo/demo-6.json", final_check(D, "demo-6", q["demo-6"], "Ilsa Renvaar", ["Ilsa Renvaar"], ["1"],
        [excl("Oren Vasse", ["demo_doc_014"], grounded=False, problem="no exclusion recorded")],
        {"basis": "unique_by_constraints", "grounded": True, "docs": [], "note": ""},
        "A later director is in the corpus and the record does not exclude him.", status="failed",
        gaps=[{"kind": "evidence", "what": "Oren Vasse also directed the conservatory; the record has no grounded exclusion",
               "doc_id": "demo_doc_014", "quote": rival}], repairable=True))
    put("revisions/demo/demo-6.r1.json", {
        "record_id": "demo-6", "set": D, "attempt": 1,
        "trigger": {"stage": "final_check", "ref": "final_check/demo/demo-6.json",
                    "reasons": ["Oren Vasse also directed the conservatory; the record has no grounded exclusion"],
                    "evidence_only": True},
        "based_on_revision": 0,
        "evidence_retry": {"queries": ["Velmaran Conservatory director after 1901", "Oren Vasse"], "resolved": True,
                           "note": "demo_doc_014 dates Vasse's directorship from 1901 to 1930."},
        "outcome": "evidence_only", "question": q["demo-6"], "answer": "Ilsa Renvaar", "edits": [], "edit_rationale": "",
        "hop_changes": [{"hop": "A1", "change": "added", "note": "exclusion of Oren Vasse (demo_doc_014)"}],
        "trivial_as_written": False, "blocker": "", "note": ""})
    a6r = deepcopy(a6)
    a6r.update(revision=1, verdict="pass_with_fixes",
               summary="Revision 1 (evidence only) adds the exclusion of the later director.")
    a6r["added_hops"] = [added("A1", "Oren Vasse directed the Velmaran Conservatory until 1930, not until 1901.", ["C2"],
                               [ev("demo_doc_014", rival, "search")], "Excludes the director the final check found.",
                               role="exclusion", candidate="Oren Vasse")]
    a6r["constraints"][1]["covered_by"] = [1, "A1"]
    a6r["candidates"].append(cand("Oren Vasse", "rival", "excluded", ["A1"], "Directed it until 1930."))
    put("work/demo/demo-6.audit.json", a6r)
    put("adversarial/demo/demo-6.hunt.r1.json", hunt(D, "demo-6", "hunt", q["demo-6"], "Ilsa Renvaar", [], 3,
                                                     "Vasse is excluded; no other director found.", revision=1))
    put("adversarial/demo/demo-6.verify.r1.json", verify(D, "demo-6", [], "No findings to rule on.", revision=1))
    put("final_check/demo/demo-6.r1.json", final_check(D, "demo-6", q["demo-6"], "Ilsa Renvaar", ["Ilsa Renvaar"],
        ["1", "A1"], [excl("Oren Vasse", ["demo_doc_014"])],
        {"basis": "unique_by_constraints", "grounded": True, "docs": [], "note": "One director until 1901."},
        "The exclusion is now recorded and grounded.", revision=1))

    # demo-7: audited and read twice; the adversarial check has not run
    put("work/demo/demo-7.audit.json", audit(D, "demo-7", q["demo-7"], "Selm",
        [con("C1", "Which river joins the Ardune", [1, "A1"]), con("C2", "below Port Ilse", [1])],
        [hop(1, ["C1", "C2"], [ev("demo_doc_003", "runs through the market town of Hadley before joining the Ardune below "
                                                  "Port Ilse")])],
        [added("A1", "The Varrow flows to the Gulf of Amberly, not into the Ardune.", ["C1"],
               [ev("demo_doc_004", "flows south through the Brannoch marshes to the Gulf of Amberly", "search")],
               "Excludes the other long river.", role="exclusion", candidate="Varrow")],
        [cand("Selm", "answer", "qualifies", [1]), cand("Varrow", "rival", "excluded", ["A1"])],
        "pass_with_fixes", "Grounded; the audit added the Varrow's exclusion.",
        completeness=unique("The other long river reaches the sea elsewhere."), chain="The Selm joins the Ardune."))

    # demo-nm-1: the corpus contradicts the answer; the repair attempt is unsuccessful
    put("work/nm_demo/demo-nm-1.audit.json", audit(NM, "demo-nm-1", make_demo_run.NEAR_MISSES[0]["question"], "Tomas Hale",
        [con("C1", "Which lighthouse keeper of Cape Vardequist", [1]),
         con("C2", "compiled the first atlas of the Oskerian coast", [2], "not_covered",
             "The atlas document credits a society committee.")],
        [hop(1, ["C1"], [ev("demo_doc_009", "Tomas Hale kept the lighthouse at Cape Vardequist from 1890 to 1921")]),
         hop(2, ["C2"], [], status="ungrounded", note="Searched three ways; no document attributes the atlas to Hale.",
             rejected=[{"doc_id": "demo_doc_010", "reason": "credits a committee of the Velmaran Geographical Society"}])],
        [], [cand("Tomas Hale", "answer", "unresolved", [1], "Nothing grounds his authorship of the atlas.")], "fail",
        "The atlas is credited to a committee; the answer is not supported.",
        completeness=unique("Single answer."), fail_reasons=["incorrect_answer", "ungrounded"],
        answer_status="does_not_derive", chain="Hale is the keeper; the atlas link fails."))
    put("revisions/nm_demo/demo-nm-1.r1.json", {
        "record_id": "demo-nm-1", "set": NM, "attempt": 1,
        "trigger": {"stage": "audit", "ref": "work/nm_demo/demo-nm-1.audit.json",
                    "reasons": ["incorrect_answer", "ungrounded"], "evidence_only": False},
        "based_on_revision": 0,
        "evidence_retry": {"queries": ["Tomas Hale atlas", "Velmaran Geographical Society atlas"], "resolved": False,
                           "note": "Only the committee is credited."},
        "outcome": "unsuccessful", "edits": [], "edit_rationale": "", "hop_changes": [],
        "blocker": "No document attributes the atlas to Hale, the family preserves the answer, and removing the atlas "
                   "clue would leave two keepers (Hale and Morlan).", "note": ""})

    # demo-nm-2: a duplicate of demo-1 from the near-miss list
    a1n = deepcopy(a1)
    a1n.update(record_id="demo-nm-2", set=NM)
    put("work/nm_demo/demo-nm-2.audit.json", a1n)
    put("adversarial/nm_demo/demo-nm-2.hunt.json", hunt(NM, "demo-nm-2", "hunt", q["demo-1"], "Ardune", [], 2,
                                                        "As for the same question in the main list."))
    put("adversarial/nm_demo/demo-nm-2.verify.json", verify(NM, "demo-nm-2", [], "No findings to rule on."))
    fc = deepcopy(files["final_check/demo/demo-1.json"])
    fc.update(record_id="demo-nm-2", set=NM)
    put("final_check/nm_demo/demo-nm-2.json", fc)
    return files


REVIEWS = {
    "demo~demo-1": {"decision": "accept", "comment": "", "hops": {}, "constraints": {}},
    "demo~demo-2": {"decision": "accept", "comment": "", "hops": {},
                    "constraints": {"C2": {"agree": "agree", "comment": "The list document gives the length class."}}},
    "demo~demo-3": {"decision": "reject", "comment": "Too easy for the benchmark: the opera's title alone finds both pages.",
                    "hops": {}, "constraints": {}},
    "rw_demo~demo-4.r1": {"decision": "accept", "comment": "The qualifier is natural and grounded.", "hops": {},
                          "constraints": {}},
    "demo~demo-6": {"decision": "accept", "comment": "", "hops": {}, "constraints": {}},
    "demo~demo-6.r1": {"decision": "accept", "comment": "", "hops": {"A1": {"agree": "agree", "comment": ""}},
                       "constraints": {}},
    "nm_demo~demo-nm-2": {"decision": "accept", "comment": "", "hops": {}, "constraints": {}},
}


def main():
    run = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE / "run"
    sys.argv = [sys.argv[0], str(run)]
    make_demo_run.main()
    for name, extra in (("demo.jsonl", NEW_CANDIDATES), ("demo_near_misses.jsonl", NEW_NEAR_MISSES)):
        p = run / "inputs" / name
        rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        ids = {r["record_id"] for r in rows}
        rows += [r for r in extra if r["record_id"] not in ids]
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    from cmass.corpus import Corpus
    from cmass.run import Run, write_json
    r = Run(run)
    corpus = Corpus(r)
    for (sid, qid), queries in SEARCHES.items():
        r.searchlog(sid, qid).unlink(missing_ok=True)
        for query in queries:
            corpus.search(query, 50, sid, qid)
    cands = make_demo_run.CANDIDATES + NEW_CANDIDATES
    for rel, obj in records(cands).items():
        write_json(run / rel, obj)
    write_json(run / "review" / "reviews" / "reviews.json",
               {k: {**v, "updated_at": "2026-01-01T00:00:00+0000"} for k, v in REVIEWS.items()})
    print(f"stage 2 demo records written under {run}\n  cd {run} && python3 {HERE.parent / 'tools' / 'stage2_state.py'}")


if __name__ == "__main__":
    main()
