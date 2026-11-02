#!/usr/bin/env python3
"""DSQA Stage 1 demo: build a run directory (default stage1/dsqa/demo/run, gitignored) from twelve invented DSQA-style
tasks over invented documents (demo/corpus.json, the island nation of Oskeria; ids demo_dsqa_NNN), write a
hand-written output for every agent step, run every deterministic step in the order of the real run, and end with
Stage 2 inputs that pipelines/cmass/tools/packets.py reads. No agents, no corpus service: the corpus CLI is
pipelines/cmass/demo/demo_cm.py, which loads this corpus automatically.

  python3 pipelines/cmass/stage1/dsqa/demo/make_demo.py [RUN_DIR]

The twelve tasks cover every path: kept to the end (2), dropped by the consistency gate, the strict audit, the extreme
audit (an ungrounded hop; an uncovered source clause), the coherence audit, the annotator pass, the adjudication,
and the table-quote repair; a polished snippet repaired and one rejected; a consolidation move and a revert; an
annotator fix applied and one rejected; and the expansion selection (two set answers swept, one with a member the
corpus lacks; two single answers).

SYNTHETIC CONTENT ONLY: every place, person, number and document is invented. Never put a real benchmark item here.
"""
import csv
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DSQA = HERE.parent
PIPE = DSQA.parent.parent                      # pipelines/cmass
RUN = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE / "run"
SET = "dsqa"

TASKS = [  # (problem, problem_category, answer, answer_type): DSQA's four columns
    ("Which provinces of Oskeria border the Gulf of Amberly and had more than 40,000 inhabitants according to the "
     "1910 edition of Oskeria in Figures?", "Geography", "Velmar, Brannoch", "Set Answer"),
    ("Which composer founded the conservatory whose concert hall opened on the Ardune quays in 1889?", "Arts",
     "Ilsa Renvaar", "Single Answer"),
    ("According to the Oskerian Fisheries Board, which ports landed more than 2,000 tonnes of herring in 1905?",
     "Finance & Economics", "Port Ilse, Velmaran", "Set Answer"),
    ("Which keepers of the Cape Vardequist lighthouse logged every ship that rounded the cape and served there for more "
     "than twenty years?", "History", "Tomas Hale, Edda Morlan", "Set Answer"),
    ("Which Oskerian river has the largest catchment according to the 1923 river survey?", "Geography", "Selm",
     "Single Answer"),
    ("Which Oskerian provinces had more than 30,000 inhabitants in both the 1900 and the 1910 count?", "Geography",
     "Velmar, Brannoch, Hadley", "Set Answer"),
    ("Which festivals of Velmaran are held on the Ardune quays at midsummer?", "Travel", "Lantern Night, Quay Fair",
     "Set Answer"),
    ("Who directed the Velmaran Conservatory after its founder's death in 1901?", "Education", "Oren Vasse",
     "Single Answer"),
    ("According to the Velmaran Geographical Society, which islands off the Oskerian coast have a lighthouse?",
     "Geography", "Isle of Tarn, Skerry Holm", "Set Answer"),
    ("Which Oskerian composers had an opera staged in Velmaran before 1880?", "Arts", "Ilsa Renvaar, Mirela Osk",
     "Set Answer"),
    ("Which railway stations of Oskeria opened in 1899?", "History",
     "Velmaran Central, Hadley Junction, Port Ilse Harbour, Corran Halt, Brannoch Road, Morrowfen", "Set Answer"),
    ("Which keeper of the Isle of Tarn lighthouse first lit its lamp in 1866?", "History", "Alder Brisk", "Single Answer"),
]
Q = [f"demo_{i:03d}" for i in range(len(TASKS))]
D = {i: f"demo_dsqa_{i:03d}" for i in range(1, 21)}


def hop(clue, docs, snippet, grounded=True):
    return {"clue": clue, "grounded": grounded, "doc_ids": [D[d] for d in docs], "snippet": snippet}


