#!/usr/bin/env python3
"""Invented Stage 3 inputs and agent outputs for the demo run, so every deterministic step of the qrels pipeline can be
tried offline (test_stage3.py runs them all in order).

  python3 make_stage3_fixtures.py inputs RUN_DIR        Stage 2 output (final/demo_final.jsonl), search logs, the Stage 3
                                                       config, a duplicate map computed over demo/corpus.json, the hop
                                                       standards and recall query families the preparing agent writes
  python3 make_stage3_fixtures.py first  RUN_DIR [--phase2 N]          first-judge verdicts for every rendered chunk
  python3 make_stage3_fixtures.py second RUN_DIR --round K [--phase2 N] second-judge verdicts for round K
  python3 make_stage3_fixtures.py adjudicate RUN_DIR [--phase2 N]      adjudicated verdicts for every disagreement

The verdicts stand in for the judge agents: what a careful judge would decide on the invented documents, plus a
deliberately over-generous second judge on demo-4 (it keeps every sampled drop), so the escalation round runs.

Stage 3's own documents in demo/corpus.json are demo_doc_101 to demo_doc_106 (exact and near duplicates, a longer
page and a near miss); the Stage 2 demo uses demo_doc_001 to demo_doc_014. Keep the ranges apart.

SYNTHETIC CONTENT ONLY: every place, person and document is invented (the island nation of Oskeria). Listed as
[synthetic] in .leak-scan-allow. Never put a real benchmark item here.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = json.loads((HERE / "corpus.json").read_text())
SET = "demo"


def ev(doc, quote, supports="full"):
    return {"doc_id": doc, "supports": supports, "quotes": [quote]}


def hop(hid, clue, evidence, type_="required", role="inclusion", candidate=None, status="grounded", **kw):
    h = {"id": hid, "clue": clue, "type": type_, "role": role, "status": status, "evidence": evidence}
    if candidate:
        h["candidate"] = candidate
    h.update(kw)
    return h


# ----------------------------------------------------------------------------- Stage 2 output (accepted questions)
FINAL = [
    {"record_id": "demo-1", "set": SET, "answer_type": "single",
     "question": "Which river runs through the capital of the island nation of Oskeria?", "answer": "Ardune",
     "constraints": [{"id": "C1", "text": "the capital of the island nation of Oskeria", "covered_by": ["1"]},
                     {"id": "C2", "text": "Which river runs through", "covered_by": ["2", "A1", "A2"]}],
     "hops": [hop("1", "Velmaran is the capital of Oskeria.",
                  [ev("demo_doc_001", "Velmaran, the capital of Oskeria, sits on the northern coast")]),
              hop("2", "The Ardune runs through Velmaran.",
                  [ev("demo_doc_002", "The Ardune rises in the Morrowfen hills and runs through Velmaran before reaching the sea at Port Ilse.")]),
              hop("A1", "The Selm does not pass through Velmaran.",
                  [ev("demo_doc_003", "It does not pass through Velmaran.")], role="exclusion", candidate="Selm"),
              hop("A2", "The Varrow flows through the Brannoch marshes, not Velmaran.",
                  [ev("demo_doc_004", "flows south through the Brannoch marshes to the Gulf of Amberly")],
                  role="exclusion", candidate="Varrow")],
     "candidates": [{"name": "Ardune", "role": "answer", "status": "qualifies", "hops": ["1", "2"]},
                    {"name": "Selm", "role": "rival", "status": "excluded", "hops": ["A1"]},
                    {"name": "Varrow", "role": "rival", "status": "excluded", "hops": ["A2"]}],
     "search_queries": ["capital of Oskeria", "river Velmaran", "rivers of Oskeria"]},
    {"record_id": "demo-2", "set": SET, "answer_type": "set",
     "question": "Which rivers of Oskeria longer than twenty kilometres rise in the Morrowfen hills?",
     "answer": ["Ardune", "Varrow"],
     "hops": [hop("1", "The Ardune rises in the Morrowfen hills.", [ev("demo_doc_002", "The Ardune rises in the Morrowfen hills")]),
              hop("2", "The Varrow rises in the Morrowfen hills.", [ev("demo_doc_004", "The Varrow rises in the Morrowfen hills")]),
              hop("A1", "Oskeria has three rivers longer than twenty kilometres: the Ardune, the Selm and the Varrow.",
                  [ev("demo_doc_005", "The island has three rivers longer than twenty kilometres: the Ardune, the Selm and the Varrow.")]),
              hop("A2", "The Selm rises on Mount Corran, not in the Morrowfen hills.",
                  [ev("demo_doc_003", "The Selm rises on the slopes of Mount Corran")], role="exclusion", candidate="Selm"),
              hop("A3", "The Ardune's quays in Velmaran were rebuilt after the flood of 1911.",
                  [ev("demo_doc_002", "Its quays in Velmaran were rebuilt after the flood of 1911.")], status="dropped",
                  note="clue removed in revision 1: the quays are not part of the question")],
     "candidates": [{"name": "Ardune", "role": "member", "status": "qualifies", "hops": ["1", "A1"]},
                    {"name": "Varrow", "role": "member", "status": "qualifies", "hops": ["2", "A1"]},
                    {"name": "Selm", "role": "rival", "status": "excluded", "hops": ["A2"]}],
     "completeness": {"basis": "list_document", "doc_ids": ["demo_doc_005"],
                      "note": "The rivers list names three rivers; the Selm is excluded by A2."},
     "search_queries": ["rivers rising in the Morrowfen hills", "Varrow source"]},
    {"record_id": "demo-3", "set": SET, "answer_type": "single",
     "question": "In what year did the composer of the opera Lanterns of Morrowfen found the Velmaran Conservatory?",
     "answer": "1874",
     "hops": [hop("1", "Lanterns of Morrowfen was composed by Ilsa Renvaar.",
                  [ev("demo_doc_006", "Lanterns of Morrowfen, an opera in three acts, was composed by Ilsa Renvaar")]),
              hop("2", "Ilsa Renvaar founded the Velmaran Conservatory in 1874.",
                  [ev("demo_doc_007", "Ilsa Renvaar founded the Velmaran Conservatory in 1874")])],
     "candidates": [{"name": "1874", "role": "answer", "status": "qualifies", "hops": ["1", "2"]}],
     "search_queries": ["Velmaran Conservatory founded", "Ilsa Renvaar conservatory", "Lanterns of Morrowfen opera"]},
    {"record_id": "demo-4", "set": SET, "answer_type": "single",
     "question": "How many years before the Port Ilse Conservatory was the Velmaran Conservatory founded?", "answer": "6",
     "hops": [hop("1", "The Velmaran Conservatory was founded in 1874.",
                  [ev("demo_doc_007", "Ilsa Renvaar founded the Velmaran Conservatory in 1874")]),
              hop("2", "The Port Ilse Conservatory was founded in 1880.",
                  [ev("demo_doc_008", "The Port Ilse Conservatory was founded in 1880 by Davin Kesh.")]),
              hop("3", "1880 - 1874 = 6 years.", [], type_="derivation", status="derivation_ok", inputs=["1", "2"],
                  note="difference of the two founding years")],
     "candidates": [{"name": "6", "role": "answer", "status": "qualifies", "hops": ["1", "2", "3"]}],
     "search_queries": ["Port Ilse Conservatory", "Velmaran Conservatory"]},
]

# every search the projection, audit and revision agents ran, as cm.py logs them
SEARCHLOGS = {
    "demo-1": [("capital of Oskeria", ["demo_doc_001", "demo_doc_010"]),
               ("river Velmaran", ["demo_doc_002", "demo_doc_003", "demo_doc_011"]),
               ("rivers of Oskeria", ["demo_doc_005", "demo_doc_012"])],
    "demo-2": [("rivers rising in the Morrowfen hills", ["demo_doc_012", "demo_doc_002", "demo_doc_004"]),
               ("Varrow source", ["demo_doc_004", "demo_doc_012"])],
    "demo-3": [("Velmaran Conservatory founded", ["demo_doc_008", "demo_doc_007", "demo_doc_103", "demo_doc_106"]),
               ("Ilsa Renvaar conservatory", ["demo_doc_007", "demo_doc_011", "demo_doc_106", "demo_doc_103"]),
               ("Lanterns of Morrowfen opera", ["demo_doc_006", "demo_doc_103"])],
    "demo-4": [("Port Ilse Conservatory", ["demo_doc_008", "demo_doc_105", "demo_doc_001"]),
               ("Velmaran Conservatory", ["demo_doc_007", "demo_doc_103", "demo_doc_106", "demo_doc_011"])],
}

QRELS_CONFIG = {"recall": "no_seed", "recall_k": 4, "recall_hits": 20, "tiers": {"drop_rate": 0.2, "min_drops": 2}}


def std(subject, full, partial=(), reject=()):
    return {"subject": subject, "full": full, "partial": list(partial), "reject": list(reject)}


STANDARDS = {
    "demo-1": {
        "1": std("Velmaran, the capital of Oskeria", "Velmaran is the capital of Oskeria",
                 reject=["Port Ilse, the second city", "pages naming Velmaran without saying it is the capital"]),
        "2": std("the Ardune (river)", "the Ardune runs or flows through Velmaran",
                 partial=["the Ardune has quays or bridges in Velmaran, when the text makes clear the river is in the city"],
                 reject=["the Selm and the Varrow: their facts belong to hops A1 and A2",
                         "the Ardune's source or mouth without Velmaran"]),
        "A1": std("the Selm (river)", "the Selm does not pass through Velmaran, or its course is given and avoids it",
                  reject=["where the Selm rises, without its course", "the Ardune's course"]),
        "A2": std("the Varrow (river)", "the Varrow's course, which does not run through Velmaran",
                  reject=["where the Varrow rises, without its course"])},
    "demo-2": {
        "1": std("the Ardune (river)", "the Ardune rises in the Morrowfen hills",
                 reject=["the Varrow and the Selm: their own hops", "the Ardune's course without its source"]),
        "2": std("the Varrow (river)", "the Varrow rises in the Morrowfen hills",
                 reject=["the Ardune and the Selm: their own hops", "the Varrow's mouth without its source"]),
        "A1": std("the rivers of Oskeria longer than twenty kilometres",
                  "a list or count of Oskeria's rivers longer than twenty kilometres (three: Ardune, Selm, Varrow)",
                  partial=["a list of Oskeria's main rivers without the length threshold"],
                  reject=["a page about one of the rivers"]),
        "A2": std("the Selm (river)", "the Selm rises outside the Morrowfen hills (on Mount Corran)",
                  reject=["the Selm's lower course (Hadley, Port Ilse) without its source"])},
    "demo-3": {
        "1": std("Ilsa Renvaar, composer; the opera Lanterns of Morrowfen",
                 "Renvaar composed Lanterns of Morrowfen ('her opera' counts)",
                 reject=["the opera's premiere without its composer"]),
        "2": std("Ilsa Renvaar; the Velmaran Conservatory", "Renvaar founded the Velmaran Conservatory in 1874",
                 partial=["Renvaar founded the Velmaran Conservatory, year not stated",
                          "the Velmaran Conservatory was founded in 1874, founder not named"],
                 reject=["the Port Ilse Conservatory (founded 1880 by Davin Kesh): another conservatory",
                         "the Renvaar Prize and other things named after her"])},
    "demo-4": {
        "1": std("the Velmaran Conservatory", "the Velmaran Conservatory was founded in 1874",
                 reject=["its founder without the year", "the Port Ilse Conservatory: hop 2"]),
        "2": std("the Port Ilse Conservatory", "the Port Ilse Conservatory was founded in 1880",
                 reject=["the Velmaran Conservatory: hop 1"])},
}

RECALL = {
    "demo-1": {"A1": {"queries": ["Selm river course", "Selm Mount Corran Hadley", "Selm joins Ardune below Port Ilse"],
                      "anchors": ["Selm"]},
               "A2": {"queries": ["Varrow river Brannoch marshes", "Varrow flows south", "Varrow Gulf of Amberly"],
                      "anchors": ["Varrow"]}},
    "demo-2": {"A1": {"queries": ["rivers of Oskeria longer than twenty kilometres", "three rivers Oskeria",
                                  "Oskeria rivers list"], "anchors": ["twenty kilometres", "three rivers"]},
               "A2": {"queries": ["Selm rises Mount Corran", "source of the Selm", "Selm river Hadley"],
                      "anchors": ["Selm"]}},
    "demo-4": {"1": {"queries": ["Velmaran Conservatory founded 1874", "founding of the Velmaran Conservatory",
                                 "Velmaran Conservatory history"], "anchors": ["Velmaran Conservatory"]},
               "2": {"queries": ["Port Ilse Conservatory founded 1880", "Port Ilse Conservatory Davin Kesh",
                                 "conservatory Port Ilse history"], "anchors": ["Port Ilse Conservatory"]}},
}


# ----------------------------------------------------------------------------- the judges' decisions
def keep(*hops):
    return {"keep": True, "hops": [{"hop": h, "supports": s, "quote": q} for h, s, q in hops]}


def drop(reason, note):
    return {"keep": False, "reason": reason, "note": note}


DEFAULT_DROP = drop("no_hop_fact", "States no fact any live hop of this question needs (demo verdict); would keep it if "
                                   "it stated one of the hop facts about the hop's subject.")
FIRST = {
    "demo-1": {
        "demo_doc_011": keep(("2", "partial", "walk along the Ardune quays")),
        "demo_doc_012": drop("no_hop_fact", "\"the Selm rises further west on Mount Corran\" gives where the Selm rises, "
                                            "not that it avoids Velmaran; would keep for A1 if it said so."),
        "demo_doc_005": drop("no_hop_fact", "Lists the three rivers without their courses; would keep for A1 or A2 if it "
                                            "said where the Selm or the Varrow flows."),
    },
    "demo-2": {
        "demo_doc_012": keep(("1", "full", "Two rivers rise there, the Ardune and the Varrow"),
                             ("2", "full", "Two rivers rise there, the Ardune and the Varrow"),
                             ("A2", "full", "the Selm rises further west on Mount Corran")),
    },
    "demo-3": {
        "demo_doc_008": drop("mention_only", "Names the Velmaran Conservatory without its founder or year; the founding it "
                                             "gives (1880, Davin Kesh) is the Port Ilse Conservatory's. Would keep if it "
                                             "named Renvaar as the founder."),
        "demo_doc_103": keep(("1", "full", "the composer Ilsa Renvaar, whose opera Lanterns of Morrowfen"),
                             ("2", "full", "The Velmaran Conservatory was founded in 1874 by the composer Ilsa Renvaar")),
        "demo_doc_011": keep(("2", "partial", "visit the conservatory founded by Ilsa Renvaar")),
        "demo_doc_106": drop("mention_only", "Names Renvaar and the conservatory (a prize named after her) but not who "
                                             "founded it or when; would keep if it stated the founding."),
    },
    "demo-4": {
        "demo_doc_103": keep(("1", "full", "The Velmaran Conservatory was founded in 1874")),
        "demo_doc_105": keep(("2", "full", "The Port Ilse Conservatory was founded in 1880 by Davin Kesh")),
        "demo_doc_011": drop("no_hop_fact", "\"the conservatory founded by Ilsa Renvaar\": the founder, not the year; "
                                            "would keep for hop 1 if it gave 1874."),
        "demo_doc_104": drop("no_hop_fact", "The conservatory's later history (Renvaar's death in 1901, the 1932 move), "
                                            "not its founding year; would keep if it gave 1874."),
        "demo_doc_106": drop("mention_only", "A prize the Velmaran Conservatory awards; no founding year."),
    },
}
SECOND = {   # the blind second judge, where it differs from the first
    "demo-1": {"demo_doc_011": drop("no_hop_fact", "Travel notes: \"walk along the Ardune quays\" does not say the river "
                                                   "runs through Velmaran; would keep if it stated the river's course.")},
}
OVERGENEROUS = {"demo-4"}      # round 1: the second judge keeps every sampled drop (exercises the escalation round)
ADJUDICATE = {   # where the adjudicator does not side with the first judge
    "demo-1": {"demo_doc_011": SECOND["demo-1"]["demo_doc_011"]},
}
PHASE2 = {   # near-duplicate verification
    "demo-1": {"demo_doc_102": keep(("2", "full", "The Ardune rises in the Morrowfen hills and runs through Velmaran "
                                                   "before reaching the sea at Port Ilse."))},
    "demo-2": {"demo_doc_102": keep(("1", "full", "The Ardune rises in the Morrowfen hills"))},
    "demo-3": {"demo_doc_104": drop("no_hop_fact", "This copy lacks the founding sentence its parent was kept for; it gives "
                                                   "Renvaar's death in 1901, not the founding or the opera.")},
    "demo-4": {"demo_doc_104": drop("no_hop_fact", "This copy lacks the founding year its parent was kept for.")},
}


def first_sentence(doc):
    return re.split(r"(?<=\.)\s", CORPUS[doc], 1)[0]


def decide(qid, doc, who, rnd=1, phase2=None):
    table = PHASE2 if phase2 else FIRST
    base = table.get(qid, {}).get(doc, DEFAULT_DROP)
    if who == "second" and not phase2:
        if qid in OVERGENEROUS and rnd == 1 and not base["keep"]:
            return keep(("1", "partial", first_sentence(doc)))
        return SECOND.get(qid, {}).get(doc, base)
    if who == "adjudicate" and not phase2:
        return ADJUDICATE.get(qid, {}).get(doc, base)
    return base


# ----------------------------------------------------------------------------- writers
def write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


def shingles(text):
    w = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    return {tuple(w[i:i + 5]) for i in range(len(w) - 4)}


def duplicate_rows():
    """The corpus_duplicates rows of the demo corpus, computed the way the released map was: exact groups by SHA-256 over
    text.strip(), near pairs by exact 5-gram word-shingle Jaccard >= 0.7 (lists disjoint, highest Jaccard first)."""
    group = {d: hashlib.sha256(t.strip().encode()).hexdigest() for d, t in CORPUS.items()}
    exact = {d: sorted(e for e in CORPUS if e != d and group[e] == group[d]) for d in CORPUS}
    sh = {d: shingles(t) for d, t in CORPUS.items()}
    near = {d: [] for d in CORPUS}
    for a, b in itertools.combinations(sorted(CORPUS), 2):
        if group[a] == group[b] or not sh[a] or not sh[b]:
            continue
        j = len(sh[a] & sh[b]) / len(sh[a] | sh[b])
        if j >= 0.7:
            near[a].append({"doc_id": b, "jaccard": round(j, 4)})
            near[b].append({"doc_id": a, "jaccard": round(j, 4)})
    rows = []
    for d in sorted(CORPUS):
        if exact[d] or near[d]:
            ns = sorted(near[d], key=lambda n: (-n["jaccard"], n["doc_id"]))
            rows.append({"doc_id": d, "exact_duplicates": exact[d], "near_duplicates": ns,
                         "n_exact": len(exact[d]), "n_near": len(ns)})
    return rows


def cmd_inputs(run: Path, a) -> None:
    cfg_path = run / "cmass.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["sets"][SET]["qrels"] = QRELS_CONFIG
    rows = duplicate_rows()
    (run / "dups").mkdir(parents=True, exist_ok=True)
    write(run / "dups" / "corpus_duplicates.json", rows)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(pa.Table.from_pylist(rows), run / "dups" / "corpus_duplicates.parquet")
        cfg["duplicates"] = {"path": "dups/corpus_duplicates.parquet", "threshold": 0.7, "id_pad": None}
    except ImportError:
        cfg["duplicates"] = {"path": "dups/corpus_duplicates.json", "threshold": 0.7, "id_pad": None}
    cfg_path.write_text(json.dumps(cfg, indent=1) + "\n")
    (run / "final").mkdir(parents=True, exist_ok=True)
    (run / "final" / f"{SET}_final.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in FINAL))
    for qid, lines in SEARCHLOGS.items():
        p = run / "work" / SET / f"{qid}.searchlog.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps({"query": q, "hits": 10, "results": res}) + "\n" for q, res in lines))
    for qid, hops in STANDARDS.items():
        write(run / "qrels" / SET / "standards" / f"{qid}.json", {"qid": qid, "hops": hops})
    for qid, hops in RECALL.items():
        write(run / "qrels" / SET / "recall" / f"{qid}.queries.json", {"qid": qid, "hops": hops})
    print(json.dumps({"run": str(run), "final": len(FINAL), "searchlogs": len(SEARCHLOGS), "duplicate_rows": len(rows),
                      "duplicates": cfg["duplicates"]["path"]}))


def judging_dirs(run: Path, phase2):
    base = run / "qrels" / SET / ("phase2/r%d" % phase2 if phase2 else "")
    return base / "work", base / "judged"


def cmd_first(run: Path, a) -> None:
    work, judged = judging_dirs(run, a.phase2)
    n = 0
    for cj in sorted(work.glob("*/chunks.json")):
        qid = cj.parent.name
        for c, docs in json.loads(cj.read_text()).items():
            write(judged / qid / f"chunk_{c}.json",
                  {"qid": qid, "chunk": c, "verdicts": [{"doc_id": d, **decide(qid, d, "first", phase2=a.phase2)} for d in docs]})
            n += len(docs)
    print(json.dumps({"first_verdicts": n}))


def cmd_second(run: Path, a) -> None:
    work, judged = judging_dirs(run, a.phase2)
    stem = "second" if a.round == 1 else f"second{a.round}"
    n = 0
    for m in sorted(work.glob(f"*/{stem}.json")):
        qid = m.parent.name
        for c, docs in json.loads(m.read_text())["chunks"].items():
            write(judged / qid / f"{stem}_{c}.json",
                  {"qid": qid, "chunk": f"{stem}_{c}",
                   "verdicts": [{"doc_id": d, **decide(qid, d, "second", a.round, a.phase2)} for d in docs]})
            n += len(docs)
    print(json.dumps({"round": a.round, "second_verdicts": n}))


def cmd_adjudicate(run: Path, a) -> None:
    _, judged = judging_dirs(run, a.phase2)
    n = 0
    for cp in sorted(judged.glob("*/second*_compare.json")):
        qid = cp.parent.name
        for part, docs in (json.loads(cp.read_text()).get("parts") or {}).items():
            write(judged / qid / f"adjudicated_{part}.json",
                  {"qid": qid, "chunk": f"adjudicated_{part}",
                   "verdicts": [{"doc_id": d, **decide(qid, d, "adjudicate", phase2=a.phase2)} for d in docs]})
            n += len(docs)
    print(json.dumps({"adjudicated": n}))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("inputs", "first", "second", "adjudicate"))
    ap.add_argument("run")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--phase2", type=int)
    a = ap.parse_args()
    run = Path(a.run).resolve()
    if not (run / "cmass.json").exists():
        sys.exit(f"{run}: not a run directory (create one with make_demo_run.py)")
    {"inputs": cmd_inputs, "first": cmd_first, "second": cmd_second, "adjudicate": cmd_adjudicate}[a.cmd](run, a)


if __name__ == "__main__":
    main()
