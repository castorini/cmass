#!/usr/bin/env python3
"""Check the Stage 2 records other than the audit itself against stage2/SCHEMA.md and the set's rules.

  check_records.py                                  # every question of every configured set
  check_records.py SET [QID ...]                    # one set, or some of its questions
  check_records.py SET QID --only revision,hunt     # some kinds: revision, hunt, verify, final_check, audit

Checked: revision attempts (revisions/<set>/<qid>.r<N>.json) against the permitted revisions of the set's
family (config.py), e.g. a question edit only of a permitted kind, an answer change only when the family
allows it; hunter findings and verifier rulings (adversarial/); final-check results (final_check/); and
the second reader's block of the audit (the audit itself is validate.py's). Every quote is checked
verbatim against its document. Exit status 1 if any ERROR. Run inside a run directory (or set $CMASS_RUN).
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cmass import stage2  # noqa: E402
from cmass.corpus import Corpus  # noqa: E402
from cmass.records import answer_type  # noqa: E402
from cmass.run import Run  # noqa: E402
from cmass.text import is_verbatim, norm_ws  # noqa: E402

KINDS = ("revision", "hunt", "verify", "final_check", "audit")
ADV_RE = re.compile(r"^(hunt|hunt_list|hunt_named|verify)(?:\.r(\d+))?$")
FC_RE = re.compile(r"^(?:\.r(\d+))?$")


class Checker:
    def __init__(self, run, corpus, sid, rec):
        self.run, self.corpus, self.sid, self.rec = run, corpus, sid, rec
        self.qid = rec["record_id"]
        self.cfg = run.config.set(sid)
        self.lines = []

    def E(self, where, msg):
        self.lines.append(f"ERROR {self.sid}/{self.qid} {where}: {msg}")

    def W(self, where, msg):
        self.lines.append(f"warn  {self.sid}/{self.qid} {where}: {msg}")

    def keys(self, where, obj, keys):
        if not isinstance(obj, dict):
            self.E(where, "not a JSON object")
            return False
        miss = [k for k in keys if k not in obj]
        if miss:
            self.E(where, f"missing key(s) {miss}")
        if "_error" in obj:
            self.E(where, obj["_error"])
            return False
        if str(obj.get("record_id", self.qid)) != self.qid or obj.get("set", self.sid) != self.sid:
            self.E(where, f"record_id/set {obj.get('record_id')!r}/{obj.get('set')!r} do not match the file's question")
        return not miss

    def quotes(self, where, doc_id, quotes):
        text = self.corpus.get_document(doc_id) if doc_id else None
        if text is None:
            self.E(where, f"document {doc_id!r} not found")
            return
        for q in quotes or []:
            if not isinstance(q, str) or not is_verbatim(text, q):
                self.E(where, f"quote not verbatim in {doc_id}: {str(q)[:90]!r}")

    def version_matches(self, where, obj, version):
        if norm_ws(obj.get("question") or "") != norm_ws(version.question):
            self.E(where, f"question is not revision {version.n}'s question")
        if not stage2.same_answer(obj.get("answer", ""), version.answer):
            self.E(where, f"answer is not revision {version.n}'s answer")

    # ------------------------------------------------------------------ versions
    def versions(self):
        """Every version 0..last successful attempt, as stage2.Version objects."""
        revs = stage2.load_revisions(self.run, self.sid, self.qid)
        out = {0: stage2.Version(self.rec, [])}
        for i in range(len(revs)):
            ok = [r for r in revs[:i + 1] if "_error" not in r]
            v = stage2.Version(self.rec, ok)
            if v.n == i + 1 and i + 1 not in out:
                out[i + 1] = v
        return revs, out

    # ------------------------------------------------------------------ C4
    def check_revisions(self):
        revs, versions = self.versions()
        files = sorted(self.run.revision_file(self.sid, self.qid, 1).parent.glob(f"{self.qid}.r*.json"))
        numbers = sorted(int(m.group(1)) for p in files if (m := re.fullmatch(re.escape(self.qid) + r"\.r(\d+)\.json", p.name)))
        if numbers != list(range(1, len(numbers) + 1)):
            self.E("revisions", f"attempt numbers {numbers} are not consecutive from 1")
        permitted = set(self.run.config.permitted_revisions(self.sid)) - {"evidence_only"}
        may_change = self.cfg.get("answer_may_change")
        max_q = stage2.to_int(self.cfg.get("max_added_qualifiers", 1), 1)
        n_qual = 0
        prev = versions[0]
        for i, r in enumerate(revs, 1):
            w = f"r{i}"
            if not self.keys(w, r, ("attempt", "trigger", "based_on_revision", "evidence_retry", "outcome", "note")):
                continue
            if stage2.to_int(r.get("attempt")) != i:
                self.E(w, f"attempt is {r.get('attempt')!r}, the file name says {i}")
            if i > 1 and revs[i - 2].get("outcome") == "unsuccessful":
                self.E(w, "an attempt after an unsuccessful one: an unsuccessful repair excludes the question")
            if stage2.to_int(r.get("based_on_revision")) != prev.n:
                self.E(w, f"based_on_revision is {r.get('based_on_revision')!r}; the version it revises is revision {prev.n}")
            t = r.get("trigger") or {}
            if t.get("stage") not in stage2.TRIGGER_STAGES:
                self.E(w, f"trigger.stage must be one of {stage2.TRIGGER_STAGES}")
            if not isinstance(t.get("reasons"), list) or not t.get("ref"):
                self.E(w, "trigger needs ref and a reasons list")
            self.check_trigger(w, t, prev)
            er = r.get("evidence_retry") or {}
            if not isinstance(er.get("queries"), list) or not er.get("queries"):
                self.E(w, "evidence_retry.queries must list the searches retried first (C4 retries retrieval before any edit)")
            else:
                logged = {x.get("query") for x in stage2.searchlog(self.run, self.sid, self.qid)}
                miss = [q for q in er["queries"] if q not in logged]
                if miss:
                    self.W(w, f"evidence_retry queries not in the question's search log: {miss[:3]}")
            if not isinstance(er.get("resolved"), bool):
                self.E(w, "evidence_retry.resolved must be true or false")
            out = r.get("outcome")
            if out not in stage2.REVISION_OUTCOMES:
                self.E(w, f"outcome must be one of {stage2.REVISION_OUTCOMES}")
                continue
            for hc in r.get("hop_changes") or []:
                if not isinstance(hc, dict) or not hc.get("hop") or hc.get("change") not in stage2.HOP_CHANGES:
                    self.E(w, f"hop_changes entries are {{hop, change: {'|'.join(stage2.HOP_CHANGES)}, note}}: {hc!r}")
            edits = r.get("edits") or []
            if out == "unsuccessful":
                if not norm_ws(r.get("blocker") or ""):
                    self.E(w, "an unsuccessful attempt needs a blocker: what is missing and what was searched")
                if i != len(revs):
                    self.E(w, "an unsuccessful attempt must be the last one")
                continue
            if "question" not in r or "answer" not in r:
                self.E(w, "a successful attempt states the new version's question and answer")
                continue
            if out == "evidence_only":
                if er.get("resolved") is not True:
                    self.E(w, "evidence_only needs evidence_retry.resolved true")
                if edits:
                    self.E(w, "evidence_only takes no edits")
                if norm_ws(r["question"]) != norm_ws(prev.question) or not stage2.same_answer(r["answer"], prev.answer):
                    self.E(w, "evidence_only keeps the question and answer of the version it repairs")
            else:
                if t.get("evidence_only"):
                    self.E(w, "the trigger permits an evidence-only repair (a final-check grounding gap); no edits")
                n_qual += self.check_edits(w, r, prev, edits, permitted, may_change)
                if n_qual > max_q:
                    self.E(w, f"{n_qual} added qualifiers in all; the set permits {max_q} (max_added_qualifiers)")
            nxt = stage2.Version(self.rec, [x for x in revs[:i] if "_error" not in x])
            prev = nxt if nxt.n == i else prev

    def check_trigger(self, w, t, prev):
        stage, ref = t.get("stage"), str(t.get("ref") or "")
        if stage == "adversarial":
            v = stage2.load_json(stage2.adv_path(self.run, self.sid, self.qid, "verify", prev.n)) or {}
            if not v.get("refuted"):
                self.E(w, f"trigger adversarial, but the verifier did not refute revision {prev.n}")
        elif stage == "final_check":
            fc = stage2.load_json(stage2.fc_path(self.run, self.sid, self.qid, prev.n)) or {}
            if fc.get("status") != "failed":
                self.E(w, f"trigger final_check, but revision {prev.n} has no failed final check")
        elif stage == "human":
            ds, rid = stage2.site_ids(self.sid, self.qid, prev)
            if ref != f"{ds}~{rid}":
                self.E(w, f"trigger human must name the review key of revision {prev.n}: {ds}~{rid}")
        elif stage == "audit" and prev.n != 0:
            self.E(w, "an audit failure of a revised version excludes it; only the source version's audit triggers a repair")

    def check_edits(self, w, r, prev, edits, permitted, may_change) -> int:
        if not edits:
            self.E(w, "outcome revised needs edits")
            return 0
        if not norm_ws(r.get("edit_rationale") or ""):
            self.E(w, "outcome revised needs an edit_rationale")
        q_old, q_new = norm_ws(prev.question), norm_ws(r["question"])
        kinds = [e.get("kind") for e in edits if isinstance(e, dict)]
        q_kinds = [k for k in kinds if k in stage2.QUESTION_EDITS]
        a_kinds = [k for k in kinds if k in stage2.ANSWER_EDITS]
        for e in edits:
            k = e.get("kind") if isinstance(e, dict) else None
            if k not in stage2.QUESTION_EDITS + stage2.ANSWER_EDITS:
                self.E(w, f"edit kind {k!r} is not one of {stage2.QUESTION_EDITS + stage2.ANSWER_EDITS}")
                continue
            if k in stage2.QUESTION_EDITS and k not in permitted:
                self.E(w, f"edit {k!r} is not permitted for set {self.sid} (permitted: {sorted(permitted) or 'evidence_only'})")
            frm, to = norm_ws(e.get("from") or ""), norm_ws(e.get("to") or "")
            if k == "add_qualifier":
                if not to or to not in q_new:
                    self.E(w, f"add_qualifier: 'to' must be the added words, a span of the new question: {to!r}")
            elif k in ("remove_clue", "remove_source_clause"):
                if not frm or frm not in q_old:
                    self.E(w, f"{k}: 'from' must be the removed words, a span of the previous question: {frm!r}")
            elif k in stage2.QUESTION_EDITS:
                if not frm or frm not in q_old:
                    self.E(w, f"{k}: 'from' must be a span of the previous question: {frm!r}")
                if not to or to not in q_new:
                    self.E(w, f"{k}: 'to' must be a span of the new question: {to!r}")
            if not norm_ws(e.get("note") or ""):
                self.W(w, f"edit {k} has no note")
        if (q_old != q_new) != bool(q_kinds):
            self.E(w, "the question changed without a question edit" if q_old != q_new
                   else "a question edit is recorded but the question is unchanged")
        a_changed = not stage2.same_answer(r["answer"], prev.answer)
        if a_changed != bool(a_kinds):
            self.E(w, "the answer changed without an answer edit" if a_changed
                   else "an answer edit is recorded but the answer is unchanged")
        if a_changed:
            if may_change is True:
                if "recompute_answer" in a_kinds and "recompute_answer" not in permitted:
                    self.E(w, "recompute_answer is not permitted for this set")
            elif may_change == "typo_only":
                if a_kinds != ["correct_answer_typo"] or not stage2.is_typo_fix(prev.answer, r["answer"]):
                    self.E(w, "this family preserves the answer except for a typo correction (correct_answer_typo, a small "
                              f"spelling change): {stage2.answer_display(prev.answer)!r} -> {stage2.answer_display(r['answer'])!r}")
            else:
                self.E(w, f"family {self.cfg['family']} preserves the answer; the attempt changes it")
            if "recompute_answer" in a_kinds and not norm_ws(r.get("completeness_note") or ""):
                self.E(w, "a recomputed answer needs completeness_note: the closing document or fixed domain, the "
                          "three-way search, and every candidate considered (the re-audit must then pass completeness)")
        if "change_attribute_if_trivial" in q_kinds:
            if r.get("trivial_as_written") is not True:
                self.E(w, "change_attribute_if_trivial needs trivial_as_written true (the wording fixes the answer)")
            if "recompute_answer" not in a_kinds:
                self.W(w, "the requested attribute changed but the answer was not recomputed")
        return q_kinds.count("add_qualifier")

    # ------------------------------------------------------------------ C5
    def adversarial_files(self):
        d = self.run.adversarial_file(self.sid, self.qid, "hunt").parent
        out = {}
        for p in sorted(d.glob(f"{self.qid}.*.json")) if d.exists() else []:
            m = ADV_RE.match(p.name[len(self.qid) + 1:-5])
            if m:
                out[(m.group(1), int(m.group(2) or 0))] = p
        return out

    def check_adversarial(self, kinds):
        _, versions = self.versions()
        files = self.adversarial_files()
        for (kind, n), p in files.items():
            if n not in versions:
                self.E(p.name, f"revision {n} does not exist")
        for v in versions.values():
            hunts = {k: stage2.load_json(p) for (k, n), p in files.items() if n == v.n and k != "verify"}
            if "hunt" in kinds:
                for k, h in hunts.items():
                    self.check_hunt(f"{k}.r{v.n}", k, h, v)
            ver = files.get(("verify", v.n))
            if "verify" in kinds and ver:
                self.check_verify(f"verify.r{v.n}", stage2.load_json(ver), hunts, v)

    def check_hunt(self, w, kind, h, v):
        if not self.keys(w, h, ("revision", "kind", "route", "question", "answer", "findings", "n_searches", "note")):
            return
        if stage2.to_int(h.get("revision")) != v.n:
            self.E(w, f"revision is {h.get('revision')!r}, the file name says {v.n}")
        if h.get("kind") != kind or h.get("route") != stage2.HUNT_KINDS[kind]:
            self.E(w, f"kind/route must be {kind!r}/{stage2.HUNT_KINDS[kind]!r}")
        self.version_matches(w, h, v)
        ids = set()
        for f in h.get("findings") or []:
            if not isinstance(f, dict) or not f.get("id") or f["id"] in ids:
                self.E(w, f"every finding needs a unique id (F1, F2, ...): {str(f)[:60]}")
                continue
            fw = f"{w} {f['id']}"
            ids.add(f["id"])
            if f.get("kind") not in stage2.FINDING_KINDS:
                self.E(fw, f"kind must be one of {stage2.FINDING_KINDS}")
            if not norm_ws(f.get("claim") or ""):
                self.E(fw, "a finding needs a claim: how it breaks the question")
            if f.get("confidence") not in stage2.CONFIDENCE:
                self.E(fw, f"confidence must be one of {stage2.CONFIDENCE}")
            for b in ("in_corpus", "changes_answer"):
                if not isinstance(f.get(b), bool):
                    self.E(fw, f"{b} must be true or false")
            ev = f.get("evidence") or []
            if f.get("in_corpus") and not ev:
                self.E(fw, "in_corpus is true but the finding cites no evidence")
            for e in ev:
                self.quotes(fw, e.get("doc_id"), e.get("quotes"))
        if not isinstance(h.get("n_searches"), int):
            self.E(w, "n_searches must be an integer")

    def check_verify(self, w, ver, hunts, v):
        if not self.keys(w, ver, ("revision", "rulings", "refuted", "blocking", "note")):
            return
        if stage2.to_int(ver.get("revision")) != v.n:
            self.E(w, f"revision is {ver.get('revision')!r}, the file name says {v.n}")
        cfg_two = stage2.needs_two_hunters(self.cfg, self.rec, v)
        need = {"hunt_list", "hunt_named"} if cfg_two else {"hunt"}
        if not need <= set(hunts):
            self.E(w, f"verifier rulings without every hunter's file: need {sorted(need)}, have {sorted(hunts)}")
        findings = {f"{k}:{f.get('id')}" for k, h in hunts.items() if isinstance(h, dict) for f in h.get("findings") or []}
        ruled = [r.get("finding") for r in ver.get("rulings") or [] if isinstance(r, dict)]
        dup = sorted({x for x in ruled if ruled.count(x) > 1})
        if dup:
            self.E(w, f"findings ruled twice: {dup}")
        if set(ruled) != findings:
            self.E(w, f"rulings must cover every finding exactly once; missing {sorted(findings - set(ruled))}, "
                      f"unknown {sorted(set(ruled) - findings)}")
        refutes = False
        for r in ver.get("rulings") or []:
            rw = f"{w} {r.get('finding')}"
            if r.get("ruling") not in stage2.RULINGS:
                self.E(rw, f"ruling must be one of {stage2.RULINGS}")
            if not norm_ws(r.get("reason") or ""):
                self.E(rw, "a ruling needs a reason citing what was read")
            refutes |= r.get("ruling") == "refutes"
            rx = r.get("record_exclusion")
            if rx:
                self.quotes(rw, rx.get("doc_id"), [rx.get("quote")])
        if bool(ver.get("refuted")) != refutes:
            self.E(w, "refuted must be true exactly when some ruling refutes")
        if refutes and not ver.get("blocking"):
            self.E(w, "a refutation lists its blocking problems")
        if not refutes and ver.get("blocking"):
            self.E(w, "blocking problems listed although nothing refutes")

    # ------------------------------------------------------------------ C6
    def check_final_checks(self):
        _, versions = self.versions()
        d = self.run.final_check_file(self.sid, self.qid).parent
        for p in sorted(d.glob(f"{self.qid}*.json")) if d.exists() else []:
            m = FC_RE.match(p.name[len(self.qid):-5])
            if not m:
                continue
            n = int(m.group(1) or 0)
            w = f"final_check.r{n}"
            if n not in versions:
                self.E(w, f"revision {n} does not exist")
                continue
            if not self.cfg.get("final_check"):
                self.W(w, "the set's config has final_check false: this file is ignored")
            self.check_final(w, stage2.load_json(p), versions[n])

    def check_final(self, w, fc, v):
        need = ("revision", "status", "question", "answer", "answer_matches_source", "answer_set", "ordered", "order_by",
                "hops", "exclusions", "closure", "gaps", "repairable", "blocking", "note")
        if not self.keys(w, fc, need):
            return
        if stage2.to_int(fc.get("revision")) != v.n:
            self.E(w, f"revision is {fc.get('revision')!r}, the file name says {v.n}")
        self.version_matches(w, fc, v)
        if fc.get("status") not in stage2.FC_STATUS:
            self.E(w, f"status must be one of {stage2.FC_STATUS}")
        if fc.get("answer_matches_source") is v.answer_changed:
            self.E(w, f"answer_matches_source must be {not v.answer_changed}")
        aset = fc.get("answer_set")
        if not isinstance(aset, list) or not aset or not all(isinstance(m, dict) and m.get("member") for m in aset):
            self.E(w, "answer_set lists every member (one for a single answer) as {member, aliases, value}")
        for m in aset or []:
            if not isinstance(m.get("aliases", []), list):
                self.E(w, f"answer_set {m.get('member')!r}: aliases must be a list")
        if not isinstance(fc.get("ordered"), bool):
            self.E(w, "ordered must be true or false")
        elif fc["ordered"] and not norm_ws(fc.get("order_by") or ""):
            self.E(w, "an ordered answer names its order_by")
        bad_hops = [h.get("id") for h in fc.get("hops") or [] if not h.get("grounded")]
        bad_ex = [x.get("candidate") for x in fc.get("exclusions") or [] if not x.get("grounded")]
        gaps = fc.get("gaps") or []
        for g in gaps:
            if g.get("kind") not in stage2.GAP_KINDS or not norm_ws(g.get("what") or ""):
                self.E(w, f"gaps entries are {{kind: {'|'.join(stage2.GAP_KINDS)}, what, doc_id, quote}}: {g!r}")
            if g.get("doc_id") and g.get("quote"):
                self.quotes(w, g["doc_id"], [g["quote"]])
        if fc.get("status") == "passed":
            if gaps or fc.get("blocking") or bad_hops or bad_ex:
                self.E(w, f"status passed with gaps, blocking problems, ungrounded hops {bad_hops} or exclusions {bad_ex}")
            if answer_type(self.rec, self.cfg["answer_type"]) in ("set", "aggregate") and \
                    not (fc.get("closure") or {}).get("grounded"):
                self.E(w, "a set or aggregate passes only with a grounded closure")
        elif fc.get("status") == "failed":
            if not gaps and not fc.get("blocking"):
                self.E(w, "a failed check lists its gaps or blocking problems")
            if not isinstance(fc.get("repairable"), bool):
                self.E(w, "repairable must be true or false")

    # ------------------------------------------------------------------ the second reader
    def check_audit(self):
        a = stage2.load_json(self.run.audit_file(self.sid, self.qid))
        if not a or "_error" in a:
            return
        vb = a.get("verification")
        if vb is None:
            return
        if not isinstance(vb, dict) or vb.get("result") not in ("confirmed", "revised") or \
                not isinstance(vb.get("changes"), list):
            self.E("audit", "verification must be {result: confirmed|revised, changes: [...], note}")
        elif vb["result"] == "revised" and not vb["changes"]:
            self.E("audit", "verification revised lists its changes, one line each")
        elif vb["result"] == "confirmed" and vb["changes"]:
            self.W("audit", "verification confirmed but lists changes")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("set", nargs="?")
    ap.add_argument("qids", nargs="*")
    ap.add_argument("--only", default=",".join(KINDS))
    a = ap.parse_args()
    kinds = set(a.only.split(","))
    if kinds - set(KINDS):
        sys.exit(f"--only takes {','.join(KINDS)}")
    run = Run()
    corpus = Corpus(run)
    n = nerr = 0
    for sid in [a.set] if a.set else list(run.config.sets):
        for rec in run.records(sid):
            if a.qids and rec["record_id"] not in a.qids:
                continue
            c = Checker(run, corpus, sid, rec)
            if "revision" in kinds:
                c.check_revisions()
            if kinds & {"hunt", "verify"}:
                c.check_adversarial(kinds)
            if "final_check" in kinds:
                c.check_final_checks()
            if "audit" in kinds:
                c.check_audit()
            n += 1
            for line in c.lines:
                print(line)
                nerr += line.startswith("ERROR")
    print(f"checked {n} question(s); {nerr} error(s)")
    sys.exit(1 if nerr else 0)


if __name__ == "__main__":
    main()
