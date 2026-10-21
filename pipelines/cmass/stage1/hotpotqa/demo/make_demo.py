#!/usr/bin/env python3
"""Offline demo of HotpotQA Stage 1: builds a run directory (default stage1/hotpotqa/demo/run, gitignored) and
runs every deterministic step of both paths on hand-written agent outputs, ending with Stage 2 inputs that
tools/packets.py reads.

  python3 pipelines/cmass/stage1/hotpotqa/demo/make_demo.py [RUN_DIR]

SYNTHETIC CONTENT ONLY: nine invented "HotpotQA-shaped" questions about the invented island nation of Oskeria,
invented NanoKnow files and invented documents (demo/corpus.json, served by pipelines/cmass/demo/demo_cm.py).
No real benchmark item is used. The ranker is stub_ranker.py, not the ranker that was run.

What the demo covers (record_id = row of the invented dev set):
  path A, NanoKnow-supported:  0 survives every gate (one annotator fix applied under the guard)
                               1 leaked (a quiz page poses it and answers it)       -> dropped at the screen
                               2 comparison, clean                                  -> excluded (bridge only)
                               3 a qualifier no document states                     -> near miss, extreme
                               4 two awards, the question names neither             -> near miss, uniqueness
  path B, ranked:              5 batch 1, survives every gate (one annotator fix rejected by the guard)
                               6 batch 1, one document carries the whole chain      -> dropped at the screen
                               7 ranked last, outside the batch of 2                -> never selected
                               8 comparison                                         -> not in the pool
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
S1 = HERE.parent                              # pipelines/cmass/stage1/hotpotqa
PIPE = S1.parent.parent                       # pipelines/cmass
SET = "hotpotqa"
sys.path.insert(0, str(PIPE))


def para(title, *sents):
    return title, list(sents)


# ---------------------------------------------------------------------------------------------- invented sources
# (question, answer, type, [(title, sentences)], [(supporting title, sentence index)], nanoknow, qrels)
ROWS = [
    ("In which town was the painter of The Salt Road at Dusk born?", "Quessly", "bridge",
     [para("The Salt Road at Dusk", "The Salt Road at Dusk is an 1893 oil painting by the Oskerian painter Corvin Aldmere.",
           "It hangs in the Velmaran Gallery."),
      para("Corvin Aldmere", "Corvin Aldmere (1861-1930) was an Oskerian painter born in Quessly, a market town on the Ollen.",
           "He painted the coast near Cape Vardequist."),
      para("Port Ilse", "Port Ilse is the second city of Oskeria.")],
     [("The Salt Road at Dusk", 0), ("Corvin Aldmere", 0)], "supported", ["demo_hotpotqa_001", "demo_hotpotqa_002"]),
    ("In which market town was the sculptor of the Harbour Lions of Port Ilse born?", "Hadley", "bridge",
     [para("Harbour Lions", "The Harbour Lions are a pair of bronze lions on the quay of Port Ilse, cast in 1899 to designs by the sculptor Maren Tolvy."),
      para("Maren Tolvy", "Maren Tolvy (1858-1933) was an Oskerian sculptor.", "She was born in Hadley.")],
     [("Harbour Lions", 0), ("Maren Tolvy", 1)], "supported", ["demo_hotpotqa_003", "demo_hotpotqa_004", "demo_hotpotqa_005"]),
    ("Were Corvin Aldmere and Maren Tolvy both born in Quessly?", "no", "comparison",
     [para("Corvin Aldmere", "Corvin Aldmere (1861-1930) was an Oskerian painter born in Quessly, a market town on the Ollen."),
      para("Maren Tolvy", "Maren Tolvy (1858-1933) was an Oskerian sculptor.", "She was born in Hadley.")],
     [("Corvin Aldmere", 0), ("Maren Tolvy", 1)], "supported", ["demo_hotpotqa_002", "demo_hotpotqa_005"]),
    ("What instrument did the founder of the Hadley School of Music play before he moved to Hadley?", "cello", "bridge",
     [para("Hadley School of Music", "The Hadley School of Music was founded in 1885 by Ansel Maddry."),
      para("Ansel Maddry", "Ansel Maddry was an Oskerian cellist and teacher.")],
     [("Hadley School of Music", 0), ("Ansel Maddry", 0)], "supported", ["demo_hotpotqa_006", "demo_hotpotqa_007"]),
    ("The keeper of the Cape Vardequist lighthouse from 1890 won what award?", "the Gull Medal", "bridge",
     [para("Cape Vardequist Light", "Tomas Hale kept the Cape Vardequist lighthouse from 1890 to 1921."),
      para("Tomas Hale", "Tomas Hale was an Oskerian lighthouse keeper.", "Hale was awarded the Gull Medal in 1902.")],
     [("Cape Vardequist Light", 0), ("Tomas Hale", 1)], "supported", ["demo_hotpotqa_008", "demo_hotpotqa_009"]),
    ("The architect of the Velmaran Tidehouse also designed which bridge over the Ardune?", "the Kestrel Bridge", "bridge",
     [para("Velmaran Tidehouse", "The Velmaran Tidehouse, completed in 1907, was designed by the architect Halvard Senn."),
      para("Halvard Senn", "Halvard Senn (1866-1940) was an Oskerian architect.",
           "Senn also designed the Kestrel Bridge over the Ardune, opened in 1912.")],
     [("Velmaran Tidehouse", 0), ("Halvard Senn", 1)], "unsupported", []),
    ("On which lake lies the village where the poet who wrote Winter Ferries was born?", "Lake Ostra", "bridge",
     [para("Winter Ferries", "Winter Ferries is a 1921 collection of poems by Anwen Lisk."),
      para("Anwen Lisk", "Anwen Lisk was born in Brask, a village on Lake Ostra.")],
     [("Winter Ferries", 0), ("Anwen Lisk", 0)], "unsupported", []),
    ("Which ship did the first harbourmaster of Port Ilse command before 1850?", "the Merrow", "bridge",
     [para("Port Ilse harbour", "The first harbourmaster of Port Ilse was Jory Pellam."),
      para("Jory Pellam", "Pellam commanded the brig Merrow in the Yarrowmere trade.")],
     [("Port Ilse harbour", 0), ("Jory Pellam", 0)], "unsupported", []),
    ("Is the Kestrel Bridge older than the Velmaran Tidehouse?", "no", "comparison",
     [para("Kestrel Bridge", "The Kestrel Bridge over the Ardune opened in 1912."),
      para("Velmaran Tidehouse", "The Velmaran Tidehouse, completed in 1907, was designed by the architect Halvard Senn.")],
     [("Kestrel Bridge", 0), ("Velmaran Tidehouse", 0)], "unsupported", []),
]


def write_sources(src: Path) -> None:
    rows = []
    for i, (q, a, typ, ctx, sup, _, _) in enumerate(ROWS):
        rows.append({"id": f"demo{i:04d}", "question": q, "answer": a, "type": typ, "level": "hard",
                     "supporting_facts": {"title": [t for t, _ in sup], "sent_id": [k for _, k in sup]},
                     "context": {"title": [t for t, _ in ctx], "sentences": [s for _, s in ctx]}})
    (src / "hotpot_dev_distractor.json").write_text(json.dumps(rows, indent=1))
    nk = src / "nanoknow"
    nk.mkdir(parents=True)
    sup = [(i, r) for i, r in enumerate(ROWS) if r[5] == "supported"]
    uns = [(i, r) for i, r in enumerate(ROWS) if r[5] == "unsupported"]
    (nk / "topics.nanoknow-hotpotqa-climbmix.supported.tsv").write_text("".join(f"{i}\t{r[0]}\n" for i, r in sup))
    (nk / "topics.nanoknow-hotpotqa-climbmix.unsupported.tsv").write_text("".join(f"{i}\t{r[0]}\n" for i, r in uns))
    (nk / "qrels.nanoknow-hotpotqa-climbmix.supported.txt").write_text(
        "".join(f"{i} Q0 {d} 1\n" for i, r in sup for d in r[6]))
    (nk / "answers.nanoknow-hotpotqa.jsonl").write_text(
        "".join(json.dumps({"qid": str(i), "answer": [r[1]]}) + "\n" for i, r in enumerate(ROWS)))


# ---------------------------------------------------------------------------------------------- hand-written agent outputs
def cx(qid, leaked=False, leak_doc="", leak_kind="none", leak_evidence="", single=False, single_doc="",
       single_evidence="", hops=2, docs=2, reason=""):
    return {"qid": qid, "leaked": leaked, "leak_doc": leak_doc, "leak_kind": leak_kind, "leak_evidence": leak_evidence,
            "single_doc_answerable": single, "single_doc": single_doc, "single_doc_evidence": single_evidence,
            "hops_needed": hops, "distinct_docs_needed": docs, "clean": not leaked and not single, "reason": reason}


def hop(i, clue, doc="", snippet="", bridge=False, grounded=True, note=""):
    h = {"index": i, "clue": clue, "grounded": grounded, "is_bridge": bridge, "note": note}
    if doc:
        h.update(doc_id=doc, doc_ids=[doc], snippet=snippet)
    return h


def extreme(qid, atoms, hops, uncovered=(), reason="clean pass"):
    ung = [h["index"] for h in hops if not h["grounded"]]
    covered, grounded = not uncovered, not ung
    return {"qid": qid, "all_atoms_covered": covered, "all_hops_grounded": grounded, "keep": covered and grounded,
            "atoms": [{"atom": a, "hop_index": k} for a, k in atoms], "hops": hops,
            "uncovered_atoms": list(uncovered), "ungrounded_hops": ung, "reason": reason}


def uniq(qid, unique=True, gold=True, n=6, rivals=(), corpus_answer="", reason=""):
    return {"qid": qid, "answer_unique": unique, "gold_supported": gold, "keep": unique and gold, "searches_run": n,
            "rivals": list(rivals), "corpus_answer": corpus_answer, "reason": reason}


def coh(qid):
    return {"qid": qid, "cross_hop_consistent": True, "jointly_derives_answer": True, "multi_document": True,
            "keep": True, "failures": [], "new_pattern": "", "reason": "The bridge entity of hop 1 is the subject of hop 2."}


PATH_A_SCREEN = {
    "0": cx("0", reason="Each document carries one hop; neither poses the question."),
    "1": cx("1", leaked=True, leak_doc="demo_hotpotqa_004", leak_kind="poses-question-and-answers-it",
            leak_evidence="Question: In which market town was the sculptor of the Harbour Lions of Port Ilse born? Answer: Hadley.",
            reason="A quiz page poses this question and gives the gold answer."),
    "2": cx("2", reason="One document per person; no document compares them."),
    "3": cx("3", hops=3, reason="The founder and his instrument are in separate documents."),
    "4": cx("4", reason="The keeper and the award are in separate documents."),
}
PATH_A_GATES = {
    "0": {
        "extreme": extreme("0", [("painter", 1), ("The Salt Road at Dusk", 1), ("born", 2), ("which town", 2)], [
            hop(1, "The Salt Road at Dusk was painted by Corvin Aldmere.", "demo_hotpotqa_001",
                "The Salt Road at Dusk, painted in 1893 by Corvin Aldmere", bridge=True),
            hop(2, "Corvin Aldmere was born in Quessly.", "demo_hotpotqa_002",
                "Aldmere was born in 1861 in Quessly, a market town on the Ollen")]),
        "uniqueness": uniq("0", corpus_answer="Quessly", reason="No other birthplace is recorded for the painter."),
        "coherence": coh("0"),
        "annotator": {"qid": "0", "keep": True, "hops": [
            {"index": 1, "verdict": "keep", "clue": "The Salt Road at Dusk was painted by Corvin Aldmere.",
             "doc_id": "demo_hotpotqa_001", "snippet": "The Salt Road at Dusk, painted in 1893 by Corvin Aldmere"},
            {"index": 2, "verdict": "fix", "clue": "The painter Corvin Aldmere was born in the town of Quessly.",
             "doc_id": "demo_hotpotqa_002",
             "snippet": "Corvin Aldmere, painter of the Oskerian coast. Aldmere was born in 1861 in Quessly",
             "problem": "the snippet does not show that Aldmere is the painter", "fix_note": "start at the sentence naming him a painter"}],
            "issues": [], "reason": "Both hops hold; hop 2 now shows the painter by name."},
    },
    "3": {
        "extreme": extreme("3", [("founder", 1), ("Hadley School of Music", 1), ("instrument", 2), ("before he moved to Hadley", 3)], [
            hop(1, "The Hadley School of Music was founded by Ansel Maddry.", "demo_hotpotqa_006",
                "The Hadley School of Music, the town's music school, was founded in 1885 by Ansel Maddry.", bridge=True),
            hop(2, "Ansel Maddry played the cello.", "demo_hotpotqa_007", "Ansel Maddry, cellist, gave his first recital in Velmaran"),
            hop(3, "Ansel Maddry played the cello before he moved to Hadley.", grounded=False,
                note="No document dates his playing relative to a move to Hadley; two rescue searches found none.")],
            reason="Hop 3 is ungrounded: no document states when he moved or that he played before it."),
    },
    "4": {
        "extreme": extreme("4", [("keeper", 1), ("Cape Vardequist lighthouse", 1), ("from 1890", 1), ("won what award", 2)], [
            hop(1, "Tomas Hale kept the Cape Vardequist lighthouse from 1890.", "demo_hotpotqa_008",
                "Tomas Hale kept the light from 1890 to 1921", bridge=True),
            hop(2, "Tomas Hale was awarded the Gull Medal.", "demo_hotpotqa_009",
                "Tomas Hale of Cape Vardequist was awarded the Gull Medal in 1902")]),
        "uniqueness": uniq("4", unique=False, n=7, rivals=[
            {"candidate": "the Order of the Lantern", "why_not_excluded": "The question names no year or kind of award; the same document records both.",
             "doc_id": "demo_hotpotqa_009", "snippet": "and the Order of the Lantern in 1915"}],
            corpus_answer="the Gull Medal (1902) and the Order of the Lantern (1915)",
            reason="Answer multiplicity: the keeper won two awards and the question does not say which is wanted."),
    },
}
PATH_B_SCREEN = {
    "5": cx("5", reason="The architect and the bridge are in separate documents; no quiz page."),
    "6": cx("6", single=True, single_doc="demo_hotpotqa_013",
            single_evidence="Anwen Lisk, author of Winter Ferries (1921), was born in Brask, a village on the shore of Lake Ostra",
            docs=1, reason="One page names the poet, her birthplace and its lake."),
}
PATH_B_GATES = {
    "5": {
        "extreme": extreme("5", [("architect", 1), ("Velmaran Tidehouse", 1), ("also designed", 2), ("which bridge", 2), ("over the Ardune", 2)], [
            hop(1, "The Velmaran Tidehouse was designed by Halvard Senn.", "demo_hotpotqa_010",
                "The Tidehouse on the Ardune quays, completed in 1907, was designed by the architect Halvard Senn", bridge=True),
            hop(2, "Halvard Senn designed the Kestrel Bridge over the Ardune.", "demo_hotpotqa_011",
                "Senn designed the Kestrel Bridge over the Ardune, which opened in 1912")]),
        "uniqueness": uniq("5", n=8, corpus_answer="the Kestrel Bridge", reason="The other Ardune bridge found has no architect named."),
        "coherence": coh("5"),
        "annotator": {"qid": "5", "keep": True, "hops": [
            {"index": 1, "verdict": "fix", "clue": "Halvard Senn was the architect of the Velmaran Tidehouse.",
             "doc_id": "demo_hotpotqa_010", "snippet": "The Tidehouse was built by Halvard Senn in 1907",
             "problem": "the snippet should name the building first", "fix_note": "shorter passage"},
            {"index": 2, "verdict": "keep", "clue": "Halvard Senn designed the Kestrel Bridge over the Ardune.",
             "doc_id": "demo_hotpotqa_011", "snippet": "Senn designed the Kestrel Bridge over the Ardune, which opened in 1912"}],
            "issues": [], "reason": "Both hops hold."},
    },
}


# ---------------------------------------------------------------------------------------------- driver
def sh(*args, cwd=None, expect=0):
    r = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, cwd=cwd)
    out = (r.stdout + r.stderr).strip()
    print(f"$ {' '.join(Path(str(x)).name if i == 0 else str(x) for i, x in enumerate(args))}")
    if out:
        print("  " + out.replace("\n", "\n  "))
    if r.returncode != expect:
        sys.exit(f"demo FAILED: exit {r.returncode} (expected {expect})")
    return r.stdout


def put(run, step, qid, obj):
    p = run / "stage1" / SET / step / f"{qid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1))


def check_all(run, outputs):
    for qid, steps in outputs.items():
        for step, obj in (steps.items() if isinstance(steps, dict) and "qid" not in steps else [("contamination", steps)]):
            put(run, step, qid, obj)
            sh(S1 / "hpqa_state.py", "--run", run, "check", SET, qid, step)


def main():
    run = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE / "run"
    if run.exists():
        shutil.rmtree(run)
    (run / "source").mkdir(parents=True)
    cm = [sys.executable, str(PIPE / "demo" / "demo_cm.py")]
    cfg = {"corpus": {"name": "demo-corpus", "backend": "cli", "cli": {"search": cm + ["search"], "doc": cm + ["doc"]},
                      "min_gap_s": 0.0, "slots": 4},
           "sets": {SET: {"input": f"inputs/{SET}.jsonl", "family": "hotpotqa", "group": "released"},
                    f"nm_{SET}": {"input": f"inputs/{SET}_near_misses.jsonl", "family": "hotpotqa", "group": "near_miss"}}}
    (run / "cmass.json").write_text(json.dumps(cfg, indent=1))
    write_sources(run / "source")
    R = ["--run", run]

    print("== Stage 0: tasks")
    sh(S1 / "stage0_tasks.py", *R, "--nanoknow", run / "source" / "nanoknow", "--hotpot", run / "source" / "hotpot_dev_distractor.json")

    print("\n== Path A: NanoKnow-supported -> screen -> bridge only -> gates")
    sh(S1 / "select_questions.py", *R, "nanoknow", SET)
    check_all(run, PATH_A_SCREEN)                                  # contamination.js would write these
    sh(S1 / "select_questions.py", *R, "stage-gates", SET)
    check_all(run, PATH_A_GATES)                                   # gates.js would write these

    print("\n== Path B: pool -> rank -> batch -> collect -> screen -> gates")
    sh(S1 / "select_questions.py", *R, "pool", SET)
    sh(S1 / "rank_screen.py", *R, "screen", SET, "--ranker", S1 / "stub_ranker.py", "--workers", "1")
    sh(S1 / "rank_screen.py", *R, "order", SET)
    batch = json.loads(sh(S1 / "select_questions.py", *R, "batch", SET, "--size", "2").strip().splitlines()[-1])
    if sorted(batch) != ["5", "6"]:
        sys.exit(f"demo FAILED: batch 1 is {batch}, expected 5 and 6 (the stub ranker's top two)")
    sh(S1 / "collect_docs.py", *R, SET, "--batch", "1")
    inp6 = json.loads((run / "stage1" / SET / "input" / "6.json").read_text())
    if "demo_hotpotqa_013" not in inp6["doc_ids"]:
        sys.exit("demo FAILED: retrieval did not collect the single document of question 6")
    check_all(run, PATH_B_SCREEN)
    sh(S1 / "select_questions.py", *R, "stage-gates", SET)
    check_all(run, PATH_B_GATES)

    print("\n== State and build")
    sh(S1 / "hpqa_state.py", *R, "list", SET)
    summary = json.loads(sh(S1 / "hpqa_state.py", *R, "summary", SET, "--json"))
    sh(S1 / "build_inputs.py", *R, SET)

    print("\n== Stage 2 reads the inputs")
    sh(PIPE / "tools" / "packets.py", SET, cwd=run)
    sh(PIPE / "tools" / "packets.py", f"nm_{SET}", cwd=run)

    # ------------------------------------------------------------------ expectations
    from cmass.run import Run, read_jsonl
    from cmass.stage2 import candidate_problems
    r = Run(run)
    cands, near = r.records(SET), r.records(f"nm_{SET}")
    errs = [f"{x['record_id']}: {e}" for x in cands + near for e in candidate_problems(x, "single")[0]]
    report = json.loads((run / "stage1" / SET / "build_report.json").read_text())
    states = {s["record_id"]: s for s in read_jsonl(run / "stage1" / SET / "selected.jsonl")}
    want = {
        "candidates": [x["record_id"] for x in cands] == ["0", "5"],
        "near misses (uniqueness, then extreme)": [(x["record_id"], x["failed_gate"]) for x in near] == [("4", "uniqueness"), ("3", "extreme")],
        "uniqueness failure kind": near[0].get("failure_kind") == "answer-multiplicity",
        "hop outcomes": report["hop_outcomes"] == {"keep": 2, "fix-applied": 1, "fix-rejected": 1},
        "applied fix changed the clue": cands[0]["hops"][1]["clue"].startswith("The painter"),
        "rejected fix kept the extreme evidence": cands[1]["hops"][0]["snippet"].startswith("The Tidehouse on the Ardune"),
        "snippets verified": report["snippets_verified"] == report["hops"] == 4,
        "selected: 5 NanoKnow + 2 ranked": sorted(states) == ["0", "1", "2", "3", "4", "5", "6"],
        "summary nanoknow": summary.get("nanoknow", {}).get("survivors") == 1 and summary["nanoknow"].get("clean_comparison_excluded") == 1,
        "summary batch 1": summary.get("ranked batch 1", {}).get("survivors") == 1 and summary["ranked batch 1"].get("single_doc") == 1,
        "C1 accepts every record": not errs,
        "packets written": all((run / "packets" / s / f"{q}.md").exists() for s, q in
                               ((SET, "0"), (SET, "5"), (f"nm_{SET}", "3"), (f"nm_{SET}", "4"))),
    }
    bad = [k for k, ok in want.items() if not ok]
    if errs:
        print("C1 errors:", errs)
    if bad:
        sys.exit(f"demo FAILED: {bad}")
    print(f"\ndemo OK: {len(cands)} candidates, {len(near)} near misses in {run / 'inputs'}")


if __name__ == "__main__":
    main()