# ---------------------------------------------------------------------------------------------- step 1: projection
H0 = [hop("Velmar borders the Gulf of Amberly.", [2], "Velmar and Brannoch border the Gulf of Amberly."),
      hop("Velmar had more than 40,000 inhabitants in the 1910 edition of Oskeria in Figures.", [1],
          "Velmar, on the Gulf of Amberly, 61,200 inhabitants"),
      hop("The population figures are those of the 1910 edition of Oskeria in Figures.", [1], "Oskeria in Figures, 1910 edition."),
      hop("Velmar is a province of Oskeria.", [1], "Population by province: Velmar")]
H1 = [hop("The Velmaran Conservatory's concert hall opened on the Ardune quays in 1889.", [3],
          "The Velmaran Conservatory opened its concert hall on the Ardune quays in 1889."),
      hop("Ilsa Renvaar founded the Velmaran Conservatory.", [4, 5], "Ilsa Renvaar founded the Velmaran Conservatory in 1874")]
H2 = [hop("The Oskerian Fisheries Board compiled the herring landings table.", [7], "Compiled by the Oskerian Fisheries Board."),
      hop("Port Ilse landed more than 2,000 tonnes of herring in 1905.", [6], "Port Ilse 3,400 tonnes")]
H3 = [hop("Tomas Hale kept the Cape Vardequist lighthouse.", [8], "Tomas Hale kept the Cape Vardequist lighthouse from 1890 to 1921"),
      hop("Tomas Hale logged every ship that rounded the cape.", [8], "logged every ship that rounded the cape"),
      hop("Tomas Hale served there for more than twenty years.", [8], "from 1890 to 1921")]
H4 = [hop("The Selm has the largest catchment of the Oskerian rivers.", [11], "The Selm has the widest catchment of the rivers of Oskeria"),
      hop("The 1923 river survey measured the catchments of the Oskerian rivers.", [10],
          "The Oskerian river survey of 1923 measured each catchment")]
H5 = [hop("Velmar had more than 30,000 inhabitants in 1900 and in 1910.", [12], "Velmar | 1900: 58,400 | 1910: 61,200"),
      hop("Velmar is among the provinces above 30,000 in both counts.", [12],
          "Velmar | Brannoch | Hadley (above 30,000 in 1900 and 1910)")]
H6 = [hop("Lantern Night is held on the Ardune quays at midsummer.", [13], "Lantern Night is held on the Ardune quays of Velmaran at midsummer"),
      {"clue": "Lantern Night is a festival of Velmaran.", "grounded": False, "doc_ids": [], "snippet": ""}]
H7 = [hop("Oren Vasse directed the Velmaran Conservatory after 1901.", [15],
          "Oren Vasse, a pupil of Ilsa Renvaar, taught at the Velmaran Conservatory from 1895.")]
H8 = [hop("The Isle of Tarn is an island off the Oskerian coast with a lighthouse.", [17],
          "The Isle of Tarn, off the Oskerian coast, has had a lighthouse since 1866.")]
H9 = [hop("Ilsa Renvaar had an opera staged in Velmaran before 1880.", [19], "Ilsa Renvaar's first opera was staged in Velmaran in 1868."),
      {"clue": "Ilsa Renvaar is an Oskerian composer.", "grounded": False, "doc_ids": [], "snippet": ""}]
H11 = [hop("Alder Brisk was the first keeper of the Isle of Tarn lighthouse.", [20], "Alder Brisk was the first keeper of the Isle of Tarn light."),
       hop("The lamp was first lit in 1866.", [17], "has had a lighthouse since 1866")]


def proj(i, verdict, hops, derived, grounded=True, match=True, failed=()):
    return {"record_id": Q[i], "answer_type": TASKS[i][3], "all_hops_grounded": grounded, "matches_gold": match,
            "complete": False, "derived_answer": derived, "verdict": verdict, "hops": hops, "failed_clues": list(failed),
            "reason": "Demo projection."}


