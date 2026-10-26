#!/usr/bin/env python3
"""End-to-end test of Stage 3 phase 3, system pooling, on the demo corpus, offline (the agents' verdicts are written
by this file, as make_stage3_fixtures.py does for phases 1 and 2):

  python3 pipelines/cmass/demo/test_pool.py [RUN_DIR]      # default: a fresh temporary directory

test_stage3.py builds phases 1 and 2 in the run; then three invented systems' pool (documents already qrels, judged
before, of a question with no rules, matching no rule, and four that match a hop rule) -> pool.py candidates (once
through the corpus service, once from a local JSONL corpus: same result) -> fetch -> chunks (the judges are not told
the systems) -> first judge, second judge (one disagreement), adjudication, all from qrels_state.py --pool -> pool.py
build -> verify --phase phase3_pool -> dups.py --layer phase3_pool_dups (an exact duplicate, a near duplicate settled
by quote, one that lost the quoted sentence and is dropped by the agents) -> closure -> build -> verify --phase
phase3_pool_dups -> pool.py stats (every added qrel credited to its systems, duplicates through their original) ->
release.py, decoded with scripts/deobfuscate.py (an exclusion-only document lands in qrels_exclusion).

The phase-3 documents (demo_doc_201 to demo_doc_208) live only in this file and the run directory, served by a small
wrapper around demo_cm.py, so the shared demo corpus and the other tests are unchanged.

SYNTHETIC CONTENT ONLY: every place, person and document is invented (the island nation of Oskeria).
"""
from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
REPO = PKG.parent.parent
PY = sys.executable
SET = "demo"

_TOWN = ("Visitors cross the old stone bridge at dawn to watch the fishing boats return, then climb to the assembly "
         "hall on the northern hill. In winter the river mist settles over the quays and the bells of the cathedral "
         "carry across the water to the far bank, where the market traders open their stalls before sunrise.")
_SELM = "Hadley market town guide. The Selm passes Hadley and never reaches Velmaran; it joins the Ardune below Port Ilse."
POOL_DOCS = {
    "demo_doc_201": "Velmaran travel notes. The Ardune runs through Velmaran, and its embankments are lined with cafes "
                    "and bookshops. " + _TOWN,
    "demo_doc_202": "Velmaran travel notes. The Ardune runs through Velmaran, and its embankments are lined with cafes "
                    "and bookshops. " + _TOWN + " Trams leave the harbour every ten minutes.",
    "demo_doc_203": "Velmaran travel notes. The river runs through the town, and its embankments are lined with cafes "
                    "and bookshops. " + _TOWN,
    "demo_doc_204": "Velmaran hosts the Ardune regatta every spring, when rowing crews from Hadley and Port Ilse race for "
                    "the Corran Cup on a course marked by painted buoys.",
    "demo_doc_205": _SELM,
    "demo_doc_206": "The Port Ilse fish market opens at dawn every morning except on the feast of the lanterns.",
    "demo_doc_207": "The Velmaran Conservatory was founded in 1874 and moved to the old customs house in 1932.",
    "demo_doc_208": _SELM,                       # an exact copy of demo_doc_205
}
# the system pool: (question, document, {system: (surfaced, opened)})
POOL = [
    ("demo-1", "demo_doc_002", {"sys_a": (True, True)}),                        # already a qrel
    ("demo-1", "demo_doc_150", {"sys_b": (True, False)}),                       # added in phase 2: already a qrel
    ("demo-1", "demo_doc_012", {"sys_b": (True, False)}),                       # judged and dropped in phase 1
    ("demo-1", "demo_doc_201", {"sys_a": (True, False), "sys_b": (True, True)}),
    ("demo-1", "demo_doc_204", {"sys_c": (True, False)}),
    ("demo-1", "demo_doc_205", {"sys_c": (False, True)}),
    ("demo-1", "demo_doc_206", {"sys_a": (True, False)}),                       # matches no rule
    ("demo-3", "demo_doc_201", {"sys_a": (True, False)}),                       # demo-3 has no rules
    ("demo-4", "demo_doc_207", {"sys_b": (True, False)}),
]
RULES = [
    {"rule_id": "demo/demo-1/2/r1", "group": "demo/demo-1/2", "all": [["Ardune"], ["Velmaran"]]},
    {"rule_id": "demo/demo-1/A1/r1", "group": "demo/demo-1/A1", "all": [["Selm"], ["Velmaran"]], "none": ["rises on"]},
    {"rule_id": "demo/demo-4/1/r1", "group": "demo/demo-4/1", "all": [["Velmaran Conservatory"], ["1874"]]},
]


