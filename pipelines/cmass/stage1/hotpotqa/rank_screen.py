#!/usr/bin/env python3
"""Path B, ranking: order the NanoKnow-unsupported bridge questions by how well the corpus appears to
support their weakest supporting fact, so the agents read the likeliest questions first. The ranking
never drops a question; batches are taken from its top (select_questions.py batch).

  rank_screen.py [--run RUN] screen SET --ranker RANKER [--ids FILE] [--hits 20] [--docs 5] [--workers 8]
  rank_screen.py [--run RUN] order SET                   # stage1/<set>/ranked.jsonl

Per question (a port of the original jev/screen.py, as it was run: --hits 20 --docs 5):
  for each official supporting fact (title, sentence) of the task:
    search the corpus for "<title> <sentence>" (first 400 characters), 20 hits; if that yields fewer than
    2 x docs distinct documents, also search for the title alone; keep the first `docs` distinct documents;
    ask the ranker whether each document states the fact (first 12,000 characters of its text);
    the fact's score is its best document's states_fact.
  weakest_fact = the lowest fact score, mean_fact = their mean; complete = every fact got a score.
A question whose retrieval fails is not written (a failed request must never read as "the corpus does
not support this"); rerun the command to retry it. Output: stage1/<set>/rank/<qid>.json.

order keeps the complete records and sorts them by weakest_fact, highest first. The original ranking
script was not kept and its tie order is not recoverable from the records; this port breaks ties by
mean_fact (highest first), then by record id.

RANKER is a Python file or module with a function

    judge(fact: str, fact_about: str, document: str) -> dict

returning {"states_fact": p, "same_entity": p, "grade": g, "cost": dollars} (probabilities in [0, 1]),
or {"error": "...", "cost": 0} when the call failed. The ranker that was run is Jev (see README.md,
"The ranking step"); stub_ranker.py is a word-overlap stand-in for the offline demo.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hpqa_common import (SerialCorpus, file, load_json, lst, natural_key, read_jsonl, run_of, tasks_by_id,  # noqa: E402
                         write_json, write_jsonl)
from cmass.corpus import Corpus, CorpusError  # noqa: E402

QUERY_CHARS = 400     # the original truncated every query to 400 characters
DOC_CHARS = 12000     # and sent the ranker the first 12,000 characters of a document


class RetrievalFailed(Exception):
    pass


def load_ranker(spec: str):
    p = Path(spec)
    if p.suffix == ".py" and p.exists():
        s = importlib.util.spec_from_file_location("hpqa_ranker", p)
        mod = importlib.util.module_from_spec(s)
        s.loader.exec_module(mod)
    else:
        mod = importlib.import_module(spec)
    if not callable(getattr(mod, "judge", None)):
        sys.exit(f"ranker {spec}: no judge(fact, fact_about, document) function")
    return mod.judge


def candidates(corpus: Corpus, fact: dict, hits: int, ndocs: int) -> list[str]:
    seen, cands = set(), []
    for q in (f"{fact['title']} {fact['fact']}", fact["title"]):
        try:
            res = corpus.search(q[:QUERY_CHARS], hits)
        except CorpusError as e:
            raise RetrievalFailed(str(e))
        for c in res:
            d = c.get("docid")
            if d and d not in seen:
                seen.add(d)
                cands.append(d)
        if len(cands) >= ndocs * 2:
            break
    return cands[:ndocs]


def screen(corpus: Corpus, judge, task: dict, hits: int, ndocs: int) -> dict:
    facts = task.get("supporting_facts") or []
    rows, cost = [], 0.0
    for f in facts:
        cands = candidates(corpus, f, hits, ndocs)
        best = None
        for d in cands:
            try:
                text = corpus.get_document(d)
            except CorpusError as e:
                raise RetrievalFailed(str(e))
            if not text:
                raise RetrievalFailed(f"empty document {d}")
            j = dict(judge(f["fact"], f["title"], " ".join(text.split())[:DOC_CHARS]) or {})
            cost += float(j.get("cost") or 0)
            j["doc_id"] = d
            if best is None or j.get("states_fact", 0) > best.get("states_fact", 0):
                best = j
        rows.append({"title": f["title"], "fact": f["fact"], "n_cands": len(cands), "best": best})
    got = [r["best"]["states_fact"] for r in rows if r["best"] and "states_fact" in r["best"]]
    ok = len(got) == len(facts) and len(facts) > 0
    return {"qid": task["record_id"], "question": task["question"], "answers": task["gold_answers"],
            "n_facts": len(facts), "complete": ok,
            "weakest_fact": (min(got) if ok else None), "mean_fact": (sum(got) / len(got) if ok else None),
            "facts": rows, "cost": cost}


def cmd_screen(run, sid, a):
    judge = load_ranker(a.ranker)
    tasks = tasks_by_id(run, sid)
    ids = (json.loads(Path(a.ids).read_text()) if a.ids else [r["record_id"] for r in read_jsonl(lst(run, sid, "pool"))])
    if not ids:
        sys.exit("nothing to screen: run select_questions.py pool first, or pass --ids")
    todo = [q for q in ids if not file(run, sid, "rank", q).exists()]
    print(f"{len(ids)} ids, {len(todo)} to screen (hits={a.hits} docs={a.docs})", flush=True)
    corpus = SerialCorpus(Corpus(run))           # ranker calls run in parallel, corpus calls one at a time
    total, failed = 0.0, []

    def one(q):
        try:
            r = screen(corpus, judge, tasks[q], a.hits, a.docs)
        except RetrievalFailed as e:
            print(f"  {q}: RETRIEVAL FAILED, not recorded: {str(e)[:80]}", flush=True)
            failed.append(q)
            return None
        write_json(file(run, sid, "rank", q), r)
        return r

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(one, todo), 1):
            if r:
                total += r["cost"]
            if i % 50 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} done  cost=${total:.4f}", flush=True)
    print(f"TOTAL ranker cost this run: ${total:.4f} | retrieval failures (rerun to retry): {len(failed)}")


def cmd_order(run, sid, a):
    recs = []
    for r in read_jsonl(lst(run, sid, "pool")):
        x = load_json(file(run, sid, "rank", r["record_id"]))
        if x and "_error" not in x:
            recs.append(x)
    ok = [r for r in recs if r.get("complete")]
    ok.sort(key=lambda r: (-r["weakest_fact"], -r["mean_fact"], natural_key(r["qid"])))
    rows = [{"rank": i, "record_id": r["qid"], "weakest_fact": r["weakest_fact"], "mean_fact": r["mean_fact"],
             "n_facts": r["n_facts"]} for i, r in enumerate(ok, 1)]
    write_jsonl(lst(run, sid, "ranked"), rows)
    print(f"ranked {len(rows)} of {len(recs)} screened ({len(recs) - len(rows)} incomplete, not ranked)")
    for t in (0.95, 0.7):
        print(f"  weakest_fact >= {t}: {sum(1 for r in rows if r['weakest_fact'] >= t)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="run directory (default: $CMASS_RUN or the current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("screen")
    s.add_argument("set")
    s.add_argument("--ranker", required=True, help="Python file or module with judge(fact, fact_about, document)")
    s.add_argument("--ids", help="JSON list of record ids (default: stage1/<set>/pool.jsonl)")
    s.add_argument("--hits", type=int, default=20)
    s.add_argument("--docs", type=int, default=5, help="documents judged per fact (5 in the run)")
    s.add_argument("--workers", type=int, default=8)
    o = sub.add_parser("order")
    o.add_argument("set")
    a = ap.parse_args()
    run = run_of(a.run)
    run.config.set(a.set)
    {"screen": cmd_screen, "order": cmd_order}[a.cmd](run, a.set, a)


if __name__ == "__main__":
    main()
