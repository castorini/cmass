#!/usr/bin/env python3
"""Deterministic selection and staging steps of HotpotQA Stage 1 (no agent, no corpus request).

  select_questions.py [--run RUN] nanoknow SET          # path A: take every NanoKnow-supported question, its
                                                        #   NanoKnow qrels as candidate documents
  select_questions.py [--run RUN] pool SET              # path B: the pool to rank (NanoKnow-unsupported, bridge only)
  select_questions.py [--run RUN] batch SET [--size 150]    # path B: take the next SIZE questions of the ranking
  select_questions.py [--run RUN] stage-gates SET       # stage gate inputs for screened-clean bridge questions

Every question taken into Stage 1 is a row of stage1/<set>/selected.jsonl ({record_id, origin, batch,
rank, hotpot_type}); hpqa_state.py works from that list. "nanoknow" writes the contamination-screen input
stage1/<set>/input/<qid>.json at once (its candidate documents are NanoKnow's qrels); for a batch,
collect_docs.py retrieves the candidates and writes the input.

The order of path A is the order it was run: the contamination screen ran on all 553 supported questions,
comparison included, and the bridge-only filter (a decision taken during the run, after the screen, by the
person running it) came after it, in stage-gates and hpqa_state.py. Path B applied the type filter first,
in "pool".

stage-gates mirrors the original staging (jev/stage_batch.py "pipeline"): the gate input is the screen
input plus the screen's own two counts, docs_needed_per_screen and hops_needed_per_screen.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hpqa_common import (file, input_record, load_json, lst, read_jsonl, run_of, selected,  # noqa: E402
                         tasks_by_id, write_json, write_jsonl, write_selected)


def cmd_nanoknow(run, sid, a):
    tasks = tasks_by_id(run, sid)
    sel = selected(run, sid)
    added = staged = 0
    for q, t in tasks.items():
        if t.get("nanoknow") != "supported":
            continue
        if q not in sel:
            sel[q] = {"record_id": q, "origin": "nanoknow", "batch": None, "rank": None, "hotpot_type": t["hotpot_type"]}
            added += 1
        p = file(run, sid, "input", q)
        if not p.exists():
            write_json(p, input_record(q, t["question"], t["gold_answers"], t["nanoknow_doc_ids"]))
            staged += 1
    write_selected(run, sid, sel)
    n = [r for r in sel.values() if r["origin"] == "nanoknow"]
    print(f"nanoknow: {len(n)} selected ({added} new), {staged} inputs staged | "
          f"types {dict(Counter(r['hotpot_type'] for r in n))}")


def cmd_pool(run, sid, a):
    tasks = tasks_by_id(run, sid)
    pool = [{"record_id": q} for q, t in tasks.items()
            if t.get("nanoknow") == "unsupported" and t.get("hotpot_type") == "bridge"]
    write_jsonl(lst(run, sid, "pool"), pool)
    uns = sum(1 for t in tasks.values() if t.get("nanoknow") == "unsupported")
    print(f"pool: {uns} NanoKnow-unsupported -> {len(pool)} bridge (comparison excluded by HotpotQA's type field)")


def cmd_batch(run, sid, a):
    ranked = read_jsonl(lst(run, sid, "ranked"))
    if not ranked:
        sys.exit("no ranking: run rank_screen.py screen and order first")
    tasks = tasks_by_id(run, sid)
    sel = selected(run, sid)
    n = max([r["batch"] or 0 for r in sel.values()] + [0]) + 1
    take = [r for r in ranked if r["record_id"] not in sel][:a.size]
    for r in take:
        q = r["record_id"]
        sel[q] = {"record_id": q, "origin": "ranked", "batch": n, "rank": r["rank"], "hotpot_type": tasks[q]["hotpot_type"]}
    write_selected(run, sid, sel)
    if take:
        print(f"batch {n}: {len(take)} questions, ranks {take[0]['rank']}-{take[-1]['rank']}, "
              f"weakest-fact scores {take[-1]['weakest_fact']}-{take[0]['weakest_fact']}")
    else:
        print("the ranking is exhausted")
    print(json.dumps([r["record_id"] for r in take]))


def cmd_stage_gates(run, sid, a):
    sel = selected(run, sid)
    staged = ready = 0
    unscreened = 0
    for q, row in sel.items():
        inp = load_json(file(run, sid, "input", q))
        cx = load_json(file(run, sid, "contamination", q))
        if not inp or "_error" in inp:
            continue
        if not cx or "_error" in cx:
            unscreened += 1
            continue
        if not cx.get("clean") or row["hotpot_type"] != "bridge":
            continue
        ready += 1
        p = file(run, sid, "gate_input", q)
        if not p.exists():
            write_json(p, {**inp, "docs_needed_per_screen": cx.get("distinct_docs_needed"),
                           "hops_needed_per_screen": cx.get("hops_needed")})
            staged += 1
    print(json.dumps({"staged": staged, "ready": ready, "not_yet_screened": unscreened}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="run directory (default: $CMASS_RUN or the current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("nanoknow", "pool", "stage-gates"):
        sub.add_parser(name).add_argument("set")
    b = sub.add_parser("batch")
    b.add_argument("set")
    b.add_argument("--size", type=int, default=150, help="questions per batch (150 in every original batch)")
    a = ap.parse_args()
    run = run_of(a.run)
    run.config.set(a.set)
    {"nanoknow": cmd_nanoknow, "pool": cmd_pool, "batch": cmd_batch, "stage-gates": cmd_stage_gates}[a.cmd](run, a.set, a)


if __name__ == "__main__":
    main()
