"""Stage 2 state: what exists on disk for a question and what it needs next (C1-C6).

Every Stage 2 tool and workflow decides from this module, never from an agent's own report: a step is
done when its file exists and checks clean. The record formats are in stage2/SCHEMA.md; the flow is:

  C1  the candidate record is checked (candidate_problems); a malformed record is excluded (validation)
  C2-C3  audit of the current version (work/<set>/<qid>.audit.json, validate.py) and a second reader
  C4  a repair attempt per trigger (revisions/<set>/<qid>.r<N>.json); the repaired version repeats C1-C6
  C5  hunters and a verifier (adversarial/), then the human decision (review store)
  C6  the final check (final_check/), for sets whose config has final_check true

Versions: revision 0 is the source question and answer; revision N is the version written by the N-th
repair attempt. Adversarial and final-check files carry the version in their name (".r<N>" for N > 0),
and a human decision is keyed to the version it was made on, so no approval carries over to a revision.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .records import answer_type, gold_answer, hop_doc_ids, question_text
from .text import norm_ws

ANSWER_TYPES = ("single", "set", "aggregate")
TRIGGER_STAGES = ("audit", "adversarial", "human", "final_check")
REVISION_OUTCOMES = ("evidence_only", "revised", "unsuccessful")
QUESTION_EDITS = ("correct_wording", "add_qualifier", "correct_clue", "remove_clue", "adjust_range",
                  "remove_source_clause", "adjust_year_or_threshold", "change_attribute_if_trivial")
ANSWER_EDITS = ("recompute_answer", "correct_answer_typo")
HOP_CHANGES = ("evidence_added", "added", "dropped", "retyped", "regrounded")
HUNT_KINDS = {"hunt": "all", "hunt_list": "list", "hunt_named": "named"}
FINDING_KINDS = ("rival", "missing_member", "wrong_member", "conflicting_value", "alternative_reading",
                 "ungrounded_hop", "other")
CONFIDENCE = ("certain", "likely", "possible")
RULINGS = ("refutes", "does_not_refute")
FC_STATUS = ("passed", "failed")
GAP_KINDS = ("evidence", "wording", "answer")
EXCLUDE_STAGES = ("audit", "revision", "adversarial", "human", "final_check", "validation")
DECISIONS = ("accept", "accept_with_fixes", "reject", "unsure")
NEXT = ("audit", "second_read", "revise", "hunt", "verify", "review", "final_check", "ready", "excluded")


# ---------------------------------------------------------------------------------------------- files
def to_int(x, default: int = -1) -> int:
    """An integer field an agent wrote, or default when it is missing or malformed."""
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def load_json(path: Path):
    """The parsed file, None when it does not exist, or {"_error": msg} when it is not valid JSON."""
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        return {"_error": f"invalid JSON in {path}: {e}"}


def adv_path(run, sid: str, qid: str, kind: str, version: int) -> Path:
    return run.adversarial_file(sid, qid, kind if version == 0 else f"{kind}.r{version}")


def fc_path(run, sid: str, qid: str, version: int) -> Path:
    p = run.final_check_file(sid, qid)
    return p if version == 0 else p.with_name(f"{qid}.r{version}.json")


def archived_audit_path(run, sid: str, qid: str, version: int) -> Path:
    return run.work_dir(sid) / f"{qid}.audit.r{version}.json"


def reopen_path(run, sid: str, qid: str) -> Path:
    return run.work_dir(sid) / f"{qid}.reopen.json"


def reviews_path(run, override=None) -> Path:
    return Path(override or os.environ.get("CMASS_REVIEWS") or run.review_dir() / "reviews" / "reviews.json")


def load_reviews(run, override=None) -> dict:
    p = reviews_path(run, override)
    data = load_json(p)
    if data is None:
        return {}
    if "_error" in data:
        raise SystemExit(data["_error"])
    return data


def load_revisions(run, sid: str, qid: str) -> list[dict]:
    """Revision attempts r1, r2, ... in order, stopping at the first missing number. "_n" is the number in the file
    name, which decides the version (check_records.py checks that the file's own "attempt" agrees)."""
    out, n = [], 1
    while True:
        r = load_json(run.revision_file(sid, qid, n))
        if r is None:
            return out
        r.setdefault("attempt", n)
        r["_n"] = n
        r["_path"] = str(run.revision_file(sid, qid, n))
        out.append(r)
        n += 1


def searchlog(run, sid: str, qid: str) -> list[dict]:
    p = run.searchlog(sid, qid)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------------------------- answers
def same_answer(a, b) -> bool:
    def norm(x):
        xs = x if isinstance(x, list) else [x]
        return sorted(norm_ws(str(v)).lower() for v in xs)
    return norm(a) == norm(b)


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def is_typo_fix(old, new) -> bool:
    """A typo correction: a small character-level change to a single answer string."""
    if isinstance(old, list) or isinstance(new, list):
        return False
    a, b = norm_ws(str(old)), norm_ws(str(new))
    d = edit_distance(a.lower(), b.lower())
    return 0 < d <= max(2, len(a) // 10) or (d == 0 and a != b)


def answer_display(ans) -> str:
    return " | ".join(map(str, ans)) if isinstance(ans, list) else str(ans)


# ---------------------------------------------------------------------------------------------- C1
def candidate_problems(rec: dict, default_type: str) -> tuple[list[str], list[str]]:
    """C1 check of a candidate record: (errors, warnings). Any error excludes it (stage validation)."""
    errs, warns = [], []
    if not norm_ws(question_text(rec)):
        errs.append("no question text")
    g = gold_answer(rec)
    if g in (None, "", []):
        errs.append("no answer")
    at = answer_type(rec, default_type)
    if at not in ANSWER_TYPES:
        errs.append(f"answer_type {at!r} is not one of {ANSWER_TYPES}")
    if at == "single" and isinstance(g, list) and len(g) > 1:
        errs.append("answer_type is single but the answer lists several members")
    hs = rec.get("hops")
    if hs is None:
        warns.append("no hops: every hop of the audit will be an added hop")
    elif not isinstance(hs, list):
        errs.append("hops is not a list")
    else:
        for i, h in enumerate(hs, 1):
            if not isinstance(h, dict) or not norm_ws(h.get("clue") or ""):
                errs.append(f"hop {i} has no clue")
            elif h.get("kind") != "derivation" and not hop_doc_ids(h):
                warns.append(f"hop {i} cites no document")
    return errs, warns


def set_problems(records: list[dict]) -> list[str]:
    seen, errs = set(), []
    for r in records:
        if r["record_id"] in seen:
            errs.append(f"duplicate record_id {r['record_id']!r}")
        seen.add(r["record_id"])
    return errs


# ---------------------------------------------------------------------------------------------- versions
class Version:
    """The current version of a question: the source (revision 0) or the last successful repair."""

    def __init__(self, rec: dict, revisions: list[dict]):
        self.source_question = question_text(rec)
        self.source_answer = gold_answer(rec)
        self.revisions = revisions
        self.failed = None
        self.n, self.question, self.answer = 0, self.source_question, self.source_answer
        for r in revisions:
            if r.get("outcome") == "unsuccessful":
                self.failed = r
                break
            if r.get("outcome") in ("evidence_only", "revised"):
                self.n = r.get("_n") or to_int(r.get("attempt"), 0)
                self.question = r.get("question", self.question)
                self.answer = r.get("answer", self.answer)
        self.question_changed = norm_ws(self.question) != norm_ws(self.source_question)
        self.answer_changed = not same_answer(self.answer, self.source_answer)
        self.changed = self.question_changed or self.answer_changed

    def accepted_edits(self) -> list[dict]:
        out = []
        for r in self.revisions:
            n = r.get("_n") or to_int(r.get("attempt"), 0)
            if r.get("outcome") == "revised" and n <= self.n:
                for e in r.get("edits") or []:
                    out.append({"revision": n, **e})
        return out


def site_ids(sid: str, qid: str, version: Version) -> tuple[str, str]:
    """(review-site dataset id, review-site record id) of a version: a changed question or answer is reviewed
    in the rewrites table rw_<set>; a revised version gets its own record id, so its decision is its own."""
    ds = f"rw_{sid}" if version.changed else sid
    rid = qid if version.n == 0 else f"{qid}.r{version.n}"
    return ds, rid


def needs_two_hunters(cfg_set: dict, rec: dict, version: Version) -> bool:
    """C5: aggregates and changed answers get two hunters with complementary routes (list documents and
    named candidates); a set may force either choice with "two_hunters"."""
    if "two_hunters" in cfg_set:
        return bool(cfg_set["two_hunters"])
    return answer_type(rec, cfg_set["answer_type"]) == "aggregate" or version.answer_changed


# ---------------------------------------------------------------------------------------------- the state
def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def question_state(run, sid: str, rec: dict, reviews: dict, audit_check=None) -> dict:
    """What exists on disk for one question and what it needs next.

    audit_check(sid, rec, audit) -> list of validate.py lines; ERROR lines make the audit unfinished.
    The result's "next" is one of NEXT; for "excluded" it also has stage, reason and detail; for "revise"
    it has the trigger the repair answers."""
    cfg = run.config.set(sid)
    qid = rec["record_id"]
    revs = load_revisions(run, sid, qid)
    broken = next((i for i, r in enumerate(revs) if "_error" in r), None)
    broken_note = revs[broken]["_error"] if broken is not None else ""
    if broken is not None:          # an unreadable attempt is redone: the state is computed without it
        revs = revs[:broken]
    v = Version(rec, revs)
    two = needs_two_hunters(cfg, rec, v)
    kinds = ["hunt_list", "hunt_named"] if two else ["hunt"]
    site_ds, site_rid = site_ids(sid, qid, v)
    st = {
        "set": sid, "qid": qid, "revision": v.n, "attempt": len(revs) + 1,
        "next": "", "detail": "", "stage": "", "reason": "",
        "trigger": {"stage": "", "ref": "", "reasons": [], "evidence_only": False},
        "family": cfg["family"], "group": cfg["group"], "permitted": run.config.permitted_revisions(sid),
        "answer_may_change": {True: "yes", False: "no"}.get(cfg.get("answer_may_change"), str(cfg.get("answer_may_change"))),
        "answer_type": answer_type(rec, cfg["answer_type"]), "question": v.question, "answer": v.answer,
        "answer_text": answer_display(v.answer),
        "question_changed": v.question_changed, "answer_changed": v.answer_changed,
        "final_check_enabled": bool(cfg.get("final_check")),
        "site_dataset": site_ds, "site_record_id": site_rid, "review_key": f"{site_ds}~{site_rid}", "decision": "",
        "audit_file": str(run.audit_file(sid, qid)), "packet_file": str(run.packet(sid, qid)),
        "revision_file": str(run.revision_file(sid, qid, len(revs) + 1)),
        "hunts": [{"kind": k, "path": str(adv_path(run, sid, qid, k, v.n)), "done": False} for k in kinds],
        "verify_file": str(adv_path(run, sid, qid, "verify", v.n)),
        "final_check_file": str(fc_path(run, sid, qid, v.n)),
        "final_check_status": "", "warnings": [],
    }

    def done(nxt, detail="", **kw):
        st.update(next=nxt, detail=detail, **kw)
        return st

    def excluded(stage, reason, detail):
        return done("excluded", detail, stage=stage, reason=reason)

    def revise(stage, ref, reasons, evidence_only=False):
        return done("revise", f"repair attempt {st['attempt']} for the {stage} finding",
                    trigger={"stage": stage, "ref": ref, "reasons": [r for r in reasons if r],
                             "evidence_only": evidence_only})

    # C1
    errs, warns = candidate_problems(rec, cfg["answer_type"])
    st["warnings"] = warns + ([f"{broken_note}; that attempt is redone"] if broken_note else [])
    if errs:
        return excluded("validation", "invalid_candidate", "; ".join(errs))
    if v.failed:
        return excluded("revision", "repair_unsuccessful",
                        f"attempt {v.failed['_n']}: {v.failed.get('blocker') or v.failed.get('note') or ''}")

    # C2-C3: the audit of the current version, valid, read twice
    afile = run.audit_file(sid, qid)
    a = load_json(afile)
    if a is None:
        return done("audit", "no audit file" if v.n == 0 else f"revision {v.n} has not been re-audited")
    if "_error" in a:
        return done("audit", a["_error"])
    if to_int(a.get("revision", 0)) != v.n:
        return done("audit", f"the audit is of revision {a.get('revision', 0)!r}; the current version is revision {v.n}")
    if _mtime(reopen_path(run, sid, qid)) > _mtime(afile):
        note = (load_json(reopen_path(run, sid, qid)) or {}).get("note", "")
        return done("audit", f"reopened after a change of audit rule: {note}")
    if audit_check:
        errors = [x for x in audit_check(sid, rec, a) if x.startswith("ERROR")]
        if errors:
            return done("audit", f"{len(errors)} validate.py error(s): {errors[0]}")
    if not isinstance(a.get("verification"), dict) or a["verification"].get("result") not in ("confirmed", "revised"):
        return done("second_read", "the audit has no second reader's verification block")
    if a.get("verdict") == "fail":
        reasons = list(a.get("fail_reasons") or [])
        if "contamination" in reasons:
            return excluded("audit", "contamination", a.get("summary", ""))
        if v.n == 0:
            return revise("audit", str(afile), reasons + [a.get("summary", "")])
        return excluded("revision", "reaudit_failed",
                        f"the re-audit of revision {v.n} fails ({', '.join(reasons)}): {a.get('summary', '')}")
    if a.get("verdict") not in ("pass", "pass_with_fixes"):
        return done("audit", f"unknown verdict {a.get('verdict')!r}")

    # C5: hunters, then the verifier
    for h in st["hunts"]:
        x = load_json(Path(h["path"]))
        h["done"] = x is not None and "_error" not in x
    missing = [h["kind"] for h in st["hunts"] if not h["done"]]
    if missing:
        return done("hunt", f"no {', '.join(missing)} file for revision {v.n}")
    ver = load_json(Path(st["verify_file"]))
    if ver is None:
        return done("verify", f"no verifier rulings for revision {v.n}")
    if "_error" in ver:
        return done("verify", ver["_error"])
    if ver.get("refuted"):
        refuted = [n for n in range(v.n + 1) if (load_json(adv_path(run, sid, qid, "verify", n)) or {}).get("refuted")]
        if len(refuted) >= 2:
            return excluded("adversarial", "second_refutation",
                            f"verified refutations at revisions {refuted}: " + "; ".join(ver.get("blocking") or []))
        return revise("adversarial", st["verify_file"], list(ver.get("blocking") or []) + [ver.get("note", "")])

    # C5: the human decision on this version
    rv = reviews.get(st["review_key"]) or {}
    d = rv.get("decision")
    st["decision"] = d or ""
    if d not in ("accept", "accept_with_fixes", "reject"):
        return done("review", f"awaiting the reviewer's decision at {st['review_key']}")
    comments = review_comments(rv)
    if d == "reject":
        return excluded("human", "rejected", "; ".join(c["comment"] for c in comments if c["comment"]) or "rejected")
    if d == "accept_with_fixes":
        return revise("human", st["review_key"], [f"{c['on']}{' ' + c['id'] if c['id'] else ''}: {c['comment']}"
                                                   for c in comments if c["comment"]] or ["accepted with fixes"])

    # C6: the final check (or its exemption)
    if not st["final_check_enabled"]:
        st["final_check_status"] = "exempt"
        return done("ready", "accepted; the final check does not apply to this set")
    fc = load_json(fc_path(run, sid, qid, v.n))
    if fc is None:
        return done("final_check", f"accepted at {st['review_key']}; no final check of revision {v.n}")
    if "_error" in fc:
        return done("final_check", fc["_error"])
    earlier_failures = [n for n in range(v.n) if (load_json(fc_path(run, sid, qid, n)) or {}).get("status") == "failed"]
    if fc.get("status") == "failed":
        why = "; ".join(list(fc.get("blocking") or []) + [g.get("what", "") for g in fc.get("gaps") or []])
        if earlier_failures:
            return excluded("final_check", "second_failure", f"failed again at revision {v.n}: {why}")
        if not fc.get("repairable"):
            return excluded("final_check", "not_repairable", why)
        gaps = fc.get("gaps") or []
        evidence_only = bool(gaps) and all(g.get("kind") == "evidence" for g in gaps)
        return revise("final_check", st["final_check_file"], [why], evidence_only=evidence_only)
    if fc.get("status") != "passed":
        return done("final_check", f"unknown final-check status {fc.get('status')!r}")
    st["final_check_status"] = "repaired" if earlier_failures else "passed"
    return done("ready", "accepted and passed the final check" + (" after a repair" if earlier_failures else ""))


def review_comments(rv: dict) -> list[dict]:
    """The reviewer's comments and disagreements: [{on, id, agree, comment}]."""
    out = []
    if norm_ws(rv.get("comment") or ""):
        out.append({"on": "question", "id": "", "agree": None, "comment": norm_ws(rv["comment"])})
    for scope, label in (("constraints", "constraint"), ("hops", "hop")):
        for k, x in sorted((rv.get(scope) or {}).items()):
            x = x or {}
            agree = x.get("agree")
            agree = {"agree": True, "disagree": False}.get(agree, agree)
            c = norm_ws(x.get("comment") or "")
            if c or agree is False:
                out.append({"on": label, "id": k, "agree": agree, "comment": c})
    return out


def reopen(run, sid: str, qid: str, note: str) -> Path:
    p = reopen_path(run, sid, qid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"note": note, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, ensure_ascii=False))
    return p


# ---------------------------------------------------------------------------------------------- output records
# The contract with Stage 3 (stage2/SCHEMA.md "Output records"): every final record has these fields.
FINAL_FIELDS = ("record_id", "set", "source", "question", "answer", "answer_type", "edits", "edit_rationale",
                "constraints", "hops", "candidates", "completeness", "search_queries", "audit_verdict",
                "human_decision", "reviewer_notes", "final_check")
FINAL_HOP_FIELDS = ("id", "clue", "type", "role", "status", "evidence")
EXCLUDED_FIELDS = ("record_id", "set", "stage", "reason", "detail")
CATEGORIES = ("unchanged", "reworded", "constraint_removed", "narrowed", "answer_changed")


def category(version: "Version") -> str:
    """The revision category of the paper's yield table; an answer change takes precedence over question edits."""
    if version.answer_changed:
        return "answer_changed"
    if not version.question_changed:
        return "unchanged"
    kinds = {e.get("kind") for e in version.accepted_edits()}
    if "add_qualifier" in kinds:
        return "narrowed"
    if kinds & {"remove_clue", "remove_source_clause"}:
        return "constraint_removed"
    return "reworded"
