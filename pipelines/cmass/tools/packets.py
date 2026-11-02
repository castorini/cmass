#!/usr/bin/env python3
"""Build the auditor's packet per question: packets/<set>/<qid>.md (C1 inputs for C2-C3).

  packets.py                 # every question of every configured set
  packets.py SET [QID ...]   # one set, or some of its questions

A packet holds the current version's question and answer (with the source wording beside a revised
version), the set's dataset rules, the C1 check of the candidate record, the revision history, and every
hop with its clue, snippet and cited documents. For each cited document it reports whether each snippet
fragment occurs in it (exact / ws = whitespace differs / ci = case differs / MISSING) and shows a context
window around the match; a hop without a snippet shows the window with the most clue terms, as a hint.
"MISSING" does not mean unsupported: the auditor still reads the document.

When the current version is newer than the audit on disk (a repaired question about to be re-audited),
the old audit is first archived as work/<set>/<qid>.audit.r<N>.json, so no audit is ever lost.
Run inside a run directory (or set $CMASS_RUN).
"""
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cmass import stage2  # noqa: E402
from cmass.corpus import Corpus  # noqa: E402
from cmass.records import hop_doc_ids, hops as rec_hops  # noqa: E402
from cmass.run import Run  # noqa: E402
from cmass.text import locate, norm_ws, split_spans  # noqa: E402

WINDOW = 450
CORE = {"record_id", "id", "question", "problem", "answer", "answers", "gold_answer", "answer_type", "hops", "hints"}
STOP = set("the a an of and or to in on at for by with from as is was were be been are that this which who whom "
           "whose its it their his her they he she than then also into about after before between during under "
           "over not no has have had one two".split())


