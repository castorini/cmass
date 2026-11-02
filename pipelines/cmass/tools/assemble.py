#!/usr/bin/env python3
"""Merge candidate records, their latest audits, revision state and adversarial findings into the review
site's record format (review_site/README.md), and write the site's build config.

  assemble.py [SET ...] [--reviews PATH] [--site-dir DIR]

Writes, in the run directory:

  audited/<set>.jsonl        every question of the set as currently written: the source version, or an
                             evidence-only repair of it (record_id <qid>.r<N>). A question whose wording or
                             answer was revised stays here "as written", marked fails_as_written, and points at
  audited/rw_<set>.jsonl     the revised version (record_id <qid>.r<N>), reviewed with the source wording beside it
  review/docs.jsonl          every document the records cite, quote or reject ({"doc_id", "text"})
  review/site_config.json    the build config for review_site/build_site_data.py: one tab per set (group
                             released or near_miss, as configured) plus its rw_<set> tab (group rewrite)
  review/index.html          a copy of the review page, so the site can be served from review/

A revised version has its own record id, hence its own review key "<tab>~<qid>.r<N>": an approval of an
earlier version never carries over (C5). The audit card shows the verdict, the second reader and the
revision (as the site's salvage block); a note under the question gives the question's Stage 2 state, its
candidate decisions and completeness, and the hunters' findings with the verifier's rulings. Then:

  python3 review_site/build_site_data.py <run>/review/site_config.json --out <run>/review/data
  python3 review_site/serve.py --site <run>/review          # decisions -> <run>/review/reviews/reviews.json
"""
import argparse
import json
import shutil
import sys
import time
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage2_state  # noqa: E402
from cmass import stage2  # noqa: E402
from cmass.corpus import Corpus  # noqa: E402
from cmass.records import hop_doc_ids, hops as rec_hops  # noqa: E402
from cmass.run import Run, write_json, write_jsonl  # noqa: E402

REPO_SITE = Path(__file__).resolve().parents[3] / "review_site"
CAVEATS = {"add_qualifier": "scope_narrowed", "remove_clue": "constraint_removed",
           "remove_source_clause": "constraint_removed", "change_attribute_if_trivial": "attribute_changed",
           "recompute_answer": "answer_recomputed", "correct_answer_typo": "answer_typo_fixed"}


def short(s, n=170) -> str:
    s = " ".join(str(s or "").split()).rstrip(".")
    return s if len(s) <= n else s[:n - 1] + "…"


def hop_ref(r) -> str:
    return f"H{r}" if isinstance(r, int) else str(r)


def salvage_block(st, v: stage2.Version, revs) -> dict | None:
    failed = v.failed
    if v.n == 0 and not failed:
        return None
    repairs, caveats, rationale = [], [], []
    for r in revs:
        if "_error" in r or r["_n"] > max(v.n, (failed or {}).get("_n", 0)):
            continue
        t = (r.get("trigger") or {}).get("stage", "?")
        head = f"r{r['_n']} ({r.get('outcome')}, after the {t} finding)"
        for e in r.get("edits") or []:
            repairs.append(f"{head}: {e.get('kind')} {e.get('from', '')!r} -> {e.get('to', '')!r}")
            caveats.append(CAVEATS.get(e.get("kind"), ""))
        for h in r.get("hop_changes") or []:
            repairs.append(f"{head}: hop {h.get('hop')} {h.get('change')}: {short(h.get('note'), 120)}")
        if r.get("outcome") == "evidence_only" and not r.get("hop_changes"):
            repairs.append(f"{head}: evidence only")
        if r.get("edit_rationale"):
            rationale.append(f"r{r['_n']}: {r['edit_rationale']}")
    edits = v.accepted_edits()
    if len(edits) >= 3:
        caveats.append("heavy_rewrite")
    if failed:
        sv = {"outcome": "not_salvageable", "blocker": failed.get("blocker", ""), "repairs": repairs,
              "note": f"repair attempt {failed['_n']} was unsuccessful; the question is excluded"}
    else:
        outcome = ("salvaged_with_rewrite" if v.question_changed else
                   "salvaged_with_gold_change" if v.answer_changed else "salvaged")
        sv = {"outcome": outcome, "repairs": repairs, "note": f"revision {v.n}; review this version"}
        if v.question_changed:
            sv["proposed_question"] = v.question
        if v.answer_changed:
            sv["proposed_answer"] = stage2.answer_display(v.answer)
    if rationale:
        sv["rewrite_rationale"] = " ".join(rationale)
    sv["caveats"] = sorted({c for c in caveats if c})
    return sv