PROJECT = {
    0: proj(0, "SUPPORTED", H0, "Velmar"), 1: proj(1, "SUPPORTED", H1, "Ilsa Renvaar"),
    2: proj(2, "SUPPORTED", H2, "Port Ilse"), 3: proj(3, "SUPPORTED", H3, "Tomas Hale"),
    4: proj(4, "SUPPORTED", H4, "Selm"), 5: proj(5, "SUPPORTED", H5, "Velmar"),
    6: proj(6, "SUPPORTED", H6, "Lantern Night"),                      # lists a hop it marked ungrounded: the gate drops it
    7: proj(7, "SUPPORTED", H7, "Oren Vasse"), 8: proj(8, "SUPPORTED", H8, "Isle of Tarn"),
    9: proj(9, "PARTIAL", H9, "Ilsa Renvaar", grounded=False, failed=["Ilsa Renvaar is an Oskerian composer."]),
    10: proj(10, "NOT", [], "", grounded=False, match=False, failed=["the opening year of every station"]),
    11: proj(11, "SUPPORTED", H11, "Alder Brisk"),
}


# ---------------------------------------------------------------------------------------------- step 3: strict audit
def strict(i, hops, keep=True, **fails):
    checks = {"constraints_covered": True, "all_grounded": True, "answer_ok": True, "member_valid": True, **fails}
    return {"record_id": Q[i], **checks, "keep": keep and all(checks.values()),
            "hops": [{"clue": h["clue"], "grounded": h["grounded"], "doc_ids": h["doc_ids"], "snippet": h["snippet"],
                      "note": ""} for h in hops],
            "uncovered_constraints": [], "failures": [] if keep else ["hop 1: the document says he taught there, not that he directed it"],
            "reason": "Demo strict audit."}


H0s = [dict(H0[0], doc_ids=[D[2], D[1]])] + H0[1:]           # the auditor found a second document for hop 1
STRICT = {0: strict(0, H0s), 1: strict(1, H1), 2: strict(2, H2), 3: strict(3, H3), 4: strict(4, H4), 5: strict(5, H5),
          7: strict(7, H7, keep=False, member_valid=False), 8: strict(8, H8), 11: strict(11, H11)}


# ---------------------------------------------------------------------------------------------- step 4: extreme audit
def xhop(index, h, required=True, grounded=True):
    return {"index": index, "grounded": grounded, "required": required, "doc_ids": h["doc_ids"] if grounded else [],
            "snippet": h["snippet"] if grounded else "", "note": ""}


def extreme(i, hops, atoms, uncovered=(), ungrounded=()):
    return {"qid": Q[i], "all_atoms_covered": not uncovered, "all_hops_grounded": not ungrounded,
            "keep": not uncovered and not ungrounded, "atoms": atoms, "hops": hops, "uncovered_atoms": list(uncovered),
            "ungrounded_hops": list(ungrounded), "reason": "clean pass" if not uncovered and not ungrounded else "Demo failure."}


A = lambda *pairs: [{"atom": a, "hop_index": n} for a, n in pairs]
EXTREME = {
    0: extreme(0, [xhop(1, H0s[0]), xhop(2, H0[1]), xhop(3, H0[2]), xhop(4, H0[3], required=False)],
               A(("border the Gulf of Amberly", 1), ("more than 40,000 inhabitants", 2), ("the 1910 edition of Oskeria in Figures", 3))),
    1: extreme(1, [xhop(1, H1[0]), xhop(2, H1[1])], A(("concert hall opened on the Ardune quays in 1889", 1), ("founded", 2))),
    2: extreme(2, [xhop(1, H2[0]), xhop(2, H2[1])], A(("According to the Oskerian Fisheries Board", 1), ("more than 2,000 tonnes", 2))),
    3: extreme(3, [xhop(1, H3[0]), xhop(2, H3[1]), xhop(3, H3[2])],
               A(("keepers of the Cape Vardequist lighthouse", 1), ("logged every ship", 2), ("more than twenty years", 3))),
    4: extreme(4, [xhop(1, H4[0]), xhop(2, H4[1])], A(("largest catchment", 1), ("the 1923 river survey", 2))),
    5: extreme(5, [xhop(1, H5[0]), xhop(2, H5[1])], A(("more than 30,000 inhabitants", 1), ("in both the 1900 and the 1910 count", 2))),
    8: extreme(8, [xhop(1, H8[0])], A(("islands off the Oskerian coast", 1), ("have a lighthouse", 1),
                                       ("According to the Velmaran Geographical Society", 0)),
               uncovered=["According to the Velmaran Geographical Society (source attribution)"]),
    11: extreme(11, [xhop(1, H11[0]), xhop(2, H11[1], grounded=False)], A(("first keeper", 1), ("first lit its lamp in 1866", 2)),
                ungrounded=[2]),
}
XCHECK = {0: EXTREME[0], 8: EXTREME[8]}                      # the cross-check sample (chosen by hand)

