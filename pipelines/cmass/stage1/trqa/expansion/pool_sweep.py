#!/usr/bin/env python3
"""TRQA expansion, step 2: presence sweep over TRQA's own pools (is every pool page in the corpus at all?).

  python3 pool_sweep.py titles  [--run RUN] [--set trqa]   Wikipedia titles of every pool page -> expansion/titles.json
  python3 pool_sweep.py sweep   [--run RUN] [--set trqa]   one search per page (resumable)    -> expansion/presence.jsonl
  python3 pool_sweep.py summary [--run RUN] [--set trqa]   per-question presence              -> expansion/presence_by_question.json

TRQA's projector had to discover each set itself; TRQA's qrels name the pool each question ranges over, so a
cheaper first test is whether the pool's members exist in the corpus. For every Wikipedia page in the pools
of universe.json (smallest pool first, so a partial sweep covers the likeliest questions), search its title
(parentheses replaced by spaces, disambiguator words kept) and record whether the cleaned title (trailing
"(...)" removed, NFKC, lower case, dashes and quotes unified) occurs in the text of any of the top 50
documents, the rank of the first such document and the number of matching documents. A failed request is
not an absence: the page is left for the next run. Presence is necessary, not sufficient: the Stage 2 agents
still ground membership and the attribute.

`titles` calls the Wikipedia API (pageids -> titles; identification only, never evidence). `sweep` uses the
run's corpus; run it on the "rest" backend, whose search returns document text (one request per page, as
in the real run); this port caches those documents under cache/docs, where the original read them in memory.
In the real run: 678 of the 1,322 universe questions had their whole pool present.

Ported from the private repo's artifacts/projection_audit/tools/trqa_pool_sweep.py.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from cmass.corpus import Corpus, CorpusError  # noqa: E402
from cmass.run import Run, write_json  # noqa: E402

HITS = 50
UA = {"User-Agent": "cmass-stage1/0.1 (research; trqa pool presence sweep)"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[‐-―−]", "-", s).replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", s).strip()


def clean_title(t: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", t).strip()


def wiki_query(params: dict) -> dict:
    req = urllib.request.Request(f"https://en.wikipedia.org/w/api.php?{urllib.parse.urlencode(params)}", headers=UA)
    for attempt in range(6):
        try:
            return json.load(urllib.request.urlopen(req, timeout=60))["query"]["pages"]
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            time.sleep(10 * (attempt + 1))
    return {}


def titles(X: Path):
    uni = json.loads((X / "universe.json").read_text())
    ids = sorted({p for r in uni for p in r["pool"]}, key=int)
    path = X / "titles.json"
    have = json.loads(path.read_text()) if path.exists() else {}
    todo = [p for p in ids if p not in have]
    for i in range(0, len(todo), 50):
        pages = wiki_query({"action": "query", "format": "json", "pageids": "|".join(todo[i:i + 50])})
        for p, v in pages.items():
            have[p] = v.get("title")
        write_json(path, have)
        time.sleep(1.5)
    print(f"{len(have)} titles ({sum(1 for v in have.values() if not v)} missing)")


def sweep(run: Run, X: Path):
    uni = json.loads((X / "universe.json").read_text())
    T = json.loads((X / "titles.json").read_text())
    corpus = Corpus(run)
    order, seen = [], set()
    for r in sorted(uni, key=lambda r: (r["n_pool"], r["rid"])):
        for p in r["pool"]:
            if p not in seen:
                seen.add(p)
                order.append(p)
    out = X / "presence.jsonl"
    done = {json.loads(l)["pageid"] for l in open(out) if l.strip()} if out.exists() else set()
    n = 0
    with open(out, "a") as f:
        for p in order:
            if p in done or not T.get(p):
                continue
            title = T[p]
            query = re.sub(r"[()]", " ", title)
            try:
                res = corpus.search(query, HITS)
            except CorpusError as e:          # an API failure is not an absence: leave it for the next run
                print("no result for", p, e)
                continue
            needle = norm(clean_title(title))
            hits, first = [], None
            for c in res:
                text = corpus.get_document(c["docid"]) or ""
                if needle and needle in norm(text):
                    hits.append(c["docid"])
                    first = first or c["rank"]
            f.write(json.dumps({"pageid": p, "title": title, "query": query, "hits_read": len(res),
                                "present": bool(hits), "first_rank": first, "n_matching": len(hits), "docs": hits[:10]},
                               ensure_ascii=False) + "\n")
            f.flush()
            n += 1
            if n % 200 == 0:
                print(f"{n} swept this run ({len(done) + n}/{len(order)})", flush=True)
    print(f"swept {n} this run; {len(done) + n}/{len(order)} pages done")


def summary(X: Path):
    uni = json.loads((X / "universe.json").read_text())
    pres = {}
    for line in open(X / "presence.jsonl"):
        if line.strip():
            r = json.loads(line)
            pres[r["pageid"]] = r["present"]
    rows = []
    for r in uni:
        known = [pres[p] for p in r["pool"] if p in pres]
        rows.append({k: r[k] for k in ("rid", "pop", "prop", "n_pool", "verdict", "cat", "q", "a")}
                    | {"swept": len(known), "present": sum(known), "all_present": len(known) == r["n_pool"] and all(known)})
    write_json(X / "presence_by_question.json", rows)
    full = [r for r in rows if r["swept"] == r["n_pool"]]
    print(f"{len(full)}/{len(rows)} questions fully swept; all members present: {sum(r['all_present'] for r in full)}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["titles", "sweep", "summary"])
    p.add_argument("--run")
    p.add_argument("--set", default="trqa")
    a = p.parse_args()
    run = Run(a.run)
    X = run.stage1_dir(a.set) / "expansion"
    if a.cmd == "titles":
        titles(X)
    elif a.cmd == "sweep":
        sweep(run, X)
    else:
        summary(X)


if __name__ == "__main__":
    main()