def adversarial_block(run, sid, qid, st) -> tuple[dict, str]:
    n = st["revision"]
    hunts = {h["kind"]: stage2.load_json(Path(h["path"])) for h in st["hunts"]}
    ver = stage2.load_json(Path(st["verify_file"]))
    block = {"revision": n, "hunts": {k: (h or {}).get("findings") for k, h in hunts.items() if h},
             "verify": ver if ver and "_error" not in ver else None}
    findings = [(k, f) for k, h in hunts.items() if h and "_error" not in h for f in h.get("findings") or []]
    if not any(hunts.values()):
        return block, f"Adversarial check (revision {n}): not run yet."
    rul = {r.get("finding"): r for r in ((ver or {}).get("rulings") or [])}
    parts = []
    for k, f in findings:
        r = rul.get(f"{k}:{f.get('id')}") or {}
        parts.append(f"{f.get('id')} {f.get('kind')} {f.get('candidate', '')!r}: {short(f.get('claim'), 110)} → "
                     f"{r.get('ruling', 'not ruled')}{': ' + short(r.get('reason'), 110) if r.get('reason') else ''}")
    earlier = [k for k in range(n) if (stage2.load_json(stage2.adv_path(run, sid, qid, "verify", k)) or {}).get("refuted")]
    head = (f"Adversarial check (revision {n}): {len(findings)} finding(s); verifier: "
            + ("not run yet" if not ver else "refuted" if ver.get("refuted") else "no refutation")
            + (f"; verified refutation(s) of revision(s) {earlier} led to this revision" if earlier else ""))
    return block, head + (". " + " · ".join(parts) if parts else ".")


def digest(st, a) -> str:
    out = [f"Stage 2: next step {st['next']}" + (f" ({st['stage']}: {st['reason']})" if st["next"] == "excluded" else "")
           + f": {short(st['detail'], 140)}."]
    cands = (a or {}).get("candidates") or []
    if cands:
        out.append("Candidates: " + "; ".join(
            f"{c.get('name')} ({c.get('role')}) {c.get('status')} [{', '.join(hop_ref(h) for h in c.get('hops') or [])}]"
            + (f": {short(c.get('note'), 80)}" if c.get("note") and c.get("status") != "qualifies" else "")
            for c in cands) + ".")
    comp = (a or {}).get("completeness") or {}
    if comp:
        out.append(f"Completeness: {comp.get('basis')}"
                   + (f" ({', '.join(comp.get('doc_ids') or [])})" if comp.get("doc_ids") else "")
                   + (f": {short(comp.get('note'), 140)}" if comp.get("note") else "") + ".")
    con = (a or {}).get("contamination") or {}
    if con.get("leak") or con.get("single_document"):
        out.append(f"Contamination: {short(con.get('note'), 140)}")
    return " ".join(out)


