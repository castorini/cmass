#!/usr/bin/env python3
"""Corpus CLI for the agents of Stages 2 and 3 (search, read, quote, verify), over the run's configured corpus.

  cm.py search  --set SET --qid QID "query words" [--hits 10] [--chars 220] [--grep PHRASE ...] [--only PHRASE ...]
  cm.py doc     --set SET --qid QID DOCID [--grep PHRASE ...] [--window 350] [--max 6000]
  cm.py quote   DOCID "first words of the span" "last words of the span"   # exact span, printed as a JSON string
  cm.py verify  DOCID "quote"                                             # is the quote verbatim in the document?
  cm.py history --set SET --qid QID                                       # every query already tried for this question

Run inside a run directory (or set $CMASS_RUN). --set/--qid log every search to work/<set>/<qid>.searchlog.jsonl,
which becomes part of the question's record. Documents are cached, so re-reading costs nothing.

search refuses a query too similar to one already tried for the question (token overlap 0.6 or more):
change the words, try a rarer term or a short 2-4 word query, or pass --force only to rerun it deeper.
--only lists just the hits whose full text contains one of the phrases (a deep sweep: use --hits 50).
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cmass.corpus import Corpus  # noqa: E402
from cmass.run import Run, read_jsonl  # noqa: E402
from cmass.text import is_verbatim, locate, norm_ws  # noqa: E402


def _tokens(q):
    return {w for w in re.findall(r"[a-z0-9]+", q.lower()) if len(w) > 1}


def near_duplicate(run, sid, qid, query, threshold=0.6):
    t, best = _tokens(query), (0.0, None)
    for rec in read_jsonl(run.searchlog(sid, qid)):
        u = _tokens(rec["query"])
        if t and u:
            j = len(t & u) / len(t | u)
            if j > best[0]:
                best = (j, rec)
    return best if best[0] >= threshold else None


def cmd_search(run, corpus, a):
    if a.set and a.qid and not a.force:
        dup = near_duplicate(run, a.set, a.qid, a.query)
        if dup:
            print(f"REFUSED: {dup[0]:.2f} token overlap with an earlier query for this question:\n"
                  f"   [{dup[1].get('hits')} hits] {dup[1]['query']}\n"
                  "Change the query, or pass --force only to rerun it deeper (more --hits).")
            sys.exit(3)
    hits = corpus.search(a.query, a.hits, a.set, a.qid)
    if not hits:
        print("(no results)")
    shown = 0
    for h in hits:
        if a.only or a.grep:
            flat = re.sub(r"\s+", " ", corpus.get_document(h["docid"]) or "")
        else:
            flat = h.get("preview") or re.sub(r"\s+", " ", corpus.get_document(h["docid"]) or "")
        score = f"{h['score']:6.2f}" if isinstance(h.get("score"), (int, float)) else "      "
        if a.only:
            low = flat.lower()
            terms = [g for g in a.only if g.lower() in low]
            if not terms:
                continue
            shown += 1
            for g in terms[:2]:
                i = low.find(g.lower())
                s0 = max(0, i - a.chars)
                print(f"#{h['rank']:<2} {h['docid']:<20} {score} [{g}] | {'…' if s0 else ''}{flat[s0:i + len(g) + a.chars]}")
            continue
        preview = flat[:a.chars]
        for g in a.grep or []:
            loc = locate(flat, g)
            if loc:
                s = max(0, loc[0] - a.chars // 2)
                preview = ("…" if s else "") + flat[s:s + a.chars]
                break
        print(f"#{h['rank']:<2} {h['docid']:<20} {score} | {preview}")
    if a.only:
        print(f"({shown} of {len(hits)} hits contain any of {a.only})")


def cmd_doc(run, corpus, a):
    text = corpus.get_document(a.docid)
    if text is None:
        print(f"DOCUMENT NOT FOUND: {a.docid}")
        sys.exit(1)
    print(f"=== {a.docid} ({len(text)} chars)")
    if not a.grep:
        print(text[:a.max] + ("\n[... truncated; use --max or --grep]" if len(text) > a.max else ""))
        return
    for g in a.grep:
        hits = [m.start() for m in re.finditer(re.escape(g), text, re.IGNORECASE)]
        print(f"--- grep {g!r}: {len(hits)} match(es)")
        for i in hits[:4]:
            s, e = max(0, i - a.window), min(len(text), i + len(g) + a.window)
            print(("…" if s else "") + text[s:e] + ("…" if e < len(text) else ""))
            print("-")


def cmd_quote(run, corpus, a):
    text = corpus.get_document(a.docid)
    if text is None:
        print(f"DOCUMENT NOT FOUND: {a.docid}")
        sys.exit(1)
    t = norm_ws(text)
    s = locate(t, norm_ws(a.start))
    if not s:
        print(f"START not found: {a.start!r}")
        sys.exit(2)
    e = locate(t[s[0]:], norm_ws(a.end)) if a.end else (0, len(norm_ws(a.start)), "exact")
    if not e:
        print(f"END not found after START: {a.end!r}")
        sys.exit(2)
    span = t[s[0]:s[0] + e[1]]
    if len(span) > 700:
        print(f"warning: span is {len(span)} chars; prefer a shorter quote", file=sys.stderr)
    print(json.dumps(span, ensure_ascii=False))


def cmd_verify(run, corpus, a):
    text = corpus.get_document(a.docid)
    if text is None:
        print(f"DOCUMENT NOT FOUND: {a.docid}")
        sys.exit(1)
    mode = is_verbatim(text, a.quote)
    print(f"VERBATIM ({mode})" if mode else "NOT FOUND verbatim")
    sys.exit(0 if mode else 2)


def cmd_history(run, corpus, a):
    recs = read_jsonl(run.searchlog(a.set, a.qid))
    for i, r in enumerate(recs, 1):
        print(f"{i:3d}. [{r.get('hits')} hits] {r['query']}")
    print(f"({len(recs)} queries logged for {a.set}/{a.qid})")


def main():
    p = argparse.ArgumentParser(description="Corpus CLI for pipeline agents.")
    p.add_argument("--run", help="run directory (default: $CMASS_RUN or the current directory)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--hits", type=int, default=10)
    s.add_argument("--chars", type=int, default=220)
    s.add_argument("--grep", nargs="*")
    s.add_argument("--only", nargs="*")
    s.add_argument("--force", action="store_true")
    s.add_argument("--set")
    s.add_argument("--qid")
    d = sub.add_parser("doc")
    d.add_argument("docid")
    d.add_argument("--grep", nargs="*")
    d.add_argument("--window", type=int, default=350)
    d.add_argument("--max", type=int, default=6000)
    d.add_argument("--set")
    d.add_argument("--qid")
    q = sub.add_parser("quote")
    q.add_argument("docid")
    q.add_argument("start")
    q.add_argument("end", nargs="?", default="")
    v = sub.add_parser("verify")
    v.add_argument("docid")
    v.add_argument("quote")
    h = sub.add_parser("history")
    h.add_argument("--set", required=True)
    h.add_argument("--qid", required=True)
    a = p.parse_args()
    run = Run(a.run)
    corpus = Corpus(run)
    {"search": cmd_search, "doc": cmd_doc, "quote": cmd_quote, "verify": cmd_verify,
     "history": cmd_history}[a.cmd](run, corpus, a)


if __name__ == "__main__":
    main()
