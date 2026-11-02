#!/usr/bin/env python3
"""Path B, candidate documents: a ranked question has no qrels, so retrieve its candidate documents from
the corpus and stage its contamination-screen input. A port of the original jev/collect_docs.py (run with
--k 6) followed by the staging in jev/stage_batch.py "contamination".

  collect_docs.py [--run RUN] SET --batch N [--k 6] [--hits 20] [--workers 4]
  collect_docs.py [--run RUN] SET --ids FILE ...

Per question, the same retrieval the ranking used, but every candidate is kept rather than the best one:
for each official supporting fact (title, sentence), search for "<title> <sentence>" and for the title
alone (first 400 characters of each query, 20 hits) and keep the top K documents of each search that have
text. doc_ids is the sorted union (the run averaged 22 documents per question). Every document is fetched
once, so it sits in the run's document cache when the agents read it.

Writes stage1/<set>/collect/<qid>.json ({qid, question, gold_answers, doc_ids, n_facts, queries}) and
stage1/<set>/input/<qid>.json (the screen input: qid, question, gold_answers, doc_ids). A question whose
retrieval fails is skipped and reported, never written with an empty pool; rerun to retry it. These
searches are not written to the agents' search logs.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hpqa_common import SerialCorpus, file, input_record, run_of, selected, tasks_by_id, write_json  # noqa: E402
from cmass.corpus import Corpus, CorpusError  # noqa: E402

QUERY_CHARS = 400


def collect(corpus: Corpus, task: dict, k: int, hits: int) -> dict:
    seen, queries = {}, []
    for f in task.get("supporting_facts") or []:
        for q in (f"{f['title']} {f['fact']}", f["title"]):
            q = q[:QUERY_CHARS]
            kept = []
            for c in corpus.search(q, hits)[:k]:
                d = c.get("docid")
                if not d:
                    continue
                if d not in seen:
                    text = corpus.get_document(d)
                    if not (text and text.strip()):        # the original kept only documents with text
                        continue
                    seen[d] = True
                kept.append(d)
            queries.append({"query": q, "doc_ids": kept})
    return {"qid": task["record_id"], "question": task["question"], "gold_answers": task["gold_answers"],
            "doc_ids": sorted(seen), "n_facts": len(task.get("supporting_facts") or []), "queries": queries}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="run directory (default: $CMASS_RUN or the current directory)")
    ap.add_argument("set")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--batch", type=int, help="a batch taken with select_questions.py batch")
    g.add_argument("--ids", help="JSON list of selected record ids")
    ap.add_argument("--k", type=int, default=6, help="documents kept per search (6 in the run)")
    ap.add_argument("--hits", type=int, default=20)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    run = run_of(a.run)
    sid = a.set
    run.config.set(sid)
    sel = selected(run, sid)
    if a.batch is not None:
        ids = [q for q, r in sel.items() if r.get("batch") == a.batch]
    else:
        ids = [str(q) for q in json.loads(Path(a.ids).read_text())]
        missing = [q for q in ids if q not in sel]
        if missing:
            sys.exit(f"{len(missing)} ids are not selected (select_questions.py batch): {missing[:5]}")
    if not ids:
        sys.exit("no questions: take a batch first (select_questions.py batch)")
    tasks = tasks_by_id(run, sid)
    corpus = SerialCorpus(Corpus(run))
    todo = [q for q in ids if not file(run, sid, "collect", q).exists()]
    print(f"{len(ids)} questions, {len(todo)} to collect (k={a.k} per query)", flush=True)

    def one(q):
        try:
            rec = collect(corpus, tasks[q], a.k, a.hits)
        except CorpusError as e:
            print(f"  {q}: retrieval failed, skipped ({str(e)[:60]})", flush=True)
            return None
        write_json(file(run, sid, "collect", q), rec)
        return rec

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(one, todo))
    staged, have = 0, []
    for q in ids:
        p = file(run, sid, "collect", q)
        if not p.exists():
            continue
        rec = json.loads(p.read_text())
        have.append(rec)
        ip = file(run, sid, "input", q)
        if not ip.exists():
            write_json(ip, input_record(q, rec["question"], rec["gold_answers"], rec["doc_ids"]))
            staged += 1
    mean = sum(len(r["doc_ids"]) for r in have) / max(1, len(have))
    print(f"collected {len(have)}/{len(ids)} | mean docs/question {mean:.1f} | inputs staged {staged}")
    print(json.dumps([r["qid"] for r in have]))


if __name__ == "__main__":
    main()