def merged(run, corpus, sid, rec, st, a, v, revs) -> dict:
    out = deepcopy(rec)
    qid = rec["record_id"]
    out["record_id"] = st["site_record_id"]
    out["question"] = v.source_question
    for k in ("answers", "gold_answer", "problem"):
        out.pop(k, None)
    out["answer"] = stage2.answer_display(v.source_answer)
    out["answer_type"] = st["answer_type"]
    out["cmass"] = {k: st[k] for k in ("set", "qid", "revision", "next", "detail", "stage", "reason", "review_key",
                                       "final_check_status")}
    invalid = sorted({d for h in rec_hops(rec) for d in hop_doc_ids(h) if corpus.get_document(d) is None})
    fresh = a is not None and "_error" not in a and stage2.to_int(a.get("revision", 0)) == v.n
    hops = []
    by_idx = {h.get("index"): h for h in (a or {}).get("hops", [])} if fresh else {}
    n_ground = n_new = 0
    for i, h in enumerate(rec_hops(rec), 1):
        h = deepcopy(h)
        h["index"] = i
        ah = deepcopy(by_idx.get(i)) if i in by_idx else None
        if ah is not None:
            cited = set(hop_doc_ids(h))
            ah["added_doc_ids"] = [e.get("doc_id") for e in ah.get("evidence", []) if e.get("doc_id") not in cited]
            ah["invalid_cited_doc_ids"] = [d for d in hop_doc_ids(h) if d in invalid]
            if ah.get("status") in ("grounded", "derivation_ok", "dropped"):
                n_ground += 1
                if ah.get("status") == "grounded" and not any(
                        e.get("source") == "original" and e.get("supports") == "full" for e in ah.get("evidence", [])):
                    n_new += 1
            h["audit"] = ah
        hops.append(h)
    out["hops"] = hops
    adv, adv_text = adversarial_block(run, sid, qid, st)
    if not fresh:
        out["audit"] = {"verdict": "not_audited", "revision": v.n, "invalid_doc_ids": invalid,
                        "summary": f"Not audited for revision {v.n} yet ({st['detail']})."}
    else:
        cons = a.get("constraints", [])
        mtime = Path(st["audit_file"]).stat().st_mtime
        out["audit"] = {
            "auditor": a.get("auditor", "auditor agent (stage2/AUDIT.md)"),
            "audited_at": time.strftime("%Y-%m-%d", time.localtime(mtime)),
            "revision": v.n, "verdict": a.get("verdict"), "fail_reasons": a.get("fail_reasons", []),
            "summary": a.get("summary", ""), "constraints": cons, "added_hops": a.get("added_hops", []),
            "answer_check": a.get("answer_check", {}), "verification": a.get("verification"),
            "candidates": a.get("candidates", []), "completeness": a.get("completeness"),
            "contamination": a.get("contamination"), "adversarial": adv, "invalid_doc_ids": invalid,
            "searches": [{"query": s.get("query"), "hits": s.get("hits"), "results": s.get("results", [])}
                         for s in stage2.searchlog(run, sid, qid)],
            "stats": {"n_constraints": len(cons), "n_constraints_covered": sum(c.get("status") == "covered" for c in cons),
                      "n_hops": len(hops), "n_hops_grounded": n_ground, "n_hops_grounded_only_via_new_docs": n_new,
                      "n_added_hops": len(a.get("added_hops", []))},
        }
    sv = salvage_block(st, v, revs)
    if sv:
        out["audit"]["salvage"] = sv
    note = digest(st, a if fresh else None) + " " + adv_text
    if rec.get("annotator_note"):
        note += f" Source note: {rec['annotator_note']}"
    out["annotator_note"] = note
    return out


def as_written(rec, st, v, rw_ds) -> dict:
    """The home-table copy of a question whose revised version is reviewed in the rewrites table."""
    out = deepcopy(rec)
    for k in ("answers", "gold_answer", "problem"):
        out.pop(k, None)
    out["question"], out["answer"] = v.source_question, stage2.answer_display(v.source_answer)
    out["hops"] = [{**deepcopy(h), "index": i} for i, h in enumerate(rec_hops(rec), 1)]
    for h in out["hops"]:
        h.pop("audit", None)
    what = " and ".join(x for x in ("reworded question" if v.question_changed else "",
                                    "changed answer" if v.answer_changed else "") if x)
    out["audit"] = {"verdict": "fail", "fail_reasons": ["fails_as_written"], "revision": 0, "fixed_in": rw_ds,
                    "summary": f"As written, this question did not pass Stage 2. Revision {v.n} ({what}) is reviewed in "
                               f"{rw_ds} as {st['site_record_id']}; decide there.",
                    **({"fixed_question": v.question} if v.question_changed else {}),
                    **({"fixed_answer": stage2.answer_display(v.answer)} if v.answer_changed else {})}
    out["cmass"] = {"set": st["set"], "qid": st["qid"], "revision": 0, "next": "see_revision",
                    "review_key": f"{st['set']}~{st['qid']}"}
    return out


