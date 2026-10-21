#!/usr/bin/env python3
"""Gate 0 (a measurement, not a filter): does every evidence snippet of the strict-audited list occur verbatim
in one of its cited documents?

  python3 verify_snippets.py [--run RUN] [--set trqa]

Deterministic, no agents. Reads stage1/<set>/strict_list.jsonl and every cited document through the run's
corpus (cached under cache/docs). A snippet is split on " ... " and every fragment must occur in one
document (whitespace normalized); the reasons are "no snippet", "unfetchable doc" and "snippet not in any
cited doc". Writes stage1/<set>/snippet_verification.json ({qid: [bad hops]}) and snippet_clean.json (the
questions with no bad hop).

In the real run only 243 of 527 evidence snippets passed (6 of 87 questions fully clean): the strict audit
wrote composite snippets (fragments of several documents, commentary, document ids). Nothing was dropped
for it; instead gate 1 (extreme) rewrites every passing hop with a fresh verbatim quote and build_final.py
re-verifies those. Ported from the private repo's artifacts/trqa/final_pipeline/verify_snippets.py.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from cmass.corpus import Corpus  # noqa: E402
from cmass.run import Run, read_jsonl, write_json  # noqa: E402
from trqa_state import Stage1  # noqa: E402

norm = lambda s: re.sub(r"\s+", " ", s or "").strip()  # noqa: E731


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run")
    p.add_argument("--set", default="trqa")
    a = p.parse_args()
    run = Run(a.run)
    s1, corpus = Stage1(run, a.set), Corpus(run)
    rs = read_jsonl(s1.list_file("strict_list.jsonl"))
    if not rs:
        raise SystemExit("no strict_list.jsonl: run build_strict_list.py first")
    docs = sorted({d for r in rs for h in r["hops"] for d in (h.get("supporting_doc_ids") or [])})
    print(f"fetching {len(docs)} documents ...", flush=True)
    text = {d: norm(corpus.get_document(d) or "") for d in docs}
    missing = [d for d, t in text.items() if not t]
    print(f"fetched {len(docs) - len(missing)}/{len(docs)}; unfetchable: {len(missing)}")

    def hit(snip, ids):
        frags = [f.strip() for f in norm(snip).split(" ... ") if f.strip()]
        if not frags:
            return None
        for d in ids:
            t = text.get(d, "")
            if t and all(f in t for f in frags):
                return d
        return None

    per_q, nbad, nev = {}, 0, 0
    for r in rs:
        bad = []
        for i, h in enumerate(r["hops"], 1):
            if h.get("kind") != "evidence":
                continue
            nev += 1
            ids, snip = h.get("supporting_doc_ids") or [], h.get("snippet") or ""
            if not snip:
                bad.append({"hop": i, "why": "no snippet"})
                continue
            if hit(snip, ids) is None:
                unfetch = [x for x in ids if not text.get(x)]
                bad.append({"hop": i, "why": "unfetchable doc" if unfetch else "snippet not in any cited doc", "docs": ids})
        per_q[r["record_id"]] = bad
        nbad += len(bad)
    clean = [q for q, b in per_q.items() if not b]
    write_json(s1.list_file("snippet_verification.json"), per_q)
    write_json(s1.list_file("snippet_clean.json"), clean)
    print(f"questions: {len(rs)} | fully verbatim: {len(clean)} | with >=1 bad hop: {len(rs) - len(clean)}")
    print(f"evidence hops: {nev}; verbatim: {nev - nbad}; failing: {nbad}")
    print("failure kinds:", dict(Counter(b["why"] for v in per_q.values() for b in v)))


if __name__ == "__main__":
    main()
