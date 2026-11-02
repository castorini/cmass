#!/usr/bin/env python3
"""After the four gates: apply the annotator's hop repairs under a guard, re-verify every snippet, and write the
Stage 1 list -> stage1/<set>/final_list.jsonl.

  python3 build_final.py [--run RUN] [--set trqa]

Survivors are the questions every gate kept (trqa_state.py: stage annotator, outcome kept), in id order.
Each hop starts from the extreme gate's re-grounded version (clue, doc_id, doc_ids, fresh snippet). An
annotator repair (verdict "fix") is an agent's claim until checked: it is applied only when its document is
one the extreme gate verified for that hop AND its snippet occurs verbatim in that document (or, fragment by
fragment, in the hop's verified documents); otherwise it is discarded and the hop keeps its extreme-gate
evidence. Then every evidence hop's snippet is checked verbatim against its documents.

Matching: curly quotes, en/em dashes and no-break spaces are normalized, whitespace collapsed; a snippet is
split into fragments at "..", "…" or an inline "[shard_<n>_<n>]" provenance marker, fragments under 8
characters are ignored, and every fragment must occur.

Writes final_list.jsonl ({record_id, subset, question, gold_answer, operator, target_attribute,
entity_set_description, n_hops, hops: [{index, kind, clue, doc_id, supporting_doc_ids, snippet}]}),
fix_verification.json (hop outcome counts) and final_snippet_failures.json. Unverified snippets are listed,
not dropped (the real run had none: 275 of 275). Ported from the private repo's
artifacts/trqa/final_pipeline/build_final.py (documents now come through the run's corpus and cache).
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from cmass.corpus import Corpus  # noqa: E402
from cmass.run import Run, read_jsonl, write_json, write_jsonl  # noqa: E402
from trqa_state import Stage1, state  # noqa: E402


def clean(s: str) -> str:
    s = (s or "").replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


SPLIT = re.compile(r"\s*(?:\.{2,}|…|\[shard_\d+_\d+\])\s*")


def frags(s: str) -> list[str]:
    return [f for f in (x.strip() for x in SPLIT.split(clean(s))) if len(f) >= 8]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run")
    p.add_argument("--set", default="trqa")
    a = p.parse_args()
    run = Run(a.run)
    s1, corpus = Stage1(run, a.set), Corpus(run)
    cache: dict[str, str] = {}

    def doc(d: str) -> str:
        if d not in cache:
            cache[d] = clean(corpus.get_document(d) or "")
        return cache[d]

    def verbatim(snip, d):
        fs, t = frags(snip), doc(d)
        return bool(fs) and bool(t) and all(f in t for f in fs)

    def verbatim_any(snip, docs):
        """Every fragment must appear in SOME document verified for this hop."""
        fs, docs = frags(snip), [d for d in docs if d]
        if not fs or not docs:
            return False
        texts = [doc(d) for d in docs]
        return all(any(f in t for t in texts) for f in fs)

    src = {r["record_id"]: r for r in read_jsonl(s1.list_file("strict_list.jsonl"))}
    def survived(q):
        st = state(s1, q)
        return st["next"] == "done" and st["stage"] == "annotator" and st["outcome"] == "kept"

    survivors = sorted(q for q in src if survived(q))
    out, verif, unverified = [], Counter(), []
    for q in survivors:
        E, A = s1.load("extreme", q), s1.load("annotator", q)
        e_hops = {h["index"]: h for h in (E.get("hops") or [])}
        a_hops = {h["index"]: h for h in (A.get("hops") or [])}
        s, hops = src[q], []
        for i in sorted(e_hops):
            eh, ah = e_hops[i], a_hops.get(i, {})
            kind = eh.get("kind") or "evidence"
            allowed = set(eh.get("doc_ids") or []) | ({eh["doc_id"]} if eh.get("doc_id") else set())
            clue, docid, snip = eh.get("clue") or "", eh.get("doc_id") or "", eh.get("snippet") or ""
            if ah.get("verdict") == "fix":
                nd, ns = (ah.get("doc_id") or docid), (ah.get("snippet") or snip)
                if nd in allowed and (verbatim(ns, nd) or verbatim_any(ns, allowed)):
                    docid, snip = nd, ns
                    if ah.get("clue"):
                        clue = ah["clue"]
                    verif["fix-applied"] += 1
                else:
                    verif["fix-rejected"] += 1
            else:
                verif[ah.get("verdict", "keep")] += 1
            if kind == "evidence" and not (docid and (verbatim(snip, docid) or verbatim_any(snip, allowed))):
                unverified.append({"qid": q, "hop": i, "doc_id": docid})
            hops.append({"index": i, "kind": kind, "clue": clue,
                         "doc_id": docid if kind == "evidence" else "",
                         "supporting_doc_ids": sorted(allowed) if kind == "evidence" else [],
                         "snippet": snip})
        out.append({"record_id": q, "subset": s["subset"], "question": s["question"], "gold_answer": s["gold_answer"],
                    "operator": s["operator"], "target_attribute": s["target_attribute"],
                    "entity_set_description": s["entity_set_description"], "n_hops": len(hops), "hops": hops})
    write_jsonl(s1.list_file("final_list.jsonl"), out)
    write_json(s1.list_file("fix_verification.json"), dict(verif))
    write_json(s1.list_file("final_snippet_failures.json"), unverified)
    ev = [h for r in out for h in r["hops"] if h["kind"] == "evidence"]
    nh = sum(r["n_hops"] for r in out)
    print(f"questions: {len(out)} | hops: {nh} (evidence {len(ev)}, derivation {nh - len(ev)})")
    print("hop outcomes:", dict(verif))
    print(f"evidence hops verbatim-verified: {len(ev) - len(unverified)}/{len(ev)}")
    for u in unverified[:10]:
        print("  UNVERIFIED:", u["qid"], "hop", u["hop"], u["doc_id"])
    print("subsets:", dict(Counter(r["subset"] for r in out)))


if __name__ == "__main__":
    main()