def keep(*hops):
    return {"keep": True, "hops": [{"hop": h, "supports": s, "quote": q} for h, s, q in hops]}


def drop(reason, note):
    return {"keep": False, "reason": reason, "note": note}


FIRST = {
    ("demo-1", "demo_doc_201"): keep(("2", "full", "The Ardune runs through Velmaran")),
    ("demo-1", "demo_doc_204"): drop("mention_only", "Names the Ardune regatta in Velmaran, not that the river runs "
                                                     "through the city; would keep if it stated the river's course."),
    ("demo-1", "demo_doc_205"): keep(("A1", "full", "The Selm passes Hadley and never reaches Velmaran")),
    ("demo-4", "demo_doc_207"): keep(("1", "full", "The Velmaran Conservatory was founded in 1874")),
    ("demo-1", "demo_doc_203"): drop("no_hop_fact", "This copy says only that 'the river' runs through 'the town'; it "
                                                    "lost the sentence naming the Ardune and Velmaran."),
}
SECOND = {("demo-1", "demo_doc_204"): keep(("2", "partial", "Velmaran hosts the Ardune regatta every spring"))}


def run_cmd(args, cwd, ok=True):
    r = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True)
    if ok is not None and (r.returncode == 0) != ok:
        sys.exit(f"FAILED ({'expected success' if ok else 'expected failure'}): {' '.join(map(str, args))}\n"
                 f"{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return r


def last_json(r):
    return json.loads(r.stdout.strip().splitlines()[-1])


def rows(path: Path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1) + "\n")


def verdicts(judged: Path, qid: str, name: str, docs, table):
    write(judged / qid / f"{name}.json", {"qid": qid, "chunk": name, "verdicts": [
        {"doc_id": d, **table.get((qid, d), FIRST[(qid, d)])} for d in docs]})


def drive(run: Path, T, ST, base: Path, second_table):
    """First judge, then second-judge rounds, compare and adjudication from qrels_state, as the workflows do."""
    def states():
        lines = run_cmd(ST + ["--all"], run).stdout.strip().splitlines()
        return {s["qid"]: s for s in map(json.loads, lines[:-1])}, json.loads(lines[-1])

    for cj in sorted((base / "work").glob("*/chunks.json")):
        for c, docs in json.loads(cj.read_text()).items():
            verdicts(base / "judged", cj.parent.name, f"chunk_{c}", docs, {})
            run_cmd(T + ["check", cj.parent.name, c], run)
    log = []
    for _ in range(6):
        st, summ = states()
        due = {x: s["next_round"] for x, s in st.items() if s["next_round"]}
        if not due:
            return st, summ, log
        for x, nr in due.items():
            run_cmd(T + ["second", x, "--round", nr["round"]] + ([nr["flags"]] if nr["flags"] else []), run)
            stem = "second" if nr["round"] == 1 else f"second{nr['round']}"
            m = json.loads((base / "work" / x / f"{stem}.json").read_text())
            for c, docs in m["chunks"].items():
                verdicts(base / "judged", x, f"{stem}_{c}", docs, second_table)
                run_cmd(T + ["second-check", x, c, "--round", nr["round"]], run)
            cmp_ = last_json(run_cmd(T + ["compare", x, "--round", nr["round"]], run))
            log.append((x, nr["round"], cmp_["disagree"]))
            for part, docs in (json.loads((base / "judged" / x / f"{stem}_compare.json").read_text())["parts"]).items():
                verdicts(base / "judged", x, f"adjudicated_{part}", docs, {})      # the adjudicator sides with the first
                run_cmd(T + ["adj-check", x, part], run)
    sys.exit("FAILED: second-judge rounds did not converge")


