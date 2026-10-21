#!/usr/bin/env python3
"""TRQA expansion, step 3: choose the expansion candidates and write them as a Stage 2 input set.

  python3 select_and_build.py select [--run RUN] [--set trqa]   -> expansion/{pilot,recall_batch,trivial}_ids.json
  python3 select_and_build.py leads  [--run RUN] [--set trqa]   Wikipedia leads of their pool pages -> expansion/leads.json
  python3 select_and_build.py build  [--run RUN] [--set trqa]   -> inputs/xp_<set>.jsonl (records without hops)

The three batches, in the order the real run made them (all from questions that had not passed Stage 1):

  pilot         a stratified random sample of the questions whose whole pool is present (presence sweep) and
                whose pool has at most 12 pages (470 of the 678 in the real run): random.Random(20261008),
                strata and sizes as QUOTA below, each stratum's eligible ids sorted, rng.sample in QUOTA order.
                30 in the real run.
  recall_batch  every remaining all-present question (any pool size) whose earlier projection recalled a lot:
                category half_or_more_recalled, gold_matched_partial_recall, supported_not_in_audit or
                full_recall_wrong_value (universe.py), not already in the pilot; ordered by pool size, largest
                first, ties in universe order. 122 in the real run.
                (Reconstructed: the real run committed the ids, not the code; this rule reproduces its 122 ids
                in their order exactly.)
  trivial       every question of universe_trivial.json, in its order (20 in the real run, none swept).
                (Reconstructed in the same way from the real run's id list.)

`leads` fetches a two-sentence Wikipedia introduction for every pool page of the selected questions
(identification only, never evidence). `build` writes one record per selected question (pilot, then recall
batch, then trivial) to the set xp_<set>'s input (inputs/xp_<set>.jsonl unless cmass.json says otherwise):
{record_id, question, answer, answer_type "aggregate", hops: [] (the Stage 2 auditor grounds every fact
as an added hop), hints (the Wikipedia leads, one per pool page), source, subset, operator,
target_attribute, entity_set_description (the earlier projector's reading, a lead), expansion_batch,
pool [{title, pageid, presence}] (presence: rank of the first corpus document containing the title, or
"not in top 50"), prior_projection {verdict, n_entities_expected, n_entities_recovered, computed_value,
rationale (first 900 characters), evidence (first 40, leads to check)}, expansion_note}.

Ported from the private repo's artifacts/projection_audit/tools/trqa_expansion.py (select, build; the
packets it wrote are now the records' extra fields, which tools/packets.py shows).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from cmass.run import Run, write_json, write_jsonl  # noqa: E402
from cmass.text import norm_ws  # noqa: E402

SEED = 20261008
MAX_POOL = 12
QUOTA = [("wiki1", None, 6), ("classic", None, 6), ("quest", "p105", 6), ("quest", "p577", 3), ("quest", "p407", 2),
         ("quest", "p495", 2), ("quest", "p2047", 2), ("quest", "p7937", 1), ("quest", "p50", 1), ("quest", "p9714", 1)]
RECALL_CATS = {"half_or_more_recalled", "gold_matched_partial_recall", "supported_not_in_audit", "full_recall_wrong_value"}
BATCHES = (("pilot", "pilot_ids.json"), ("recall_batch", "recall_batch_ids.json"), ("trivial", "trivial_ids.json"))
UA = {"User-Agent": "cmass-stage1/0.1 (research; trqa expansion)"}
NOTE = ("TRQA expansion candidate: the question had not passed Stage 1. The pool is TRQA's own candidate pool "
        "(Wikipedia pages); the Wikipedia leads in hints identify each candidate and are never evidence. "
        "'presence' gives the rank of the first corpus document (title search, top 50) containing the title: a lead "
        "to where to start reading. The earlier projector's reading and evidence are leads to check.")


def select(X: Path):
    rows = json.loads((X / "presence_by_question.json").read_text())
    rng = random.Random(SEED)
    pilot = []
    for pop, prop, n in QUOTA:
        pool = sorted(r["rid"] for r in rows if r["all_present"] and r["pop"] == pop and r["n_pool"] <= MAX_POOL
                      and (prop is None or r["prop"] == prop))
        pick = rng.sample(pool, min(n, len(pool)))
        print(f"pilot  {pop:8} {prop or '':6} {len(pool):4} eligible -> {len(pick)} picked")
        pilot += pick
    uni = json.loads((X / "universe.json").read_text())
    present = {r["rid"]: r["all_present"] for r in rows}
    taken = set(pilot)
    recall = [r for r in uni if r["cat"] in RECALL_CATS and present.get(r["rid"]) and r["rid"] not in taken]
    recall = [r["rid"] for r in sorted(recall, key=lambda r: -r["n_pool"])]          # stable: ties keep universe order
    triv_path = X / "universe_trivial.json"
    trivial = [r["rid"] for r in json.loads(triv_path.read_text())] if triv_path.exists() else []
    for (name, fn), ids in zip(BATCHES, (pilot, recall, trivial)):
        write_json(X / fn, ids)
        print(f"{name}: {len(ids)}")


def selected(X: Path) -> list[tuple[str, str]]:
    out = []
    for name, fn in BATCHES:
        p = X / fn
        out += [(rid, name) for rid in (json.loads(p.read_text()) if p.exists() else [])]
    return out


def universe_rows(X: Path) -> dict[str, dict]:
    uni = {r["rid"]: r for r in json.loads((X / "universe.json").read_text())}
    tp = X / "universe_trivial.json"
    if tp.exists():
        uni |= {r["rid"]: r for r in json.loads(tp.read_text())}
    return uni


def leads(X: Path):
    uni = universe_rows(X)
    pageids = sorted({p for rid, _ in selected(X) for p in uni[rid]["pool"]}, key=int)
    path = X / "leads.json"
    out = json.loads(path.read_text()) if path.exists() else {}
    todo = [p for p in pageids if p not in out]
    for i in range(0, len(todo), 20):
        q = urllib.parse.urlencode({"action": "query", "format": "json", "prop": "extracts", "exintro": 1, "explaintext": 1,
                                    "exsentences": 2, "exlimit": 20, "pageids": "|".join(todo[i:i + 20])})
        req = urllib.request.Request(f"https://en.wikipedia.org/w/api.php?{q}", headers=UA)
        pages = {}
        for attempt in range(6):
            try:
                pages = json.load(urllib.request.urlopen(req, timeout=60))["query"]["pages"]
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 5:
                    raise
                time.sleep(10 * (attempt + 1))
        for p, v in pages.items():
            out[p] = {"title": v.get("title"), "lead": norm_ws(v.get("extract") or "")}
        write_json(path, out)
        time.sleep(2)
    print(f"{len(out)} leads for {len(pageids)} pool pages")


def build(run: Run, sid: str, X: Path):
    uni = universe_rows(X)
    L = json.loads((X / "leads.json").read_text()) if (X / "leads.json").exists() else {}
    pres = {}
    if (X / "presence.jsonl").exists():
        for line in open(X / "presence.jsonl"):
            if line.strip():
                r = json.loads(line)
                pres[r["pageid"]] = r
    task_src = {t["record_id"]: t.get("source") for t in run.tasks(sid)}
    recs = []
    for rid, batch in selected(X):
        u = uni[rid]
        pf = run.stage1_file(sid, "project", rid)
        pr = json.loads(pf.read_text()) if pf.exists() else {}
        pool, hints = [], []
        for p in u["pool"]:
            v, s = L.get(p, {}), pres.get(p, {})
            hit = (f"rank {s['first_rank']} `{s['docs'][0]}`" if s.get("first_rank") else "not in top 50") if s else "not swept"
            title = v.get("title") or s.get("title")
            pool.append({"title": title, "pageid": p, "presence": hit})
            if v.get("lead"):
                hints.append(f"{title} (Wikipedia page {p}, identification only): {v['lead'][:280]}")
        prior = {"verdict": pr.get("verdict"), "n_entities_expected": pr.get("n_entities_expected"),
                 "n_entities_recovered": pr.get("n_entities_recovered"), "computed_value": pr.get("computed_value"),
                 "rationale": norm_ws(pr.get("rationale") or "")[:900],
                 "evidence": [{"entity": norm_ws(str(e.get("entity")))[:160], "attribute_value": norm_ws(str(e.get("attribute_value")))[:80],
                               "doc_id": e.get("doc_id")} for e in (pr.get("evidence") or [])[:40]]}
        recs.append({"record_id": rid, "question": u["q"], "answer": u["a"], "answer_type": "aggregate", "hops": [],
                     "hints": hints, "source": task_src.get(rid), "subset": rid.split("__")[0],
                     "operator": pr.get("operator"), "target_attribute": pr.get("target_attribute"),
                     "entity_set_description": pr.get("entity_set_description"), "expansion_batch": batch,
                     "pool": pool, "prior_projection": prior, "expansion_note": NOTE})
    s = run.config.sets.get(f"xp_{sid}")
    out = run.root / (s["input"] if s else f"inputs/xp_{sid}.jsonl")
    write_jsonl(out, recs)
    print(f"wrote {len(recs)} expansion candidates to {out}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["select", "leads", "build"])
    p.add_argument("--run")
    p.add_argument("--set", default="trqa")
    a = p.parse_args()
    run = Run(a.run)
    X = run.stage1_dir(a.set) / "expansion"
    {"select": lambda: select(X), "leads": lambda: leads(X), "build": lambda: build(run, a.set, X)}[a.cmd]()


if __name__ == "__main__":
    main()