# ---------------------------------------------------------------------------------------------- step 5: evidence
POLISH = {
    0: [(1, 2, "Velmar and Brannoch border the Gulf of Amberly."),
        (2, 1, "Population by province: Velmar, on the Gulf of Amberly, 61,200 inhabitants."),   # repaired: ';' in the document
        (3, 1, "Oskeria in Figures, 1910 edition.")],
    1: [(1, 3, "The Velmaran Conservatory opened its new concert hall on the Ardune quays in 1889."),   # not verbatim: falls back
        (2, 4, "Ilsa Renvaar founded the Velmaran Conservatory in 1874 and taught composition there.")],
    2: [(1, 7, "Herring landings by port, 1910 edition. Compiled by the Oskerian Fisheries Board."),
        (2, 6, "Port Ilse 3,400 tonnes; Velmaran 2,150 tonnes")],
    3: [(i, 8, "Tomas Hale kept the Cape Vardequist lighthouse from 1890 to 1921 and logged every ship that rounded the cape.")
        for i in (1, 2, 3)],
    4: [(1, 11, "The Selm has the widest catchment of the rivers of Oskeria, its guidebook claims."),
        (2, 10, "The Oskerian river survey of 1923 measured each catchment")],
    5: [(1, 12, "Velmar: 58,400 (1900); 61,200 (1910)"), (2, 12, "Velmar, Brannoch and Hadley exceed 30,000 in both years.")],
}
CONSOLIDATE = {
    0: {"docs_before": 2, "docs_after": 1, "hops": [
        {"index": 1, "doc_id": D[1], "snippet": "Velmar, on the Gulf of Amberly", "changed": True, "why": "the anchor names the coast too"},
        {"index": 2, "doc_id": D[1], "snippet": "Population by province: Velmar, on the Gulf of Amberly, 61,200 inhabitants",
         "changed": False, "why": ""},
        {"index": 3, "doc_id": D[1], "snippet": "Oskeria in Figures, 1910 edition.", "changed": False, "why": ""}]},
    1: {"docs_before": 2, "docs_after": 2, "hops": [
        {"index": 1, "doc_id": D[3], "snippet": "The Velmaran Conservatory opened its concert hall on the Ardune quays in 1889.",
         "changed": False, "why": ""},
        {"index": 2, "doc_id": D[5], "snippet": "Ilsa Renvaar founded the Velmaran Conservatory in 1876.",   # not verbatim: reverts
         "changed": True, "why": "a shorter passage"}]},
    3: {"docs_before": 1, "docs_after": 1, "hops": []},      # not a target: discarded as having no input
}

# ---------------------------------------------------------------------------------------------- steps 6-10
COHERENCE = {i: {"qid": Q[i], "attribution_ok": True, "cross_hop_consistent": True, "jointly_justifies": True, "keep": True,
                 "failures": [], "new_pattern": "", "reason": "The hops agree."} for i in (0, 1, 3, 4, 5)}