def main() -> None:
    keep_dir = len(sys.argv) > 1
    run = Path(sys.argv[1]).resolve() if keep_dir else Path(tempfile.mkdtemp(prefix="cmass_pool_")) / "run"
    shutil.rmtree(run, ignore_errors=True)
    run_cmd([PY, HERE / "test_stage3.py", run], REPO)               # phases 1 and 2, kept in `run`
    shutil.rmtree(run.with_name(run.name + "_modes"), ignore_errors=True)
    S3 = PKG / "stage3"
    T = [PY, S3 / "qrels.py", "--set", SET]
    P = [PY, S3 / "pool.py", "--set", SET]
    D = [PY, S3 / "dups.py", "--set", SET, "--layer", "phase3_pool_dups"]
    qd = run / "qrels" / SET

    # ------------------------------------------------------------------ the phase-3 documents, the map, the inputs
    (run / "pool_corpus.json").write_text(json.dumps(POOL_DOCS, indent=1))
    (run / "pool_cm.py").write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        "import demo_cm\n"
        f"demo_cm.DOCS.update(json.load(open({str(run / 'pool_corpus.json')!r})))\n"
        "demo_cm.main()\n")
    cfg = json.loads((run / "cmass.json").read_text())
    cm = [PY, str(run / "pool_cm.py")]
    cfg["corpus"]["cli"] = {"search": cm + ["search"], "doc": cm + ["doc"]}
    sys.path.insert(0, str(HERE))
    fx = importlib.import_module("make_stage3_fixtures")
    fx.CORPUS = {**fx.CORPUS, **POOL_DOCS}
    write(run / "dups" / "corpus_duplicates_pool.json", fx.duplicate_rows())
    cfg["duplicates"] = {"path": "dups/corpus_duplicates_pool.json", "threshold": 0.7, "id_pad": None}
    (run / "cmass.json").write_text(json.dumps(cfg, indent=1) + "\n")
    pool_rows = [{"dataset": "demo", "query_id": x, "doc_id": d,
                  "systems": {s: {"surfaced": su, "opened": op} for s, (su, op) in sy.items()}} for x, d, sy in POOL]
    (run / "pool.jsonl").write_text("".join(json.dumps(r) + "\n" for r in pool_rows))
    (run / "pool_unknown.jsonl").write_text((run / "pool.jsonl").read_text()
                                            + json.dumps({"query_id": "demo-9", "doc_id": "demo_doc_201", "systems": ["sys_a"]}) + "\n")
    (run / "rules.jsonl").write_text("".join(json.dumps(r) + "\n" for r in RULES))

    # ------------------------------------------------------------------ candidates
    run_cmd(P + ["candidates", "--pool", run / "pool_unknown.jsonl", "--rules", run / "rules.jsonl"], run, ok=False)
    c = last_json(run_cmd(P + ["candidates", "--pool", run / "pool.jsonl", "--rules", run / "rules.jsonl",
                              "--matcher", "token"], run))
    assert (c["pool_pairs"], c["already_qrel"], c["judged_before"], c["no_rules"], c["unmatched"], c["candidates"]) == \
        (9, 2, 1, 1, 1, 4), c
    cands = rows(qd / "phase3_pool" / "candidates.jsonl")
    assert [(r["id"], r["doc_id"], r["groups"]) for r in cands] == [
        ("demo-1", "demo_doc_201", ["2"]), ("demo-1", "demo_doc_204", ["2"]), ("demo-1", "demo_doc_205", ["2", "A1"]),
        ("demo-4", "demo_doc_207", ["1"])], cands
    assert cands[0]["systems"] == ["sys_a", "sys_b"] and cands[2]["opened"] and not cands[2]["surfaced"], cands
    # the same candidates from a local JSONL corpus (as for ClimbMix parquet): texts read there, cached for the judges
    local = run / "local_corpus.jsonl"
    local.write_text("".join(json.dumps({"docid": d, "text": t}) + "\n" for d, t in fx.CORPUS.items()))
    before = (qd / "phase3_pool" / "candidates.jsonl").read_text()
    c2 = last_json(run_cmd(P + ["candidates", "--pool", run / "pool.jsonl", "--rules", run / "rules.jsonl",
                               "--corpus", local, "--matcher", "token"], run))
    assert (qd / "phase3_pool" / "candidates.jsonl").read_text() == before and c2["candidates"] == 4, c2

    # ------------------------------------------------------------------ judging
    run_cmd(P + ["fetch"], run)
    ch = last_json(run_cmd(P + ["chunks"], run))
    assert ch["questions"] == ["demo-1", "demo-4"] and ch["workflow_args"]["pool"] is True, ch
    md = (qd / "phase3_pool" / "work" / "demo-1" / "chunk_01_a.md").read_text()
    assert "demo_doc_201" in md and "sys_" not in md and "system" not in md.lower(), md[:600]     # blind to the systems
    show = run_cmd(T + ["--pool", "show", "demo-1"], run).stdout
    assert "phase3 system pool" in show and "3 documents to judge" in show and "sys_" not in show, show
    run_cmd(P + ["build"], run, ok=False)                                     # nothing judged yet: build refuses
    TP, STP = T + ["--pool"], [PY, S3 / "qrels_state.py", "--set", SET, "--pool"]
    st, summ, log = drive(run, TP, STP, qd / "phase3_pool", SECOND)
    assert summ["complete"] == summ["questions"] == 2, summ
    assert ("demo-1", 1, 1) in log, log                                       # demo_doc_204: one disagreement
    assert last_json(run_cmd(P + ["status"], run))["complete"] == 2
    b = last_json(run_cmd(P + ["build"], run))
    assert b["added"] == 3 and b["pairs"] == b["base_pairs"] + 3 and b["added_by_system"] == {"sys_a": 1, "sys_b": 2, "sys_c": 1}, b
    assert last_json(run_cmd(T + ["verify", "--phase", "phase3_pool"], run))["problems"] == 0
    hm = {r["id"]: {h["id"]: h["docs"] for h in r["hops"]} for r in rows(qd / "phase3_pool" / "qrels_hops.jsonl")}
    assert hm["demo-1"]["2"]["demo_doc_201"]["source"] == "system_pool", hm["demo-1"]["2"]
    rej = {(r["id"], r["doc_id"]): r for r in rows(qd / "phase3_pool" / "rejections.jsonl")}
    assert rej[("demo-1", "demo_doc_204")]["phase"] == 3 and rej[("demo-1", "demo_doc_204")]["adjudicated"], rej

    # ------------------------------------------------------------------ duplicates of the additions
    r1 = last_json(run_cmd(D + ["lookup", "--round", "1"], run))
    assert r1["parents"] == 3 and r1["new_exact"] == 1 and r1["new_near"] == 2, r1        # 208; 202 and 203
    run_cmd(D + ["fetch", "--round", "1"], run)
    dc = last_json(run_cmd(D + ["chunks", "--round", "1"], run))
    assert dc["settled_by_quote"] == 1 and dc["questions"] == ["demo-1"], dc                  # 202 by quote, 203 to agents
    assert dc["workflow_args"]["layer"] == "phase3_pool_dups", dc
    TD = T + ["--phase2", "1", "--layer", "phase3_pool_dups"]
    STD = [PY, S3 / "qrels_state.py", "--set", SET, "--phase2", "1", "--layer", "phase3_pool_dups"]
    st, summ, _ = drive(run, TD, STD, qd / "phase3_pool_dups" / "r1", {})
    assert summ["complete"] == summ["questions"], summ
    ap = last_json(run_cmd(D + ["apply", "--round", "1"], run))
    assert ap["exact_added"] == 1 and ap["near_by_quote"] == 1 and ap["near_rejected"] == 1, ap
    assert last_json(run_cmd(D + ["lookup", "--round", "2"], run))["closure"]
    bd = last_json(run_cmd(D + ["build"], run))
    assert bd["layer"] == "phase3_pool_dups" and bd["pairs"] == bd["base_pairs"] + 2, bd
    assert last_json(run_cmd(T + ["verify", "--phase", "phase3_pool_dups"], run))["problems"] == 0
    final = {r["id"]: set(r["qrels"]) for r in rows(qd / "phase3_pool_dups" / "qrels.jsonl")}
    p2 = {r["id"]: set(r["qrels"]) for r in rows(qd / "phase2" / "qrels.jsonl")}
    assert final["demo-1"] - p2["demo-1"] == {"demo_doc_201", "demo_doc_202", "demo_doc_205", "demo_doc_208"}, final
    assert final["demo-4"] - p2["demo-4"] == {"demo_doc_207"} and final["demo-2"] == p2["demo-2"], final

    # ------------------------------------------------------------------ per-system credit
    s = last_json(run_cmd(P + ["stats"], run))
    assert s["added"] == 5 and s["unattributed"] == 0 and s["removed_from_phase2"] == 0, s
    assert s["new_qrels"] == {"sys_a + sys_b": {"pooled_kept": 1, "duplicate": 1, "total": 2},
                              "sys_b": {"pooled_kept": 1, "total": 1},
                              "sys_c": {"pooled_kept": 1, "duplicate": 1, "total": 2}}, s["new_qrels"]
    assert s["funnel"]["sys_c"] == {"pooled": 2, "candidate": 2, "kept": 1}, s["funnel"]

    # ------------------------------------------------------------------ the release format, decoded back
    out = run / "release"
    canary = "DEMO CANARY: invented strings only, for this test"
    rel = last_json(run_cmd([PY, S3 / "release.py", "--set", SET, "--out", out, "--canary", canary], run))
    assert rel["qrels"] == sum(map(len, final.values())), rel
    dec = [json.loads(l) for l in run_cmd([PY, REPO / "scripts" / "deobfuscate.py", out / "qrel_roles.jsonl"],
                                          REPO).stdout.splitlines()]
    assert {(r["query_id"], r["doc_id"]) for r in dec} == {(x, d) for x, ds in final.items() for d in ds}
    role = {(r["query_id"], r["doc_id"]): r["role"] for r in dec}
    assert role[("demo-1", "demo_doc_205")] == role[("demo-1", "demo_doc_208")] == "exclusion", role   # hop A1 only
    assert role[("demo-1", "demo_doc_201")] == "inclusion", role
    # --queries: the canary and the question order of an already released queries file
    sys.path.insert(0, str(S3))
    import release as RL
    order = ["demo-4", "demo-3", "demo-2", "demo-1"]
    (run / "released_queries.jsonl").write_text("".join(
        json.dumps({"id": RL.encode(x, canary), "question": RL.encode("...", canary), "answer": RL.encode("...", canary),
                    "canary": canary}) + "\n" for x in order))
    out2 = run / "release_ordered"
    run_cmd([PY, S3 / "release.py", "--set", SET, "--out", out2, "--queries", run / "released_queries.jsonl"], run)
    got = list(dict.fromkeys(RL.decode(r["query_id"], canary) for r in rows(out2 / "qrels.jsonl")))
    assert got == order, got
    assert sorted((out2 / "qrels.jsonl").read_text().splitlines()) == sorted((out / "qrels.jsonl").read_text().splitlines())
    print(json.dumps({"run": str(run), "pool": {k: c[k] for k in ("pool_pairs", "already_qrel", "judged_before",
                                                                  "no_rules", "unmatched", "candidates")},
                      "phase3_pool_added": b["added"], "duplicates_added": bd["pairs"] - bd["base_pairs"],
                      "new_qrels_by_systems": {k: v["total"] for k, v in s["new_qrels"].items()},
                      "release": {k: rel[k] for k in ("qrels", "inclusion", "exclusion")},
                      "result": "all system-pool demo checks passed"}))
    if not keep_dir:
        shutil.rmtree(run.parent)


if __name__ == "__main__":
    main()
