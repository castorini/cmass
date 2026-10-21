#!/usr/bin/env python3
"""Offline demo of the TRQA Stage 0 + Stage 1 port: builds stage1/trqa/demo/run/ (gitignored) and runs every
deterministic step on hand-written agent outputs, ending with Stage 2 inputs that tools/packets.py reads.

  python3 pipelines/cmass/stage1/trqa/demo/make_demo.py

SYNTHETIC CONTENT ONLY: every place, question, answer, document and Wikipedia page here is invented (the
island nation of Oskeria; documents demo_trqa_NNN in demo/corpus.json, which demo/demo_cm.py serves). The raw
"TRQA" files are invented rows, encrypted the way TRQA encrypts its fields but with an invented demo canary,
so Stage 0's decryption runs for real. The agent outputs (projector, strict audit, four gates) are written
by hand to cover every path: SUPPORTED / PARTIAL / NOT, a strict-audit drop, the manual backfill, a drop at
each gate, an annotator fix that is applied and one that the guard rejects, and the expansion's three
batches (pilot, recall batch, trivial). Never put a real benchmark item here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
S1 = HERE.parent
PIPE = S1.parents[1]
TOOLS = PIPE / "tools"
DEMO_CM = PIPE / "demo" / "demo_cm.py"
RUN = HERE / "run"
SET = "trqa"
DEMO_CANARY = "trqa:oskeria-demo-canary-not-a-real-one"

A, B, C = "wiki1__demo_1", "wiki1__demo_2", "wiki1__demo_3"
D, E, F, G = "wiki2__demo_4_pdemo", "wiki2__demo_5_pdemo", "wiki2__demo_6_pdemo", "wiki2__demo_7_pdemo"
H, J, K = "wiki2__demoquest_8_p105", "wiki2__demoquest_10_p577", "wiki2__demoquest_11_p105"
I = "ecommerce__demo_9"

# ------------------------------------------------------------------ the invented "TRQA" test queries
QUERIES = {
    "wiki1": [
        ("demo_1", "How many provinces of Oskeria have a coastline on the Gulf of Amberly?", "3"),
        ("demo_2", "What is the largest area, in square kilometres, of an Oskerian province with a coastline on the Gulf of Amberly?", "2310"),
        ("demo_3", "How many lighthouses on the coast of Oskeria were first lit before 1920?", "3"),
    ],
    "wiki2": [
        ("demo_4_pdemo", "What is the total height in metres of the working lighthouses of Oskeria?", "85"),
        ("demo_5_pdemo", "What is the combined length in kilometres of the rivers that rise in the Morrowfen hills?", "77"),
        ("demo_6_pdemo", "How many of the bridges over the Ardune in Velmaran were rebuilt after the flood of 1911?", "2"),
        ("demo_7_pdemo", "How many rivers of Oskeria are longer than twenty kilometres?", "3"),
        ("demoquest_8_p105", "How many bird species breed in the Brannoch marshes?", "4"),
        ("demoquest_10_p577", "What is the earliest publication year among Oskerian novels published in 1904?", "1904"),
        ("demoquest_11_p105", "How many owl species are endemic to Oskeria?", "2"),
    ],
    "ecommerce": [
        ("demo_9", "What is the average price in dollars of the 1.5-litre kettles sold by the Velmaran Kettle Company?", "42.5"),
    ],
}
# TRQA's qrels: each question's candidate pool, as Wikipedia page ids with a chunk suffix (<pageid>-<n>)
BIRDS = ["Brannoch heron", "Amberly egret", "Marsh bittern", "Oskerian reed warbler", "Varrow kingfisher", "Grey crake",
         "Dune plover", "Moor harrier", "Sedge bunting", "Black-tailed rail", "Morrowfen teal", "Lesser marsh tern", "Selm grebe"]
PAGES = {  # pageid: (question id, Wikipedia title)
    **{"99001%02d" % i: ("demo_7_pdemo", t) for i, t in enumerate(["Ardune", "Selm (river)", "Varrow", "Kell (river)"], 1)},
    **{"99002%02d" % i: ("demoquest_8_p105", t) for i, t in enumerate(BIRDS, 1)},
    **{"99003%02d" % i: ("demoquest_10_p577", t) for i, t in enumerate(["The Lantern Keeper", "Salt over Velmaran", "A Winter at Hadley"], 1)},
    **{"99004%02d" % i: ("demoquest_11_p105", t) for i, t in enumerate(["Vardequist scops owl", "Morrowfen wood owl"], 1)},
}


def enc(text: str) -> str:
    key = hashlib.sha256(DEMO_CANARY.encode()).digest()
    b = text.encode()
    return base64.b64encode(bytes(b[i] ^ key[i % 32] for i in range(len(b)))).decode()


# ------------------------------------------------------------------ hand-written agent outputs
def proj(qid, verdict, op, attr, setdesc, n_exp, n_rec, computed, gold, matches, complete, evidence, rationale):
    return {"record_id": qid, "subset": qid.split("__")[0], "verdict": verdict, "operator": op, "target_attribute": attr,
            "entity_set_description": setdesc, "n_entities_expected": n_exp, "n_entities_recovered": n_rec,
            "computed_value": computed, "gold_answer": gold, "matches_gold": matches, "recall_complete": complete,
            "evidence": [{"entity": e, "attribute_value": v, "doc_id": d} for e, v, d in evidence], "rationale": rationale}


COASTAL = [("Velmar", "demo_trqa_001"), ("Brannoch", "demo_trqa_001"), ("Vardequist", "demo_trqa_001")]
PROJECT = {
    A: proj(A, "SUPPORTED", "count", "number of provinces", "provinces of Oskeria with a coastline on the Gulf of Amberly",
            3, 3, "3", "3", True, True, [(f"{p} province", "coastal", d) for p, d in COASTAL],
            "The province list names five provinces, three of them coastal."),
    B: proj(B, "SUPPORTED", "max", "area in square kilometres", "coastal provinces of Oskeria", 3, 3, "2310", "2310", True, True,
            [("Velmar province", "1240", "demo_trqa_002"), ("Brannoch province", "2310", "demo_trqa_003"),
             ("Vardequist province", "860", "demo_trqa_004")], "Areas of the three coastal provinces; the largest is Brannoch."),
    C: proj(C, "SUPPORTED", "count", "year first lit", "lighthouses on the Oskerian coast first lit before 1920", 3, 3, "3", "3",
            True, True, [("Cape Vardequist lighthouse", "1868", "demo_trqa_007"), ("Port Ilse light", "1902", "demo_trqa_008"),
                         ("Hadley Point lighthouse", "1911", "demo_trqa_009")], "Three of the four working lights predate 1920."),
    D: proj(D, "SUPPORTED", "sum", "height in metres", "working lighthouses of Oskeria", 4, 4, "85", "85", True, True,
            [("Cape Vardequist lighthouse", "31", "demo_trqa_007"), ("Port Ilse light", "24", "demo_trqa_008"),
             ("Hadley Point lighthouse", "12", "demo_trqa_009"), ("Selm estuary light", "18", "demo_trqa_010")],
            "Heights of the four working lights; Hadley Point converted from feet."),
    E: proj(E, "SUPPORTED", "sum", "length in kilometres", "rivers rising in the Morrowfen hills", 2, 2, "77", "77", True, True,
            [("Ardune", "46", "demo_trqa_012"), ("Varrow", "31", "demo_trqa_013")], "Two rivers rise in the Morrowfen hills."),
    F: proj(F, "SUPPORTED", "count", "rebuilt after the 1911 flood", "bridges over the Ardune in Velmaran rebuilt after the flood",
            2, 2, "2", "2", True, True, [("Mill Bridge", "rebuilt", "demo_trqa_016"), ("Quay Bridge", "rebuilt", "demo_trqa_017")],
            "Two of the three Velmaran bridges were rebuilt."),
    G: proj(G, "SUPPORTED", "count", "length over twenty kilometres", "rivers of Oskeria longer than twenty kilometres", 3, 3, "3",
            "3", True, True, [("Ardune", "over 20 km", "demo_doc_005"), ("Selm", "over 20 km", "demo_doc_005"),
                              ("Varrow", "over 20 km", "demo_doc_005")], "A rivers list names three."),
    H: proj(H, "PARTIAL", "count", "breeding species", "bird species breeding in the Brannoch marshes", 4, 2, "2", "4", False, False,
            [("Brannoch heron", "breeds", "demo_trqa_019"), ("Amberly egret", "breeds", "demo_trqa_019")],
            "Only two breeding species found."),
    I: proj(I, "NOT", "average", "price in dollars", "1.5-litre kettles of the Velmaran Kettle Company", 0, 0, "", "42.5", False,
            False, [], "The product catalogue is not in the corpus."),
    J: proj(J, "NOT", "min", "publication year", "Oskerian novels published in 1904", 3, 0, "", "1904", False, False, [],
            "No novel found."),
    K: proj(K, "NOT", "count", "endemic owl species", "owl species endemic to Oskeria", 2, 1, "1", "2", False, False,
            [("Vardequist scops owl", "endemic", "demo_trqa_020")], "Only one owl is stated to be endemic."),
}


def shop(clue, docs, snippet):        # a strict-audit hop
    return {"clue": clue, "grounded": True, "doc_ids": docs, "snippet": snippet}


def strict(qid, hops, keep=True, complete=True, note="No further member found in 4 searches.", failures=()):
    return {"stem": qid, "constraints_covered": True, "all_grounded": True, "aggregate_ok": True, "complete": complete,
            "keep": keep, "hops": hops, "failures": list(failures), "completeness_note": note,
            "reason": "all four checks pass" if keep else "; ".join(failures)}


MEMBER = [shop(f"{p} is a province of Oskeria with a coastline on the Gulf of Amberly.", [d],
               f"{p}, ... coastline on the Gulf of Amberly | provinces list ({d})") for p, d in COASTAL]
STRICT = {
    A: strict(A, MEMBER),
    B: strict(B, MEMBER + [shop("Velmar province covers 1,240 square kilometres.", ["demo_trqa_002"], "covers 1,240 square kilometres"),
                           shop("Brannoch province covers 2,310 square kilometres.", ["demo_trqa_003"], "covers 2,310 square kilometres"),
                           shop("Vardequist province covers 860 square kilometres.", ["demo_trqa_004"], "covers 860 square kilometres"),
                           shop("max(1240, 2310, 860) = 2310", [], "")]),
    C: strict(C, [shop(f"The {n} is on the Oskerian coast and was first lit in {y}.", ["demo_trqa_006", d], f"{n} | first lit in {y}")
                  for n, y, d in [("Cape Vardequist lighthouse", 1868, "demo_trqa_007"), ("Port Ilse light", 1902, "demo_trqa_008"),
                                  ("Hadley Point lighthouse", 1911, "demo_trqa_009")]]),
    D: strict(D, [shop(f"The {n} is {h} metres tall.", ["demo_trqa_006", d], s) for n, h, d, s in [
        ("Cape Vardequist lighthouse", 31, "demo_trqa_007", "stands 31 metres tall"),
        ("Port Ilse light", 24, "demo_trqa_008", "24 metres high"),
        ("Hadley Point lighthouse", 12, "demo_trqa_009", "40 feet high (about 12 metres)"),
        ("Selm estuary light", 18, "demo_trqa_010", "rises 18 metres")]]),
    # the audit agent wrote this question's hops in another shape (no clue): the manual backfill case
    E: strict(E, [{"entity": "Ardune", "doc_id": "demo_trqa_012", "type": "length", "grounded": True, "note": "46 km",
                   "snippet": "46 kilometres from its source in the Morrowfen hills"},
                  {"entity": "Varrow", "doc_id": "demo_trqa_013", "type": "length", "grounded": True, "note": "31 km",
                   "snippet": "the shorter of the two rivers that rise in the Morrowfen hills"}]),
    F: strict(F, [shop("The Mill Bridge crosses the Ardune in Velmaran.", ["demo_trqa_015"], "the Mill Bridge"),
                  shop("The Quay Bridge crosses the Ardune in Velmaran.", ["demo_trqa_015"], "the Quay Bridge"),
                  shop("The Mill Bridge was rebuilt after the flood of 1911.", ["demo_trqa_016"], "rebuilt in stone after the flood of 1911"),
                  shop("The Quay Bridge was rebuilt after the flood of 1911.", ["demo_trqa_017"], "repaired after the flood")]),
    G: strict(G, [shop(f"The {r} is longer than twenty kilometres.", ["demo_doc_005"], "three rivers longer than twenty kilometres")
                  for r in ("Ardune", "Selm", "Varrow")], keep=False, complete=False,
              note="The Kell (demo_trqa_014), 24 kilometres long, is a fourth river longer than twenty kilometres.",
              failures=["incomplete entity set: a fourth qualifying river is attested"]),
}
BACKFILL_E = {"hops": [
    {"clue": "The Ardune, which rises in the Morrowfen hills, is 46 kilometres long.", "kind": "evidence",
     "supporting_doc_ids": ["demo_trqa_012"], "value": "46"},
    {"clue": "The Varrow, which rises in the Morrowfen hills, is 31 kilometres long.", "kind": "evidence",
     "supporting_doc_ids": ["demo_trqa_013"], "value": "31"}]}


def xhop(i, clue, doc, snippet, kind="evidence", grounded=True, note=""):
    return {"index": i, "kind": kind, "grounded": grounded, "doc_id": doc, "doc_ids": [doc] if doc else [],
            "snippet": snippet, "clue": clue, "note": note}


def extreme(qid, hops, atoms, keep=True, ungrounded=(), reason="clean pass"):
    return {"qid": qid, "all_atoms_covered": True, "all_hops_grounded": not ungrounded, "keep": keep and not ungrounded,
            "atoms": [{"atom": a, "hop_index": n} for a, n in atoms], "hops": hops, "uncovered_atoms": [],
            "ungrounded_hops": list(ungrounded), "miskinded_hops": [], "reason": reason}


COAST_SNIP = "Velmar, Brannoch and Vardequist have a coastline on the Gulf of Amberly"
XMEMBER = [xhop(i, f"{p} is a province of Oskeria with a coastline on the Gulf of Amberly.", "demo_trqa_001", COAST_SNIP)
           for i, (p, _) in enumerate(COASTAL, 1)]
EXTREME = {
    A: extreme(A, XMEMBER, [("provinces of Oskeria", 1), ("coastline on the Gulf of Amberly", 1)]),
    B: extreme(B, XMEMBER + [
        xhop(4, "Velmar province covers 1,240 square kilometres.", "demo_trqa_002",
             "Velmar province, which contains the capital Velmaran, covers 1,240 square kilometres"),
        xhop(5, "Brannoch province covers 2,310 square kilometres.", "demo_trqa_003", "Brannoch province covers 2,310 square kilometres"),
        xhop(6, "Vardequist province covers 860 square kilometres.", "demo_trqa_004",
             "Vardequist province, at the island's western tip, covers 860 square kilometres"),
        xhop(7, "max(1240, 2310, 860) = 2310", "", "max(1240, 2310, 860) = 2310", kind="derivation")],
        [("province of Oskeria", 1), ("coastline on the Gulf of Amberly", 1), ("area", 4), ("square kilometres", 4)]),
    C: extreme(C, [xhop(i, f"The {n} was first lit in {y}.", d, f"The {n} was first lit in {y}")
                   for i, (n, y, d) in enumerate([("Cape Vardequist lighthouse", 1868, "demo_trqa_007"),
                                                  ("Port Ilse light", 1902, "demo_trqa_008")], 1)]
               + [xhop(3, "Hadley Point lighthouse was first lit in 1911.", "demo_trqa_009", "Hadley Point lighthouse, first lit in 1911")],
               [("lighthouses on the coast of Oskeria", 1), ("first lit before 1920", 1)]),
    D: extreme(D, [xhop(1, "The Cape Vardequist lighthouse is 31 metres tall.", "demo_trqa_007", "Its stone tower stands 31 metres tall"),
                   xhop(2, "The Port Ilse light is 24 metres high.", "demo_trqa_008", "its iron tower is 24 metres high"),
                   xhop(3, "Hadley Point lighthouse is 40 feet high.", "demo_trqa_009", "is a squat tower 40 feet high"),
                   xhop(4, "The Selm estuary light is 18 metres high.", "demo_trqa_010", "rises 18 metres above the dunes")],
               [("working lighthouses of Oskeria", 1), ("height", 1), ("metres", 1)]),
    E: extreme(E, [xhop(1, "The Ardune is 46 kilometres long.", "demo_trqa_012", "46 kilometres from its source in the Morrowfen hills"),
                   xhop(2, "The Varrow is 31 kilometres long.", "", "", grounded=False,
                        note="demo_trqa_013 calls the Varrow the shorter river but gives no length; 4 rescue searches found none")],
               [("rivers that rise in the Morrowfen hills", 1), ("length in kilometres", 1)], ungrounded=[2],
               reason="Hop 2 is ungrounded: no document states the Varrow's length."),
    F: extreme(F, [xhop(1, "The Mill Bridge crosses the Ardune in Velmaran.", "demo_trqa_015",
                        "Three bridges cross the Ardune in Velmaran: the Assembly Bridge, the Mill Bridge and the Quay Bridge"),
                   xhop(2, "The Quay Bridge crosses the Ardune in Velmaran.", "demo_trqa_015",
                        "Three bridges cross the Ardune in Velmaran: the Assembly Bridge, the Mill Bridge and the Quay Bridge"),
                   xhop(3, "The Mill Bridge was rebuilt after the flood of 1911.", "demo_trqa_016",
                        "The Mill Bridge in Velmaran was rebuilt in stone after the flood of 1911"),
                   xhop(4, "The Quay Bridge was rebuilt after the flood of 1911.", "demo_trqa_017", "it was repaired after the flood")],
               [("bridges over the Ardune in Velmaran", 1), ("rebuilt after the flood of 1911", 3)]),
}


def complete(qid, n, recomputed, extras=()):
    ok = not extras
    return {"qid": qid, "set_complete": ok, "aggregate_unchanged": ok, "keep": ok, "searches_run": n,
            "extra_members": list(extras), "recomputed_answer": recomputed,
            "reason": "no verified extra member" if ok else "a verified extra member changes the count"}


COMPLETENESS = {
    A: complete(A, 9, "3"), B: complete(B, 10, "2310"), D: complete(D, 9, "85"), F: complete(F, 8, "2"),
    C: complete(C, 11, "4", [{"entity": "Morrow Rock lighthouse", "why_qualifies": "on the Oskerian coast, first lit in 1879",
                              "doc_id": "demo_trqa_011", "snippet": "Morrow Rock lighthouse was first lit in 1879",
                              "attribute_value": "1879", "changes_answer": True}]),
}


def coherent(qid, value, failures=()):
    ok = not failures
    return {"qid": qid, "cross_hop_consistent": ok, "aggregate_matches_gold": ok, "jointly_justifies": True,
            "attribution_ok": True, "keep": ok, "recomputed": value, "gold": PROJECT[qid]["gold_answer"],
            "failures": list(failures), "new_pattern": "", "reason": "coherent" if ok else failures[0]["detail"]}


COHERENCE = {
    A: coherent(A, "3"), B: coherent(B, "2310"), F: coherent(F, "2"),
    D: coherent(D, "113", [{"pattern": "unit-mismatch", "hops": [3],
                            "detail": "Hadley Point's height is stated in feet, the other three in metres."}]),
}


def ahop(i, verdict="keep", **kw):
    return {"index": i, "verdict": verdict, **kw}


ANNOTATOR = {
    # hop 1: a better passage in the verified document -> fix (build_final.py applies it)
    A: {"qid": A, "keep": True, "issues": [], "reason": "hop 1 now shows that Velmar is a province",
        "hops": [ahop(1, "fix", clue="Velmar is one of the five provinces of Oskeria and has a coastline on the Gulf of Amberly.",
                      doc_id="demo_trqa_001", problem="the snippet does not show that Velmar is a province",
                      fix_note="quote the sentence listing the provinces too",
                      snippet="Oskeria is divided into five provinces: Velmar, Ardunia, Corran, Brannoch and Vardequist. "
                              "Velmar, Brannoch and Vardequist have a coastline on the Gulf of Amberly"),
                 ahop(2), ahop(3)]},
    # hop 5: a "fix" citing a document the extreme gate did not verify for the hop -> build_final.py rejects it
    B: {"qid": B, "keep": True, "issues": [], "reason": "one proposed fix",
        "hops": [ahop(1), ahop(2), ahop(3), ahop(4),
                 ahop(5, "fix", doc_id="demo_trqa_005", snippet="Ardunia province covers 3,050 square kilometres",
                      problem="(a wrong proposal, kept to show the guard)", fix_note=""),
                 ahop(6), ahop(7)]},
    F: {"qid": F, "keep": False, "issues": [{"kind": "snippet supports a neighbouring fact", "detail": "repaired is not rebuilt",
                                             "hops": [4]}],
        "reason": "hop 4: no passage establishes that the Quay Bridge was rebuilt after the 1911 flood",
        "hops": [ahop(1), ahop(2), ahop(3), ahop(4, "drop", problem="repaired after an unnamed flood")]},
}

LEADS = {p: {"title": t, "lead": f"{t} is an invented subject of the Oskeria demo world."} for p, (q, t) in PAGES.items()}


# ------------------------------------------------------------------ helpers
def sh(*cmd, fail_ok=False) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("TRQA_CANARY", "CMASS_RUN", "CMASS_CONFIG")}
    r = subprocess.run([str(c) for c in cmd], cwd=RUN, env=env, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    print(f"$ {' '.join(Path(str(c)).name if str(c).startswith('/') else str(c) for c in cmd)}")
    print("  " + out.replace("\n", "\n  ") if out else "  (no output)")
    if r.returncode and not fail_ok:
        raise SystemExit(f"FAILED ({r.returncode})")
    return out


def put(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1))


def outputs(step: str, rows: dict) -> None:
    for qid, obj in rows.items():
        put(RUN / "stage1" / SET / step / f"{qid}.json", obj)


def jl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def main():
    if RUN.exists():
        shutil.rmtree(RUN)
    RUN.mkdir(parents=True)
    py = sys.executable
    put(RUN / "cmass.json", {
        "corpus": {"name": "demo-corpus", "backend": "cli", "min_gap_s": 0.0, "slots": 4,
                   "cli": {"search": [py, str(DEMO_CM), "search"], "doc": [py, str(DEMO_CM), "doc"]}},
        "sets": {SET: {"input": f"inputs/{SET}.jsonl", "family": "trqa", "group": "released"},
                 f"nm_{SET}": {"input": f"inputs/{SET}_near_misses.jsonl", "family": "trqa", "group": "near_miss"},
                 f"xp_{SET}": {"input": f"inputs/xp_{SET}.jsonl", "family": "trqa", "group": "near_miss"}}})
    raw = RUN / "source" / "trqa_raw"
    raw.mkdir(parents=True)
    for sub, rows in QUERIES.items():
        (raw / f"test_queries_{sub}.jsonl").write_text("".join(
            json.dumps({"id": i, "question": enc(q), "answer": enc(a), "canary": DEMO_CANARY}) + "\n" for i, q, a in rows))
        qrels = [{"query_id": q, "doc_id": enc(f"{p}-{n}"), "relevance": 1, "iteration": 0, "canary": DEMO_CANARY}
                 for p, (q, _) in PAGES.items() if any(q == i for i, _, _ in rows) for n in range(2 if p.endswith("01") else 1)]
        (raw / f"test_qrels_{sub}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in qrels))

    print("== Stage 0: task construction")
    sh(py, S1 / "stage0_build_tasks.py", "--raw", raw)
    tasks = jl(RUN / "tasks" / f"{SET}.jsonl")
    assert len(tasks) == 11 and {t["record_id"]: t["answer"] for t in tasks}[B] == "2310", "decryption"

    print("\n== Stage 1, projection (hand-written agent outputs) and the answerability check")
    # an agent's corpus command as the guides give it: this step's own search log, centered previews
    sh(py, TOOLS / "cm.py", "search", "--stage", "1", "--step", "project", "--set", SET, "--qid", A,
       "provinces coastline Gulf of Amberly", "--hits", "3", "--chars", "120", "--center")
    assert (RUN / "stage1" / SET / "project" / f"{A}.searchlog.jsonl").exists(), "the step's own search log"
    outputs("project", PROJECT)
    sh(py, S1 / "trqa_state.py", "check", SET, "project", A)
    sh(py, S1 / "collect_verdicts.py")
    sh(py, S1 / "build_strict_inputs.py")
    sh(py, S1 / "trqa_state.py", "list", SET, "--next", "strict")

    print("\n== strict audit (hand-written), the manual backfill, the strict list")
    outputs("strict", STRICT)
    out = sh(py, S1 / "build_strict_list.py", fail_ok=True)
    assert "MANUAL STEP" in out and E in out, "the builder must stop for the backfill"
    put(RUN / "stage1" / SET / "strict_backfill" / f"{E}.json", BACKFILL_E)       # the person's hand-written hops
    sh(py, S1 / "build_strict_list.py")
    sh(py, S1 / "verify_snippets.py")

    print("\n== the four gates (hand-written) and the final list")
    for step, rows in (("extreme", EXTREME), ("completeness", COMPLETENESS), ("coherence", COHERENCE), ("annotator", ANNOTATOR)):
        outputs(step, rows)
    for qid in (A, B, F):
        sh(py, S1 / "trqa_state.py", "check", SET, "annotator", qid)
    sh(py, S1 / "trqa_state.py", "summary", SET)
    sh(py, S1 / "build_final.py")
    sh(py, S1 / "write_stage2_inputs.py")

    print("\n== expansion candidate selection (titles and leads hand-written in place of the Wikipedia API)")
    sh(py, S1 / "expansion" / "universe.py", "--raw", raw)
    X = RUN / "stage1" / SET / "expansion"
    uni = {r["rid"] for r in json.loads((X / "universe.json").read_text())}
    put(X / "titles.json", {p: t for p, (q, t) in PAGES.items() if f"wiki2__{q}" in uni})
    sh(py, S1 / "expansion" / "pool_sweep.py", "sweep")
    sh(py, S1 / "expansion" / "pool_sweep.py", "summary")
    sh(py, S1 / "expansion" / "select_and_build.py", "select")
    put(X / "leads.json", LEADS)
    sh(py, S1 / "expansion" / "select_and_build.py", "build")

    print("\n== Stage 2 reads the inputs")
    for s in (SET, f"nm_{SET}", f"xp_{SET}"):
        sh(py, TOOLS / "packets.py", s)

    # ------------------------------------------------------------------ checks
    S = RUN / "stage1" / SET
    v = Counter(r["verdict"] for r in jl(S / "verdicts.jsonl"))
    assert v == {"SUPPORTED": 7, "PARTIAL": 1, "NOT": 3}, v
    assert [r["record_id"] for r in jl(S / "strict_list.jsonl")] == sorted([A, B, C, D, E, F])
    fin = jl(S / "final_list.jsonl")
    assert [r["record_id"] for r in fin] == [A, B]
    assert json.loads((S / "fix_verification.json").read_text()) == {"fix-applied": 1, "keep": 8, "fix-rejected": 1}
    assert json.loads((S / "final_snippet_failures.json").read_text()) == []
    assert fin[0]["hops"][0]["clue"].startswith("Velmar is one of the five") and fin[1]["hops"][4]["doc_id"] == "demo_trqa_003"
    near = jl(RUN / "inputs" / f"{SET}_near_misses.jsonl")
    assert [(r["record_id"], r["failed_gate"]) for r in near] == [(F, "annotator"), (D, "coherence"), (C, "completeness"), (E, "extreme")]
    assert near[2]["recomputed_answer"] == "4" and near[3]["hops"][0]["value"] == "46"
    xp = jl(RUN / "inputs" / f"xp_{SET}.jsonl")
    assert [(r["record_id"], r["expansion_batch"]) for r in xp] == [(G, "pilot"), (K, "pilot"), (H, "recall_batch"), (J, "trivial")]
    assert all(r["hops"] == [] and r["answer_type"] == "aggregate" for r in xp)
    for f in ("inputs/trqa.jsonl", "inputs/trqa_near_misses.jsonl", "inputs/xp_trqa.jsonl"):
        assert (RUN / f).exists()
    print(f"\nDEMO OK: 11 tasks -> 7 SUPPORTED -> 6 strict-kept -> 5 extreme -> 4 completeness -> 3 coherence -> "
          f"2 annotator; 4 near misses; 4 expansion candidates (pilot 2, recall 1, trivial 1). Run directory: {RUN}")


if __name__ == "__main__":
    main()
