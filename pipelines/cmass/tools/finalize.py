#!/usr/bin/env python3
"""Build the Stage 2 output records (C6 script validation): final/<set>_final.jsonl and final/<set>_excluded.jsonl.

  finalize.py [SET ...] [--reviews PATH] [--partial]

A question is final when its current version passed the audit (validate.py, 0 errors) and the second
reader, survived the adversarial check, was accepted by the reviewer at that version's review key, and
passed the final check (or its set is exempt). It is excluded, with the stage and reason, when Stage 2
excluded it (cmass/stage2.py decides both from the files on disk). Then, for every retained record:

  - every quote of every accepted hop is matched again against its document; a mismatch stops
    finalization and nothing is written;
  - the record's revision, adversarial and final-check files are checked (check_records.py); an error stops;
  - a candidate listed in several input lists (same source question and answer) is included once, in the
    first configured set that accepts it; the other copies are excluded as duplicates (stage validation).

A question still in progress (no decision yet, a repair pending, ...) stops finalization unless --partial
is given; then it is listed and left out of both files. The record formats are in stage2/SCHEMA.md.
Run inside a run directory (or set $CMASS_RUN).
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_records  # noqa: E402
import stage2_state  # noqa: E402
from cmass import stage2  # noqa: E402
from cmass.corpus import Corpus  # noqa: E402
from cmass.records import hops as rec_hops  # noqa: E402
from cmass.run import Run, write_jsonl  # noqa: E402
from cmass.text import is_verbatim, norm_ws  # noqa: E402


class Stop(SystemExit):
    pass


def evidence(h) -> list[dict]:
    return [{"doc_id": e["doc_id"], "supports": e.get("supports"), "quotes": list(e.get("quotes") or [])}
            for e in h.get("evidence", [])]


def final_hop(hid, clue, h) -> dict:
    row = {"id": hid, "clue": clue, "type": h.get("type"), "role": h.get("role", "inclusion"),
           "constraints": h.get("constraints", []), "status": h.get("status"), "evidence": evidence(h),
           "note": h.get("note", "")}
    if row["role"] == "exclusion":
        row["candidate"] = h.get("candidate", "")
    return row


def recheck_quotes(corpus, where, hops):
    for h in hops:
        if h["status"] not in ("grounded", "derivation_ok"):
            raise Stop(f"{where}: hop {h['id']} is {h['status']!r} in an accepted record")
        for e in h["evidence"]:
            text = corpus.get_document(e["doc_id"])
            for q in e["quotes"]:
                if text is None or not is_verbatim(text, q):
                    raise Stop(f"{where}: hop {h['id']}: quote not verbatim in {e['doc_id']}: {q[:90]!r} "
                               "(finalization stopped; nothing written)")


def build(run, corpus, sid, rec, st, reviews) -> dict:
    qid = rec["record_id"]
    where = f"{sid}/{qid}"
    a = stage2.load_json(run.audit_file(sid, qid))
    revs = stage2.load_revisions(run, sid, qid)
    v = stage2.Version(rec, revs)
    clues = {i: h.get("clue", "") for i, h in enumerate(rec_hops(rec), 1)}
    hops, dropped = [], []
    for h in sorted(a["hops"], key=lambda x: x.get("index", 0)):
        i = h.get("index")
        if h.get("status") == "dropped":
            dropped.append({"id": str(i), "clue": clues.get(i, ""), "note": h.get("note", "")})
        else:
            hops.append(final_hop(str(i), clues.get(i, ""), h))
    hops += [final_hop(h["id"], h.get("clue", ""), h) for h in a.get("added_hops", [])]
    recheck_quotes(corpus, where, hops)
    rv = reviews.get(st["review_key"]) or {}
    fc = stage2.load_json(stage2.fc_path(run, sid, qid, v.n)) if st["final_check_enabled"] else None
    cands = [{k: c[k] for k in ("name", "role", "status", "hops", "aliases", "value", "note") if k in c}
             for c in a.get("candidates", [])]
    if fc and fc.get("answer_set"):
        answer_set = [{"member": m.get("member"), "aliases": m.get("aliases", []), "value": m.get("value", "")}
                      for m in fc["answer_set"]]
        ordered, order_by = bool(fc.get("ordered")), fc.get("order_by", "")
    else:
        answer_set = [{"member": c["name"], "aliases": c.get("aliases", []), "value": c.get("value", "")}
                      for c in cands if c.get("role") in ("answer", "member") and c.get("status") == "qualifies"]
        ordered, order_by = False, ""
    queries = list(dict.fromkeys(s.get("query") for s in stage2.searchlog(run, sid, qid) if s.get("query")))
    rationale = " ".join(f"r{r['_n']}: {norm_ws(r['edit_rationale'])}" for r in revs
                         if r.get("outcome") == "revised" and r.get("edit_rationale") and r["_n"] <= v.n)
    refuted = [n for n in range(v.n + 1)
               if (stage2.load_json(stage2.adv_path(run, sid, qid, "verify", n)) or {}).get("refuted")]
    fc_note = ("the final check does not apply to this set (config final_check false)" if not st["final_check_enabled"]
               else (fc or {}).get("note", ""))
    row = {
        "record_id": qid, "set": sid,
        "source": {"question": v.source_question, "answer": v.source_answer},
        "question": v.question, "answer": v.answer, "answer_type": st["answer_type"],
        "category": stage2.category(v), "revision": v.n,
        "edits": v.accepted_edits(), "edit_rationale": rationale,
        "constraints": [{k: c.get(k) for k in ("id", "text", "covered_by", "status", "note")} for c in a["constraints"]],
        "hops": hops, "dropped_hops": dropped, "candidates": cands, "completeness": a.get("completeness") or None,
        "answer_set": answer_set, "ordered": ordered, "order_by": order_by,
        "search_queries": queries,
        "audit_verdict": a["verdict"], "audit_summary": a.get("summary", ""),
        "adversarial": {"versions_checked": v.n + 1, "refuted_at_revisions": refuted},
        "human_decision": rv.get("decision"), "review_key": st["review_key"],
        "reviewer_notes": stage2.review_comments(rv),
        "final_check": {"status": st["final_check_status"], "note": fc_note},
        "also_in": [],
    }
    missing = [k for k in stage2.FINAL_FIELDS if k not in row]
    bad_hops = [h["id"] for h in hops if any(k not in h for k in stage2.FINAL_HOP_FIELDS)]
    if missing or bad_hops or row["human_decision"] != "accept" or row["audit_verdict"] not in ("pass", "pass_with_fixes") \
            or row["final_check"]["status"] not in ("passed", "exempt", "repaired"):
        raise Stop(f"{where}: the final record breaks the contract (missing {missing}, hops {bad_hops}, decision "
                   f"{row['human_decision']!r}, verdict {row['audit_verdict']!r}, final check {row['final_check']['status']!r})")
    return row


def dup_key(row) -> tuple:
    ans = row["source"]["answer"]
    ans = sorted(norm_ws(str(x)).lower() for x in (ans if isinstance(ans, list) else [ans]))
    return norm_ws(row["source"]["question"]).lower(), tuple(ans)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sets", nargs="*")
    ap.add_argument("--reviews", help="review store (default: <run>/review/reviews/reviews.json or $CMASS_REVIEWS)")
    ap.add_argument("--partial", action="store_true", help="write what is decided and list the questions in progress")
    a = ap.parse_args()
    run = Run()
    corpus = Corpus(run)
    reviews = stage2.load_reviews(run, a.reviews)
    check = stage2_state.audit_checker(run, corpus)
    sets = a.sets or list(run.config.sets)
    final = {s: [] for s in sets}
    excluded = {s: [] for s in sets}
    pending, kept, errors = [], {}, []
    for sid in sets:
        for rec in run.records(sid):
            st = stage2.question_state(run, sid, rec, reviews, check)
            qid = rec["record_id"]
            if st["next"] == "excluded":
                excluded[sid].append({"record_id": qid, "set": sid, "stage": st["stage"], "reason": st["reason"],
                                      "detail": st["detail"], "revision": st["revision"]})
                continue
            if st["next"] == "audit" and (reviews.get(st["review_key"]) or {}).get("decision") == "accept" \
                    and stage2.load_json(run.audit_file(sid, qid)) is not None:
                raise Stop(f"{sid}/{qid}: accepted at {st['review_key']}, but its audit no longer validates: {st['detail']} "
                           "(finalization stopped; nothing written)")
            if st["next"] != "ready":
                pending.append(f"{sid}/{qid}: next {st['next']} ({st['detail']})")
                continue
            c = check_records.Checker(run, corpus, sid, rec)
            c.check_revisions(), c.check_adversarial({"hunt", "verify"}), c.check_final_checks(), c.check_audit()
            errors += [x for x in c.lines if x.startswith("ERROR")]
            row = build(run, corpus, sid, rec, st, reviews)
            k = dup_key(row)
            if k in kept:
                first = kept[k]
                if norm_ws(first["question"]) != norm_ws(row["question"]) or \
                        not stage2.same_answer(first["answer"], row["answer"]):
                    raise Stop(f"{sid}/{qid} and {first['set']}/{first['record_id']}: the same source question was "
                               "accepted in two different versions; resolve it in review before finalizing")
                first["also_in"].append({"set": sid, "record_id": qid, "review_key": row["review_key"]})
                excluded[sid].append({"record_id": qid, "set": sid, "stage": "validation", "reason": "duplicate",
                                      "detail": f"same source question and answer as {first['set']}/{first['record_id']}, "
                                                "included there once", "revision": st["revision"]})
                continue
            kept[k] = row
            final[sid].append(row)
    if errors:
        raise Stop("check_records errors in retained records (finalization stopped; nothing written):\n  "
                   + "\n  ".join(errors))
    if pending:
        print(f"{len(pending)} question(s) still in progress:\n  " + "\n  ".join(pending))
        if not a.partial:
            raise Stop("finalization stopped: finish them, or pass --partial to write what is decided")
    for sid in sets:
        write_jsonl(run.final_file(sid, "final"), final[sid])
        write_jsonl(run.final_file(sid, "excluded"), excluded[sid])
        print(f"{sid}: final {len(final[sid])} {dict(Counter(r['category'] for r in final[sid]))} | excluded "
              f"{len(excluded[sid])} {dict(Counter(r['stage'] for r in excluded[sid]))}")


if __name__ == "__main__":
    main()
