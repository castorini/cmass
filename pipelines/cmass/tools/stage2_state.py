#!/usr/bin/env python3
"""Report, per question, what Stage 2 files exist on disk and what the question needs next.

  stage2_state.py                      # every question of every configured set: a table and the counts
  stage2_state.py SET [QID ...]        # one set, or some of its questions
  stage2_state.py ... --json           # {"questions": [state, ...]} for workflows (one object per question)
  stage2_state.py ... --next hunt,verify   # only the questions whose next step is one of these
  stage2_state.py ... --brief          # only set, qid, revision, next (with --json: for long lists)
  stage2_state.py --reopen SET QID --note "the audit rule that changed"

The workflows (stage2/*.js) run this before and after every agent step, so they resume from disk and never
trust an agent's own report. "next" is one of: audit, second_read, revise, hunt, verify, review,
final_check, ready, excluded (with stage, reason, detail). The logic is cmass/stage2.py; an audit counts
only when validate.py reports 0 errors for it. "record_errors" lists check_records.py's errors for the
question's revision, adversarial and final-check files (a workflow has the agent that wrote a file fix
it). Run inside a run directory (or set $CMASS_RUN).

--reopen marks a question whose audit must be redone because a human-review decision changed an audit
rule (C4: "earlier records affected by that rule are reopened"); its next step becomes "audit".
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_records  # noqa: E402
import validate  # noqa: E402
from cmass import stage2  # noqa: E402
from cmass.corpus import Corpus  # noqa: E402
from cmass.run import Run  # noqa: E402


def audit_checker(run, corpus):
    def check(sid, rec, a):
        return validate.check(corpus, sid, rec, a, run.config.set(sid)["answer_type"])
    return check


def states(run, sets=None, qids=None, reviews_file=None, corpus=None) -> list[dict]:
    corpus = corpus or Corpus(run)
    reviews = stage2.load_reviews(run, reviews_file)
    check = audit_checker(run, corpus)
    out = []
    for sid in sets or list(run.config.sets):
        recs = run.records(sid)
        for e in stage2.set_problems(recs):
            print(f"warning {sid}: {e}", file=sys.stderr)
        for rec in recs:
            if qids and rec["record_id"] not in qids:
                continue
            st = stage2.question_state(run, sid, rec, reviews, check)
            c = check_records.Checker(run, corpus, sid, rec)
            c.check_revisions(), c.check_adversarial({"hunt", "verify"}), c.check_final_checks(), c.check_audit()
            st["record_errors"] = [x for x in c.lines if x.startswith("ERROR")][:8]
            st["reviews_file"] = str(stage2.reviews_path(run, reviews_file))
            out.append(st)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("set", nargs="?")
    ap.add_argument("qids", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--next", help="only questions whose next step is one of these (comma-separated)")
    ap.add_argument("--brief", action="store_true", help="only set, qid, revision and next")
    ap.add_argument("--reviews", help="review store (default: <run>/review/reviews/reviews.json or $CMASS_REVIEWS)")
    ap.add_argument("--reopen", action="store_true", help="mark SET QID ... for re-audit (needs --note)")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    run = Run()
    if a.reopen:
        if not (a.set and a.qids and a.note):
            sys.exit("--reopen needs SET, at least one QID and --note")
        for q in a.qids:
            run.record(a.set, q)
            print("reopened:", stage2.reopen(run, a.set, q, a.note))
        return
    if a.qids and not a.set:
        sys.exit("QIDs need a SET")
    rows = states(run, [a.set] if a.set else None, set(a.qids) or None, a.reviews)
    if a.next:
        rows = [r for r in rows if r["next"] in a.next.split(",")]
    if a.brief:
        rows = [{k: r[k] for k in ("set", "qid", "revision", "next", "detail", "stage", "reason")} for r in rows]
    if a.json:
        print(json.dumps({"questions": rows}, ensure_ascii=False))
        return
    for r in rows:
        what = f"{r['stage']}/{r['reason']}: " if r["next"] == "excluded" else ""
        bad = f" [{len(r['record_errors'])} record error(s)]" if r.get("record_errors") else ""
        print(f"{r['set']:<14} {r['qid']:<16} r{r['revision']:<2} {r['next']:<12} {what}{r['detail'][:110]}{bad}")
    c = Counter(r["next"] if r["next"] != "excluded" else f"excluded:{r['stage']}" for r in rows)
    print(f"({len(rows)} questions) " + ", ".join(f"{k} {n}" for k, n in sorted(c.items())))


if __name__ == "__main__":
    main()
