#!/usr/bin/env python3
"""TRQA expansion, step 1: the universe of questions outside the strict-audited ones, with TRQA's own pools.

  python3 universe.py --raw DIR [--run RUN] [--set trqa]

Reads the raw TRQA files (`../trqa_source.py download --out DIR`: test queries and qrels, decrypted in
memory), the projector's verdicts (stage1/<set>/verdicts.jsonl) and the Stage 2 inputs Stage 1 wrote
(inputs/<set>.jsonl and its near misses: the strict-audited questions, 87 in the real run). For every wiki1 /
wiki2 question NOT among those (e-commerce pools are TRQA's synthetic catalogue and are left out) it writes:

  rid, pop (wiki1 | classic | quest: wiki2 ids containing "quest_"), prop (the id's last "_" part), q, a
  (decrypted, answer stripped), pool (TRQA's qrel documents cut to their Wikipedia page ids: the part before
  the last "-", sorted), n_pool, verdict / n_exp / n_rec / matches / complete (the projector's), and cat, the
  prior-recall category:
    supported_not_in_audit        verdict SUPPORTED (the strict audit dropped it)
    full_recall_wrong_value       recall complete, aggregate != gold
    gold_matched_partial_recall   aggregate == gold, recall incomplete
    half_or_more_recalled         n_rec >= 0.5 * n_exp
    some_recalled / nothing_recalled

A "trivial" question (a four-digit answer that appears as a word in the question, e.g. an earliest
publication year among novels published in that year) goes to universe_trivial.json instead of
universe.json. Outputs: stage1/<set>/expansion/universe.json, universe_trivial.json (decrypted TRQA
content: keep the run directory private). In the real run: 1,322 questions in the universe, 20 trivial.

Ported from the private repo's artifacts/projection_audit/tools/trqa_hf.py ("universe").
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE.parent))
from cmass.run import Run, read_jsonl, write_json  # noqa: E402
import trqa_source  # noqa: E402


def category(r: dict) -> str:
    if r["verdict"] == "SUPPORTED":
        return "supported_not_in_audit"
    if r["complete"] and not r["matches"]:
        return "full_recall_wrong_value"
    if r["matches"] and not r["complete"]:
        return "gold_matched_partial_recall"
    if r["n_rec"] and r["n_exp"] and r["n_rec"] >= 0.5 * r["n_exp"]:
        return "half_or_more_recalled"
    return "some_recalled" if r["n_rec"] else "nothing_recalled"


def audited_ids(run: Run, sid: str) -> set[str]:
    out = set()
    for name in (sid, f"nm_{sid}"):
        s = run.config.sets.get(name)
        default = f"inputs/{sid}.jsonl" if name == sid else f"inputs/{sid}_near_misses.jsonl"
        out |= {str(r["record_id"]) for r in read_jsonl(run.root / (s["input"] if s else default))}
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run")
    p.add_argument("--set", default="trqa")
    p.add_argument("--raw", required=True, help="directory with the raw TRQA files (trqa_source.py download)")
    a = p.parse_args()
    run = Run(a.run)
    V = {r["record_id"]: r for r in read_jsonl(run.stage1_dir(a.set) / "verdicts.jsonl")}
    if not V:
        raise SystemExit("no stage1/<set>/verdicts.jsonl: run collect_verdicts.py first")
    audited = audited_ids(run, a.set)
    if not audited:
        raise SystemExit("no Stage 2 inputs: run write_stage2_inputs.py first (the universe excludes those questions)")
    rows, trivial = [], []
    for sub in ("wiki1", "wiki2"):
        Q, R = trqa_source.load_raw(Path(a.raw), sub, with_qrels=True)
        for x in Q:
            qid, q, ans = x["id"], x["question"], str(x["answer"]).strip()
            rid = trqa_source.stem(sub, qid)
            v = V.get(rid, {})
            pool = sorted({d.rsplit("-", 1)[0] for d in R[qid]})
            r = dict(rid=rid, pop="wiki1" if sub == "wiki1" else ("quest" if "quest_" in qid else "classic"),
                     prop=qid.rsplit("_", 1)[-1], q=q, a=ans, pool=pool, n_pool=len(pool), verdict=v.get("verdict"),
                     n_exp=v.get("n_exp"), n_rec=v.get("n_rec"), matches=v.get("matches_gold"),
                     complete=v.get("recall_complete"), audited=rid in audited,
                     trivial=bool(re.fullmatch(r"\d{4}", ans) and re.search(rf"\b{ans}\b", q)))
            if not r["audited"]:
                r["cat"] = category(r)
                (trivial if r["trivial"] else rows).append(r)
    X = run.stage1_dir(a.set) / "expansion"
    write_json(X / "universe.json", rows)
    write_json(X / "universe_trivial.json", trivial)
    print(f"{len(rows)} questions in the expansion universe; {len(trivial)} trivial ones in universe_trivial.json")


if __name__ == "__main__":
    main()
