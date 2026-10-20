#!/usr/bin/env python3
"""End-to-end test of Stage 3 on the demo corpus, offline: every deterministic step of qrels.py, qrels_state.py and
dups.py, with make_stage3_fixtures.py standing in for the agents.

  python3 pipelines/cmass/demo/test_stage3.py [RUN_DIR]      # default: a fresh temporary directory

pool -> std-check -> recall -> fetch -> chunks -> (bad verdicts: check and state must catch them) -> first judge ->
check -> second-judge rounds, compare, adjudication until qrels_state says complete (one question escalates) -> build
-> verify; then dups lookup -> fetch -> chunks -> near-duplicate verification -> apply -> lookup (closure) -> build ->
verify --phase phase2; plus the duplicate-map readers (parquet and JSON) and the id spelling round trip.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
PY = sys.executable
SET = "demo"


def run_cmd(args, cwd, ok=True):
    r = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True)
    if ok is not None and (r.returncode == 0) != ok:
        sys.exit(f"FAILED ({'expected success' if ok else 'expected failure'}): {' '.join(map(str, args))}\n"
                 f"{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return r


def last_json(r):
    return json.loads(r.stdout.strip().splitlines()[-1])


def main() -> None:
    keep = len(sys.argv) > 1          # a run directory named on the command line is kept for inspection
    run = Path(sys.argv[1]).resolve() if keep else Path(tempfile.mkdtemp(prefix="cmass_stage3_"))
    if run.exists():
        shutil.rmtree(run)
    run_cmd([PY, HERE / "make_demo_run.py", run], HERE)
    run_cmd([PY, HERE / "make_stage3_fixtures.py", "inputs", run], HERE)
    T = [PY, PKG / "stage3" / "qrels.py", "--set", SET]
    ST = [PY, PKG / "stage3" / "qrels_state.py", "--set", SET]
    D = [PY, PKG / "stage3" / "dups.py", "--set", SET]
    F = [PY, HERE / "make_stage3_fixtures.py"]

    def states(*extra):
        lines = run_cmd(ST + list(extra) + ["--all"], run).stdout.strip().splitlines()
        return {s["qid"]: s for s in map(json.loads, lines[:-1])}, json.loads(lines[-1])

    def tier_rounds(extra):
        """Drive the second judge, compare and adjudication from qrels_state, as the workflows do."""
        for _ in range(6):
            st, summ = states(*extra)
            due = {x: s["next_round"] for x, s in st.items() if s["next_round"]}
            if not due:
                return st, summ
            for x, nr in due.items():
                run_cmd(T + extra + ["second", x, "--round", nr["round"]] + ([nr["flags"]] if nr["flags"] else []), run)
            for k in sorted({nr["round"] for nr in due.values()}):
                run_cmd(F + ["second", run, "--round", k] + (["--phase2", extra[1]] if extra else []), HERE)
            for x, nr in due.items():
                m = json.loads((qdir(extra) / "work" / x / f"second{'' if nr['round'] == 1 else nr['round']}.json").read_text())
                for c in m["chunks"]:
                    run_cmd(T + extra + ["second-check", x, c, "--round", nr["round"]], run)
                cmp_ = last_json(run_cmd(T + extra + ["compare", x, "--round", nr["round"]], run))
                log.append((x, nr, cmp_["disagree"], cmp_["first_drop_second_keep"]))
            run_cmd(F + ["adjudicate", run] + (["--phase2", extra[1]] if extra else []), HERE)
            for x in due:
                for part in states(*extra)[0][x]["rounds"][str(due[x]["round"])].get("adj_parts", []):
                    run_cmd(T + extra + ["adj-check", x, part], run)
        sys.exit("FAILED: second-judge rounds did not converge")

    def qdir(extra):
        return run / "qrels" / SET / (f"phase2/r{extra[1]}" if extra else "")

    log = []
    # ------------------------------------------------------------------ phase 1
    pool = last_json(run_cmd(T + ["pool"], run))
    assert pool["questions"] == 4 and pool["derivation_hops"] == 1 and pool["out_hops"] == 1, pool
    assert pool["without_stage1_record"] == ["demo-4"], pool
    inv = {json.loads(l)["id"]: json.loads(l) for l in (run / "qrels" / SET / "inventory.jsonl").read_text().splitlines()}
    assert inv["demo-1"]["needs_recall"] == ["A1", "A2"] and inv["demo-3"]["needs_recall"] == [], inv["demo-1"]["needs_recall"]
    assert "seed:2" in inv["demo-1"]["pool"]["demo_doc_011"] and "audit" in inv["demo-1"]["pool"]["demo_doc_011"]
    for x in inv:
        run_cmd(T + ["std-check", x], run)
        run_cmd(T + ["show", x], run)
        if inv[x]["needs_recall"]:
            run_cmd(T + ["recall", x], run)
    run_cmd(T + ["build"], run, ok=False)                           # nothing judged yet: build refuses
    run_cmd(T + ["fetch"], run)
    for x in inv:
        run_cmd(T + ["chunks", x], run)
    st, _ = states()
    assert all(s["blocking"] == "first judge" for s in st.values()), st

    # a bad verdict file: check and state must both catch it
    chunk = json.loads((run / "qrels" / SET / "work" / "demo-4" / "chunks.json").read_text())["01"]
    absent = "a phrase that occurs in no demo document"
    bad = {"qid": "demo-4", "chunk": "01", "verdicts": [
        {"doc_id": chunk[0], "keep": True, "hops": [{"hop": "3", "supports": "full", "quote": absent}]},
        {"doc_id": chunk[1], "keep": False, "reason": "irrelevant", "note": ""}]}
    p = run / "qrels" / SET / "judged" / "demo-4" / "chunk_01.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bad))
    out = run_cmd(T + ["check", "demo-4", "01"], run, ok=False).stdout
    for needle in ("derivation hop 3", "not verbatim", "not one of", "no note", "has no verdict"):
        assert needle in out, (needle, out)
    assert states()[0]["demo-4"]["unjudged"] == ["01"]

    run_cmd(F + ["first", run], HERE)
    for x in inv:
        run_cmd(T + ["check", x], run)
    st, summ = tier_rounds([])
    assert summ["complete"] == 4, summ
    assert ("demo-4", {"round": 2, "flags": "--drops-only"}) in [(x, nr) for x, nr, _, _ in log], log
    assert any(x == "demo-1" and d == 1 for x, _, d, _ in log), log      # the partial-only keep the adjudicator dropped
    run_cmd(T + ["build"], run)
    v = last_json(run_cmd(T + ["verify"], run))
    assert v["problems"] == 0 and v["derivation_hops_exempt"] == 1, v
    p1 = {json.loads(l)["id"]: set(json.loads(l)["qrels"]) for l in (run / "qrels" / SET / "phase1" / "qrels.jsonl").read_text().splitlines()}
    want = {"demo-1": {"demo_doc_001", "demo_doc_002", "demo_doc_003", "demo_doc_004"},
            "demo-2": {"demo_doc_002", "demo_doc_003", "demo_doc_004", "demo_doc_005", "demo_doc_012"},
            "demo-3": {"demo_doc_006", "demo_doc_007", "demo_doc_011", "demo_doc_103"},
            "demo-4": {"demo_doc_007", "demo_doc_008", "demo_doc_103", "demo_doc_105"}}
    assert p1 == want, p1
    trec = (run / "qrels" / SET / "phase1" / "qrels.trec").read_text().splitlines()
    assert len(trec) == sum(map(len, want.values())) and all(len(l.split()) == 4 for l in trec)

    # verify must catch a missing accepted evidence document
    qf = run / "qrels" / SET / "phase1" / "qrels.jsonl"
    good = qf.read_text()
    rows = [json.loads(l) for l in good.splitlines()]
    rows[0]["qrels"].remove("demo_doc_001")
    qf.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = run_cmd(T + ["verify"], run, ok=False).stdout
    assert "accepted evidence demo_doc_001 not in the qrels" in out, out
    qf.write_text(good)

    # ------------------------------------------------------------------ phase 2
    r1 = last_json(run_cmd(D + ["lookup", "--round", "1"], run))
    # demo_doc_104 (a copy of demo_doc_103 without its founding sentence) is new for demo-3; for demo-4 it is new, or
    # judged_dropped when recall already pooled it in phase 1
    assert r1["new_exact"] == 2 and r1["new_near"] + r1["pairs"].get("near_judged_dropped", 0) == 4, r1
    run_cmd(D + ["fetch", "--round", "1"], run)
    ch = last_json(run_cmd(D + ["chunks", "--round", "1"], run))
    assert ch["questions"][:3] == ["demo-1", "demo-2", "demo-3"] and set(ch["questions"]) <= set(want), ch
    md = (run / "qrels" / SET / "phase2" / "r1" / "work" / "demo-1" / "chunk_01_a.md").read_text()
    assert "Near duplicate of demo_doc_002" in md and "verbatim in this document: yes" in md, md[:800]
    run_cmd(D + ["apply", "--round", "1"], run, ok=False)            # not verified yet: apply refuses
    run_cmd(F + ["first", run, "--phase2", "1"], HERE)
    for x in ch["questions"]:
        run_cmd(T + ["--phase2", "1", "check", x], run)
    st, summ = tier_rounds(["--phase2", "1"])
    assert summ["complete"] == len(ch["questions"]), summ
    ap = last_json(run_cmd(D + ["apply", "--round", "1"], run))
    assert ap["exact_added"] == 2 and ap["near_added"] == 2 and ap["near_rejected"] == r1["new_near"] - 2, ap
    r2 = last_json(run_cmd(D + ["lookup", "--round", "2"], run))
    assert r2["closure"] and r2["pairs"].get("exact_already_kept") and r2["pairs"].get("near_already_kept"), r2
    run_cmd(D + ["status"], run)
    b = last_json(run_cmd(D + ["build"], run))
    assert b["closure"] and b["phase2_pairs"] == b["phase1_pairs"] + 4, b
    v2 = last_json(run_cmd(T + ["verify", "--phase", "phase2"], run))
    assert v2["problems"] == 0, v2
    p2 = {json.loads(l)["id"]: set(json.loads(l)["qrels"]) for l in (run / "qrels" / SET / "phase2" / "qrels.jsonl").read_text().splitlines()}
    for x in ("demo-1", "demo-2"):
        assert p2[x] == want[x] | {"demo_doc_101", "demo_doc_102"}, p2[x]
    assert p2["demo-3"] == want["demo-3"] and p2["demo-4"] == want["demo-4"], p2
    hm = {json.loads(l)["id"]: {h["id"]: h["docs"] for h in json.loads(l)["hops"]}
          for l in (run / "qrels" / SET / "phase2" / "qrels_hops.jsonl").read_text().splitlines()}
    assert hm["demo-1"]["2"]["demo_doc_101"]["source"] == "exact_dup" and hm["demo-1"]["2"]["demo_doc_102"]["source"] == "near_dup"

    # ------------------------------------------------------------------ the duplicate-map readers and id spellings
    sys.path.insert(0, str(PKG / "stage3"))
    import dups
    from cmass.run import Run
    r = Run(run)
    docs = [f"demo_doc_{i:03d}" for i in range(1, 15)] + [f"demo_doc_{i}" for i in range(101, 107)]
    readers = {}
    for path in ("dups/corpus_duplicates.parquet", "dups/corpus_duplicates.json"):
        if path.endswith(".parquet") and not (run / path).exists():
            continue
        r.config.data["duplicates"] = {"path": path, "id_pad": None}
        readers[path] = dups.DupMap(r).rows(docs)
    assert len({json.dumps(v, sort_keys=True) for v in readers.values()}) == 1, readers
    r.config.data["duplicates"] = {"path": "dups/corpus_duplicates.json", "id_pad": 5}
    dm = dups.DupMap(r)
    assert dm.to_map("shard_99999_42") == "shard_99999_00042" and dm.to_corpus("shard_99999_00042") == "shard_99999_42"
    assert dm.to_map("demo_doc_002") == "demo_doc_002"
    print(json.dumps({"run": str(run), "phase1_pairs": b["phase1_pairs"], "phase2_pairs": b["phase2_pairs"],
                      "rounds": [(x, nr["round"], nr["flags"], d, f) for x, nr, d, f in log],
                      "map_readers": sorted(readers), "result": "all Stage 3 demo checks passed"}))
    if not keep:
        shutil.rmtree(run)


if __name__ == "__main__":
    main()