COHERENCE[2] = {"qid": Q[2], "attribution_ok": False, "cross_hop_consistent": False, "jointly_justifies": True, "keep": False,
                "failures": [{"pattern": "attribution-not-for-data", "detail": "The Board is credited on the 1910 table; the "
                              "figures used are from the 1905 table, which never names it.", "hops": [1, 2]},
                             {"pattern": "edition-mismatch", "detail": "The two hops read different editions.", "hops": [1, 2]}],
                "new_pattern": "", "reason": "The credit does not cover the data used."}


def ann(index, verdict="keep", **kw):
    return {"index": index, "verdict": verdict, **kw}


ANNOTATOR = {
    0: {"keep": True, "hops": [ann(1, "fix", clue="Velmar borders the Gulf of Amberly (the list of coastal provinces).",
                                   doc_id=D[2], snippet="Velmar and Brannoch border the Gulf of Amberly.",
                                   problem="The shown passage is about population.", fix_note="the coastal list says it outright"),
                               ann(2), ann(3)], "issues": []},
    1: {"keep": True, "hops": [ann(1), ann(2, "fix", doc_id=D[3], snippet="The Velmaran Conservatory: a history in brief.",
                                           fix_note="a heading")],          # rejected: not a verified document of the hop
        "issues": [{"kind": "conflicting-source", "detail": "A city almanac dates the founding to 1876; the cited history gives 1874.",
                    "hops": [2]}]},
    3: {"keep": False, "hops": [ann(1), ann(2), ann(3, "drop", problem="No verified passage states the length of service.")],
        "issues": [{"kind": "uncomputed-derivation", "detail": "The years of service are left to the reader.", "hops": [3]}]},
    4: {"keep": True, "hops": [ann(1), ann(2)],
        "issues": [{"kind": "gold-answer-not-corpus-supported", "detail": "The 1923 survey gives the Ardune the largest catchment.",
                    "hops": [1, 2]}]},
    5: {"keep": True, "hops": [ann(1), ann(2)],
        "issues": [{"kind": "unreadable-table-fragment", "detail": "Both snippets rebuild the table with separators.", "hops": [1, 2]}]},
}
FLAGS = {Q[i]: ANNOTATOR[i]["issues"] for i in (1, 4, 5)}    # the operator's selection of serious flags (manual)
ADJUDICATE = {
    1: {"decision": "keep_with_note", "annotator_note": "Another corpus document, a city almanac, dates the founding to 1876; "
        "the cited passage is the conservatory's own history.", "fatal_flags": [], "survivable_flags": ["conflicting-source"],
        "rationale": "The history and the almanac disagree on the year only; the founder is the same in both."},
    4: {"decision": "drop", "annotator_note": "", "fatal_flags": ["the 1923 survey contradicts the gold answer"],
        "survivable_flags": [], "rationale": "The survey the question names gives the Ardune the largest catchment."},
    5: {"decision": "keep_with_note", "annotator_note": "Read each province's row across: the two columns are the 1900 and 1910 counts.",
        "fatal_flags": [], "survivable_flags": ["unreadable-table-fragment"],
        "rationale": "The figures are in the table; the snippets only need re-quoting."},
}
TABLE_REPAIR = {5: [{"index": 1, "possible": True, "doc_id": D[12], "snippet": "Velmar 58,400 61,200", "note": ""},
                    {"index": 2, "possible": False, "doc_id": D[12], "snippet": "",
                     "note": "Being above the threshold in both counts is derived from the table; no passage states it."}]}
TABLE_DECISIONS = {Q[5]: {"decision": "drop", "reason": "hop 2 has no verbatim passage: the fact is derived from the table"}}


# ---------------------------------------------------------------------------------------------- the run
def sh(*args, check=True, quiet=False):
    r = subprocess.run([sys.executable, *map(str, args)], cwd=RUN, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if not quiet:
        print(f"$ {' '.join(Path(str(a)).name if i == 0 else str(a) for i, a in enumerate(args))}\n  " + out.replace("\n", "\n  "))
    if check and r.returncode:
        raise SystemExit(f"failed: {args}")
    return out


def put(step, i, obj):
    p = RUN / "stage1" / SET / step / f"{Q[i]}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1))


def put_json(name, obj):
    (RUN / "stage1" / SET / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1))