def flat(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def clue_window(text: str, clue: str, size: int = 700) -> tuple[int, int, int, int]:
    terms = {w for w in re.findall(r"[a-z0-9]+", clue.lower()) if len(w) > 2 and w not in STOP}
    low, best = text.lower(), (0, 0)
    for s in range(0, max(1, len(text) - size + 150), 150):
        sc = sum(t in low[s:s + size] for t in terms)
        if sc > best[0]:
            best = (sc, s)
    return best[1], min(len(text), best[1] + size), best[0], len(terms)


def doc_lines(corpus: Corpus, docid: str, frags: list[str], clue: str, primary: bool) -> list[str]:
    text = corpus.get_document(docid)
    if text is None:
        return [f"  - `{docid}`: NOT FOUND in the corpus (an invalid id: the audit lists it in rejected_docs)"]
    if not frags:
        s, e, n, of = clue_window(text, clue)
        out = [f"  - `{docid}` ({len(text)} chars), no snippet: best clue window has {n}/{of} clue terms (a hint only)"]
        if primary or len(text) < 1500:
            out.append(f"    > {'…' if s else ''}{flat(text[s:e])}{'…' if e < len(text) else ''}")
        return out
    hits = [locate(text, f) for f in frags]
    found = [h for h in hits if h]
    modes = ",".join(h[2] if h else "MISSING" for h in hits)
    out = [f"  - `{docid}` ({len(text)} chars) snippet fragments: {len(found)}/{len(frags)} found [{modes}]"]
    if found and (primary or len(found) < len(frags)):
        s, e = min(h[0] for h in found), max(h[1] for h in found)
        s0, e0 = max(0, s - WINDOW), min(len(text), e + WINDOW)
        e0 = min(e0, s0 + 2600)
        out.append(f"    > {'…' if s0 else ''}{flat(text[s0:e0])}{'…' if e0 < len(text) else ''}")
    elif not found and (primary or len(text) < 1500):
        out.append(f"    > {flat(text[:900])}{'…' if len(text) > 900 else ''}")
    return out


def archive_stale_audit(run, sid: str, qid: str, version: int) -> str:
    p = run.audit_file(sid, qid)
    a = stage2.load_json(p)
    if not a or "_error" in a or stage2.to_int(a.get("revision", 0)) >= version:
        return ""
    dst = stage2.archived_audit_path(run, sid, qid, stage2.to_int(a.get("revision", 0)))
    if not dst.exists():
        shutil.copy2(p, dst)
    return str(dst.relative_to(run.root))


def build(run, corpus, sid: str, rec: dict) -> str:
    cfg = run.config.set(sid)
    qid = rec["record_id"]
    revs = stage2.load_revisions(run, sid, qid)
    v = stage2.Version(rec, [r for r in revs if "_error" not in r])
    archived = archive_stale_audit(run, sid, qid, v.n)
    errs, warns = stage2.candidate_problems(rec, cfg["answer_type"])
    out = [f"# {sid} / {qid}" + (f" (revision {v.n})" if v.n else ""), ""]
    out += [f"**Question:** {v.question}", "", f"**Answer:** {stage2.answer_display(v.answer)}", ""]
    if v.question_changed:
        out += [f"**Source question:** {v.source_question}", ""]
    if v.answer_changed:
        out += [f"**Source answer:** {stage2.answer_display(v.source_answer)}", ""]
    out += [f"- **answer_type:** {rec.get('answer_type') or cfg['answer_type']}",
            f"- **set rules:** family `{cfg['family']}`, group `{cfg['group']}`; permitted revisions "
            f"{', '.join(run.config.permitted_revisions(sid))}; answer may change: {cfg['answer_may_change']}; "
            f"final check: {'yes' if cfg.get('final_check') else 'no'}",
            f"- **notes:** {cfg.get('notes', '')}"]
    for k, val in rec.items():
        if k not in CORE and val not in (None, "", [], {}):
            out.append(f"- **{k}:** {val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)}")
    out += ["", "## Input check (C1)", ""]
    out += [f"- ERROR: {e}" for e in errs] + [f"- warning: {w}" for w in warns] or ["- the candidate record is well formed"]
    if revs:
        out += ["", "## Revision history (C4)", ""]
        for r in revs:
            if "_error" in r:
                out.append(f"- {r['_error']}")
                continue
            t = r.get("trigger") or {}
            out.append(f"- **r{r['_n']}** ({r.get('outcome')}), after the {t.get('stage')} finding "
                       f"({t.get('ref', '')}): {'; '.join(t.get('reasons') or [])}")
            for e in r.get("edits") or []:
                out.append(f"  - edit `{e.get('kind')}`: {e.get('from', '')!r} -> {e.get('to', '')!r}. {e.get('note', '')}")
            if r.get("edit_rationale"):
                out.append(f"  - rationale: {r['edit_rationale']}")
            for h in r.get("hop_changes") or []:
                out.append(f"  - hop {h.get('hop')}: {h.get('change')}. {h.get('note', '')}")
        prior = sorted(run.work_dir(sid).glob(f"{qid}.audit.r*.json"))
        if prior:
            out.append(f"- earlier audits (reuse what holds): {', '.join(str(p.relative_to(run.root)) for p in prior)}")
        if archived:
            out.append(f"- the audit of the previous version was archived just now as {archived}")
    reopen = stage2.load_json(stage2.reopen_path(run, sid, qid))
    if reopen and "_error" not in reopen:
        out += ["", f"**Reopened** ({reopen.get('at', '')}): {reopen.get('note', '')}"]
    hs = rec_hops(rec)
    out += ["", f"## Hops ({len(hs)})", ""]
    if not hs:
        out += ["The record has no hops: write every hop of the audit as an added hop (A1, A2, ...).", ""]
    for i, h in enumerate(hs, 1):
        tags = " ".join(f"{k}={h[k]}" for k in ("kind", "redundant", "is_bridge") if k in h)
        out += [f"### Hop {i} {tags}".rstrip(), f"- **clue:** {h.get('clue', '')}"]
        docs = hop_doc_ids(h)
        frags = split_spans(h.get("snippet") or "")
        if h.get("snippet"):
            out.append(f"- **snippet:** {h['snippet']}")
        out.append(f"- **cited docs ({len(docs)}):** primary first")
        for j, d in enumerate(docs):
            out += doc_lines(corpus, d, frags, h.get("clue", ""), primary=(j == 0))
        out.append("")
    if rec.get("hints"):
        out += ["## Hints (source material: leads for searching, never evidence)", ""]
        hints = rec["hints"] if isinstance(rec["hints"], list) else [rec["hints"]]
        out += [f"- {norm_ws(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False))[:1500]}" for x in hints]
        out.append("")
    log = stage2.searchlog(run, sid, qid)
    out += ["## Searches so far", "",
            f"{len(log)} queries logged for this question (`cm.py history --set {sid} --qid {qid}` lists them)."]
    return "\n".join(out) + "\n"


def main():
    argv = sys.argv[1:]
    run = Run()
    corpus = Corpus(run)
    sets = [argv[0]] if argv else list(run.config.sets)
    only = set(argv[1:])
    n = 0
    for sid in sets:
        for rec in run.records(sid):
            if only and rec["record_id"] not in only:
                continue
            p = run.packet(sid, rec["record_id"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(build(run, corpus, sid, rec))
            n += 1
    print(f"wrote {n} packet(s) under {run.root / 'packets'}")


if __name__ == "__main__":
    main()
