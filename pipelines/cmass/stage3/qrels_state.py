#!/usr/bin/env python3
"""What exists on disk for one question's Stage 3 judging, so a workflow resumes from disk and never trusts an agent's
report: a judge whose file reads were refused can still answer "0 check errors" without having written a verdict.

  qrels_state.py --set S QID [--phase2 N]       one JSON line for the question
  qrels_state.py --set S --all [--phase2 N]     one line per question, then a summary line

Fields
  has_standard      standards/QID.json exists and passes std-check
  missing_recall    live hops that need recall and have no candidate list yet (has_candidates: none missing)
  chunked           chunks.json exists and every document to judge is in a chunk (unchunked: how many are not)
  chunks, unjudged  all first-judge chunk ids; those without a clean verdict file (missing, unreadable or failing check)
  rounds            per second-judge round: rendered set ids, missing (no clean verdict file), compared,
                    first_drop_second_keep, adj_parts, adj_missing (no clean adjudicated file), complete
  pending_round     the lowest rendered round that is not complete (finish it first), or null
  next_round        the round to render next, {"round": N, "flags": "" | "--drops-only"}, or null. A policy round is
                    due while some judged document is in no round's population (round 1, or a back-fill after the
                    pool grew); a drops-only round when the policy rounds turned escalate_at or more sampled drops into
                    keeps and some drop has not been re-judged
  complete          nothing is left to do; blocking says what is missing otherwise
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qrels as Q  # noqa: E402


def _newer(path: Path, files) -> bool:
    """path exists and is at least as new as every existing file in files."""
    if not path.exists():
        return False
    t = path.stat().st_mtime
    return all(not f.exists() or f.stat().st_mtime <= t for f in files)


def state(q, qid: str) -> dict:
    r = q.row(qid)
    wd, jd = q.work / qid, q.judged / qid
    std_errs = Q.standard_errors(q, r)
    needs = [] if q.p2 else r["needs_recall"]
    cf = q.recall_file(qid, "candidates")
    have = set(json.loads(cf.read_text())["hops"]) if cf.exists() else set()
    missing_recall = [h for h in needs if h not in have]
    cj = wd / "chunks.json"
    chunks = json.loads(cj.read_text()) if cj.exists() else None
    miss = q.missing(qid)
    chunked_docs = {d for ch in (chunks or {}).values() for d in ch}
    unchunked = [d for d in q.candidates(r) if d not in chunked_docs and d not in miss]
    unjudged = [c for c, docs in (chunks or {}).items()
                if Q.check_file(q, r, docs, jd / f"chunk_{c}.json", f"chunk {c}")]
    to_judge = q.to_judge(r)
    out = {"qid": qid, "set": q.sid, "phase": q.phase_label(), "has_standard": not std_errs,
           "std_errors": len(std_errs), "needs_recall": needs, "missing_recall": missing_recall,
           "has_candidates": not missing_recall, "chunked": chunks is not None and not unchunked,
           "chunks": list(chunks or {}), "unchunked": len(unchunked), "unjudged": unjudged, "to_judge": len(to_judge),
           "not_in_corpus": len(miss), "rounds": {}, "pending_round": None, "next_round": None}
    first_done = chunks is not None and not unchunked and not unjudged
    rounds = q.rounds(qid)
    for rnd, m in rounds.items():
        stem, _ = Q.round_tags(rnd)
        files = [jd / f"{stem}_{c}.json" for c in m["chunks"]]
        st = {"drops_only": bool(m.get("drops_only")), "rendered": list(m["chunks"]),
              "missing": [c for c, docs in m["chunks"].items()
                          if Q.check_file(q, r, docs, jd / f"{stem}_{c}.json", f"{stem} {c}")]}
        cp = jd / f"{stem}_compare.json"
        st["compared"] = not st["missing"] and _newer(cp, files)     # a compare older than a verdict file is stale
        if st["compared"]:
            cmp_ = json.loads(cp.read_text())
            parts = cmp_.get("parts") or {}
            st.update({"first_drop_second_keep": cmp_.get("first_drop_second_keep", 0), "disagree": cmp_.get("disagree", 0),
                       "adj_parts": list(parts),
                       "adj_missing": [p for p, docs in parts.items()
                                       if Q.check_file(q, r, docs, jd / f"adjudicated_{p}.json", f"adjudicated {p}")]})
        st["complete"] = st["compared"] and not st.get("adj_missing")
        out["rounds"][str(rnd)] = st
    pending = [rnd for rnd in rounds if not out["rounds"][str(rnd)]["complete"]]
    out["pending_round"] = pending[0] if pending else None
    if first_done and not pending:
        first = q.first_verdicts(qid)
        considered = {d for m in rounds.values() for d in m["population"]}
        second_judged = {d for m in rounds.values() for ch in m["chunks"].values() for d in ch}
        nxt = (max(rounds) + 1) if rounds else 1
        if [d for d in to_judge if d not in considered]:
            out["next_round"] = {"round": nxt, "flags": ""}
        else:
            flips = sum(st.get("first_drop_second_keep", 0) for st in out["rounds"].values() if not st["drops_only"])
            left = [d for d in to_judge if not first[d].get("keep") and d not in second_judged]
            if flips >= q.tiers()["escalate_at"] and left:
                out["next_round"] = {"round": nxt, "flags": "--drops-only"}
    blocking = ("standard" if std_errs else "recall" if missing_recall else "chunks" if not out["chunked"]
                else "first judge" if unjudged else f"round {out['pending_round']}" if pending
                else f"round {out['next_round']['round']} not rendered" if out["next_round"] else "")
    out["blocking"] = blocking
    out["complete"] = not blocking
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run")
    ap.add_argument("--set", dest="sid")
    ap.add_argument("--phase2", type=int, metavar="N")
    ap.add_argument("qid", nargs="?")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    run = Q.Run(a.run)
    q = Q.QSet(run, a.sid or Q.default_set(run), a.phase2)
    if a.all:
        qids = (list(dict.fromkeys(x["id"] for x in q.pairs() if x["kind"] == "near" and x["status"] == "new"))
                if q.p2 else list(q.inventory()))
        rows = [state(q, x) for x in qids]
        for s in rows:
            print(json.dumps(s))
        print(json.dumps({"set": q.sid, "phase": q.phase_label(), "questions": len(rows),
                          "complete": sum(s["complete"] for s in rows),
                          "incomplete": {s["qid"]: s["blocking"] for s in rows if not s["complete"]}}))
    elif a.qid:
        print(json.dumps(state(q, a.qid)))
    else:
        ap.error("pass a QID or --all")


if __name__ == "__main__":
    main()