def all_done(step, *qids):
    out = json.loads(sh(DSQA / "dsqa_state.py", step, *qids, "--json", quiet=True))
    bad = [r for r in out["questions"] if r["status"] != "done"]
    if bad:
        raise SystemExit(f"{step}: not done: {bad}")
    print(f"$ dsqa_state.py {step} {' '.join(qids)}\n  {step}: {len(out['questions'])} done")


def counts():
    return json.loads((RUN / "stage1" / SET / "counts.json").read_text())


def expect(got, want, what):
    if got != want:
        raise SystemExit(f"{what}: got {got}, expected {want}")


def main():
    if RUN.exists():
        shutil.rmtree(RUN)
    RUN.mkdir(parents=True)
    cfg = {"corpus": {"name": "demo", "backend": "cli", "min_gap_s": 0, "retries": 2, "lock_dir": str(RUN / "locks"),
                      "cli": {"search": [sys.executable, str(PIPE / "demo" / "demo_cm.py"), "search"],
                              "doc": [sys.executable, str(PIPE / "demo" / "demo_cm.py"), "doc"]}},
           "sets": {SET: {"input": f"inputs/{SET}.jsonl", "family": "dsqa", "group": "released"}}}
    (RUN / "cmass.json").write_text(json.dumps(cfg, indent=1))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["problem", "problem_category", "answer", "answer_type"])
    w.writerows(TASKS)
    (RUN / "source").mkdir()
    (RUN / "source" / "DSQA-demo.csv").write_text(buf.getvalue())

    # Stage 0 (the checksum warning is expected: this is not the real file)
    sh(DSQA / "stage0_tasks.py", "--csv", "source/DSQA-demo.csv", "--prefix", "demo")
    # 1-2 projection and consistency gate
    for i, o in PROJECT.items():
        put("project", i, o)
    all_done("project")
    sh(DSQA / "dsqa_build.py", "gate")
    expect((counts()["gate"]["SUPPORTED"], counts()["gate"]["released"]), (10, 9), "gate")
    # 3 strict audit
    for i, o in STRICT.items():
        put("strict", i, o)
    all_done("strict")
    sh(DSQA / "dsqa_build.py", "audited")
    expect(counts()["audited"]["kept"], 8, "strict audit")
    # 4 extreme audit and its cross-check
    for i, o in EXTREME.items():
        put("extreme", i, o)
    all_done("extreme")
    sh(DSQA / "dsqa_build.py", "extreme")
    expect((counts()["extreme"]["kept"], counts()["extreme"]["confirmatory_hops_omitted"]), (6, 1), "extreme audit")
    for i, o in XCHECK.items():
        put("xcheck", i, o)
    all_done("xcheck", Q[0], Q[8])
    sh(DSQA / "dsqa_build.py", "xcheck")
    # 5 evidence presentation: polish, labels, consolidation
    for i, hs in POLISH.items():
        put("polish", i, {"qid": Q[i], "hops": [{"index": n, "doc_id": D[d], "snippet": s, "why": "states it outright"}
                                               for n, d, s in hs]})
    all_done("polish")
    sh(DSQA / "dsqa_build.py", "polish-verify")
    expect({k: counts()["polish_verify"].get(k) for k in ("ok", "repaired", "failed")}, {"ok": 10, "repaired": 1, "failed": 3}, "polish")
    sh(DSQA / "dsqa_build.py", "labels")
    sh(DSQA / "dsqa_build.py", "consolidate-targets")
    expect(counts()["consolidate_targets"]["targets"], 2, "consolidation targets")
    for i, o in CONSOLIDATE.items():
        put("consolidate", i, {"qid": Q[i], **o})
    all_done("consolidate")
    sh(DSQA / "dsqa_build.py", "consolidate-verify")
    expect({k: counts()["consolidate_verify"].get(k) for k in ("moved", "kept", "reverted", "discarded")},
           {"moved": 1, "kept": 3, "reverted": 1, "discarded": 1}, "consolidation")
    sh(DSQA / "dsqa_build.py", "evidence")
    # 6 coherence
    for i, o in COHERENCE.items():
        put("coherence", i, o)
    all_done("coherence")
    sh(DSQA / "dsqa_build.py", "coherence")
    expect(counts()["coherence"]["kept"], 5, "coherence")
    # 7 annotator pass and its verified fixes
    for i, o in ANNOTATOR.items():
        put("annotator", i, {"qid": Q[i], **o, "reason": "Demo annotator pass."})
    all_done("annotator")
    sh(DSQA / "dsqa_build.py", "apply-fixes")
    expect(counts()["annotator"]["survivor_hop_outcomes"], {"fix-applied": 1, "keep": 7, "fix-rejected": 1}, "annotator")
    # 8 verbatim check, issues; 9 the operator's flags (manual) and the adjudication
    sh(DSQA / "dsqa_build.py", "verbatim")
    sh(DSQA / "dsqa_build.py", "issues")
    put_json("flags.json", FLAGS)
    for i, o in ADJUDICATE.items():
        put("adjudicate", i, {"qid": Q[i], **o})
    all_done("adjudicate")
    # 10 table-quote repair and the operator's decision (manual)
    for i, hs in TABLE_REPAIR.items():
        put("table_repair", i, {"qid": Q[i], "hops": hs})
    all_done("table_repair")
    put_json("table_decisions.json", TABLE_DECISIONS)
    # 11-13 final list, verification, near misses, Stage 2 inputs
    sh(DSQA / "dsqa_build.py", "final")
    expect((counts()["final"]["final"], counts()["final"]["with_annotator_note"]), (2, 1), "final")
    sh(DSQA / "dsqa_build.py", "verify-final")
    sh(DSQA / "dsqa_build.py", "near-misses")
    expect(counts()["near_misses"]["by_gate"], {"adjudication": 2, "annotator": 1, "coherence": 1}, "near misses")
    sh(DSQA / "dsqa_build.py", "inputs")
    # 14 expansion candidate selection (the sweep searches the demo corpus)
    for cmd in ("candidates", "sweep", "summary", "pilot", "records"):
        sh(DSQA / "dsqa_expansion.py", cmd)
    c = counts()
    expect((c["expansion_candidates"]["set_candidates"], c["expansion_candidates"]["single_candidates"],
            c["expansion_summary"]["all_present"], c["expansion_pilot"]["pilot"], c["expansion_records"]["records"]),
           (3, 2, 2, 2, 4), "expansion")
    # the Stage 2 side reads all three input sets
    cfg["sets"].update({f"nm_{SET}": {"input": f"inputs/{SET}_near_misses.jsonl", "family": "dsqa", "group": "near_miss"},
                        f"xp_{SET}": {"input": f"inputs/{SET}_expansion.jsonl", "family": "dsqa", "group": "released"}})
    (RUN / "cmass.json").write_text(json.dumps(cfg, indent=1))
    for s in (SET, f"nm_{SET}", f"xp_{SET}"):
        sh(PIPE / "tools" / "packets.py", s)
    errs = [p.name for p in (RUN / "packets").rglob("*.md") if "- ERROR:" in p.read_text()]
    if errs:
        raise SystemExit(f"Stage 2 input check (C1) errors in {errs}")
    print(f"\nDSQA Stage 1 demo OK: 12 tasks -> 10 SUPPORTED -> 9 released -> 8 audited -> 6 extreme -> 5 coherent -> "
          f"4 annotated -> 2 final + 4 near misses; expansion 4 records. Run directory: {RUN}")


if __name__ == "__main__":
    main()