def doc_ids(row) -> set:
    ids = set()
    for h in row.get("hops", []):
        ids.update(hop_doc_ids(h))
        ah = h.get("audit") or {}
        ids.update(e.get("doc_id") for e in ah.get("evidence", []))
        ids.update(r.get("doc_id") for r in ah.get("rejected_docs", []))
    for h in (row.get("audit") or {}).get("added_hops", []):
        ids.update(e.get("doc_id") for e in h.get("evidence", []))
        ids.update(r.get("doc_id") for r in h.get("rejected_docs", []))
    return {d for d in ids if d}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sets", nargs="*")
    ap.add_argument("--reviews", help="review store (default: <run>/review/reviews/reviews.json or $CMASS_REVIEWS)")
    ap.add_argument("--site-dir", default=str(REPO_SITE), help="the review site whose index.html is copied")
    a = ap.parse_args()
    run = Run()
    corpus = Corpus(run)
    reviews = stage2.load_reviews(run, a.reviews)
    check = stage2_state.audit_checker(run, corpus)
    datasets, all_docs = [], set()
    for sid in a.sets or list(run.config.sets):
        cfg = run.config.set(sid)
        home, rw, rw_ds = [], [], f"rw_{sid}"
        for rec in run.records(sid):
            st = stage2.question_state(run, sid, rec, reviews, check)
            if st["stage"] == "validation":
                print(f"  {sid}/{rec['record_id']}: not shown (C1: {st['detail']})")
                continue
            revs = stage2.load_revisions(run, sid, rec["record_id"])
            v = stage2.Version(rec, [r for r in revs if "_error" not in r])
            row = merged(run, corpus, sid, rec, st, stage2.load_json(run.audit_file(sid, rec["record_id"])), v, revs)
            if v.changed:
                home.append(as_written(rec, st, v, rw_ds))
                rw.append(row)
            else:
                home.append(row)
        for name, rows in ((sid, home), (rw_ds, rw)):
            p = run.audited_file(name)
            if not rows:
                p.unlink(missing_ok=True)
                continue
            write_jsonl(p, rows)
            for r in rows:
                all_docs |= doc_ids(r)
        title = cfg.get("title") or sid
        extra = {"canary": cfg["canary"]} if cfg.get("canary") else {}
        datasets.append({"id": sid, "name": title, "title": title, "group": cfg["group"],
                         "records": f"../audited/{sid}.jsonl", **extra})
        if rw:
            datasets.append({"id": rw_ds, "name": title, "title": f"{title}: revised versions", "group": "rewrite",
                             "records": f"../audited/{rw_ds}.jsonl", **extra})
        print(f"{sid}: {len(home)} records" + (f"; {rw_ds}: {len(rw)} revised versions" if rw else ""))
    rd = run.review_dir()
    write_jsonl(rd / "docs.jsonl", ({"doc_id": d, "text": t} for d in sorted(all_docs)
                                    if (t := corpus.get_document(d)) is not None))
    write_json(rd / "site_config.json", {"corpus": run.config.corpus.get("name", ""), "docs": "docs.jsonl",
                                         "datasets": datasets})
    index = Path(a.site_dir) / "index.html"
    if index.exists():
        shutil.copy2(index, rd / "index.html")
    site = Path(a.site_dir)
    print(f"wrote {rd / 'site_config.json'} ({len(datasets)} tabs, {len(all_docs)} documents). Next:\n"
          f"  python3 {site / 'build_site_data.py'} {rd / 'site_config.json'} --out {rd / 'data'}\n"
          f"  python3 {site / 'serve.py'} --site {rd}")


if __name__ == "__main__":
    main()
