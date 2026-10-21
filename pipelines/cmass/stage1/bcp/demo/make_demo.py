#!/usr/bin/env python3
"""Build and check an offline demo run of BrowseComp-Plus Stage 1 (stage1/bcp/demo/run, gitignored).

  python3 pipelines/cmass/stage1/bcp/demo/make_demo.py

It runs every deterministic step of the port, in README order, on hand-written agent outputs and manual
decisions, over the invented documents of demo/corpus.json (served by pipelines/cmass/demo/demo_cm.py):
Stage 0 decrypts an encrypted source file in the dataset's column format, bcp_state.py lists what each agent
step has to do and checks every output, bcp_build.py builds every list, and the run ends with
inputs/bcp.jsonl and inputs/bcp_near_misses.jsonl, which tools/packets.py turns into Stage 2 packets.
Every path of the procedure is exercised: a content-policy skip, PARTIAL and NOT verdicts, the union of the
two scrutiny passes, a coverage failure, a manual review drop, an independent projection kept and one
rejected, a phase A repair accepted, an independent near miss repaired and rejected by the
operator, unfixable phase A and B targets, the strict audit (with a rescue), the
lenient re-judging (survive, fail on coverage, fail on a core fact), the manual trim and a qrels-review cut.

SYNTHETIC CONTENT ONLY: every place, person, document and question is invented (the island nation of
Oskeria). The encryption key below is an invented demo key, not the dataset's canary. Never put a real
benchmark item here.
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent              # stage1/bcp/demo
STAGE1 = HERE.parent                                # stage1/bcp
PIPE = STAGE1.parent.parent                         # pipelines/cmass
RUN = HERE / "run"
SET = "bcp"
DEMO_KEY = "oskeria-demo-key"                       # invented; real runs read the canary from the environment
sys.path.insert(0, str(PIPE))
from cmass.run import Run  # noqa: E402
from cmass.stage2 import candidate_problems  # noqa: E402


def q(n):
    return f"bcp-demo-{n:02d}"


# ------------------------------------------------------------------------------------- invented tasks
TASKS = [
    (q(1), "A cellist born in a market town on the Selm studied under a composer who directed the Velmaran "
           "Conservatory, founded a string quartet in the late 1940s and led it for more than three decades. "
           "What was the quartet called?", "Hadley String Quartet"),
    (q(2), "A sailor won Port Ilse's August regatta three times in a cutter built at a yard on the Varrow estuary; "
           "in one of his winning years the regatta drew a crowd of 12,400. In what year was his cutter built?", "1931"),
    (q(3), "An astronomer who taught mathematics at the island's oldest secondary school founded an observatory and "
           "discovered a comet in 1903. In what year did that comet last reach perihelion?", "1974"),
    (q(4), "A poet born in a ferry port, the son of a ferry captain, published his first collection in 1931 and became "
           "the island's first poet laureate. On which street is the plaque that marks his later home?", "Harbour Street"),
    (q(5), "A painter married a cellist in 1952 and the couple had two daughters and one son. In what year did he "
           "paint the ceiling of a ferry terminal?", "1960"),
    (q(6), "A keeper of the Cape Vardequist lighthouse logged every ship that rounded the cape. What was the first "
           "ship he logged?", "Grey Heron"),
    (q(7), "Which winner of Port Ilse's August regatta later became mayor of Velmaran?", "Joran Ilvas"),
    (q(8), "A yard on the Varrow estuary that closed in 1962 built a cutter in 1931 that won Port Ilse's August "
           "regatta in 1948. What was the cutter's name?", "Grey Heron"),
    (q(9), "Which Oskerian smuggler hid casks under the Port Ilse ferry terminal?", "Hal Merrow"),
    (q(10), "In what year did the ferry line between the capital of Oskeria and the island's second city open?", "1902"),
    (q(11), "Which Oskerian astronomer's comet was first photographed from a balloon in 1903?", "Liesl Varga"),
    (q(12), "Which cellist from Hadley founded a conservatory in the island's second city in 1880?", "Davin Kesh"),
]
# the source benchmark's own pages for each question (hints only; invented "web" documents)
HINTS = {qid: [{"docid": f"web-{i:02d}1", "url": f"https://example.org/oskeria/{i}",
                "text": f"An invented encyclopedia page about the subject of demo question {i}."},
               {"docid": f"web-{i:02d}2", "url": f"https://example.org/oskeria/{i}/more",
                "text": f"A second invented page with background for demo question {i}."}]
         for i, (qid, _, _) in enumerate(TASKS, 1)}

# ------------------------------------------------------------------------------------- hand-written agent outputs
# project: verdict, discriminating hops (clue, [(doc, snippet)]), redundant or unsupported hops (clue, status)
PROJECT = {
    q(1): ("PROJECTABLE", [
        ("Mirela Ostvan was a cellist born in Hadley.", [("demo_bcp_001", "an Oskerian cellist born in Hadley")]),
        ("Hadley is a market town on the Selm.", [("demo_doc_003", "runs through the market town of Hadley")]),
        ("Mirela Ostvan studied under Oren Vasse, who directed the Velmaran Conservatory.",
         [("demo_bcp_001", "studied at the Velmaran Conservatory under Oren Vasse"),
          ("demo_doc_014", "Oren Vasse directed the Velmaran Conservatory until 1930")]),
        ("Mirela Ostvan founded the Hadley String Quartet in 1949.", [("demo_bcp_001", "in 1949 founded the Hadley String Quartet")])],
        [("She led the quartet for thirty-one years.", "redundant")]),
    q(2): ("PROJECTABLE", [
        ("Joran Ilvas won the Amberly Regatta at Port Ilse three times.",
         [("demo_bcp_007", "won the Amberly Regatta three times"), ("demo_bcp_006", "held at Port Ilse every August")]),
        ("He sailed the cutter Grey Heron.", [("demo_bcp_007", "sailing the cutter Grey Heron")]),
        ("The Grey Heron was built at the Brannoch yard in 1931.", [("demo_bcp_008", "built at the Brannoch yard in 1931")]),
        ("The Brannoch yard is on the Varrow estuary.", [("demo_bcp_009", "The Brannoch yard, on the Varrow estuary")])], []),
    q(3): ("PROJECTABLE", [
        ("Liesl Varga taught mathematics at the Velmaran Lyceum.", [("demo_bcp_012", "Liesl Varga taught mathematics at the Velmaran Lyceum")]),
        ("The Velmaran Lyceum is the oldest secondary school on the island.", [("demo_bcp_013", "is the oldest secondary school on the island")]),
        ("Liesl Varga founded the Morrowfen Observatory and discovered Comet Varga in 1903.",
         [("demo_bcp_010", "who discovered the comet that bears her name in 1903")]),
        ("Comet Varga last reached perihelion in 1974.", [("demo_bcp_011", "its last perihelion was in 1974")])], []),
    q(4): ("PROJECTABLE", [
        ("Anselm Drey was born in Port Ilse in 1907.", [("demo_bcp_015", "Anselm Drey was born in Port Ilse in 1907")]),
        ("Anselm Drey was the son of a ferry captain.", [("demo_bcp_015", "the son of a ferry captain")]),
        ("Anselm Drey published his first collection, Salt Lanterns, in 1931.", [("demo_bcp_014", "published his first collection, Salt Lanterns, in 1931")]),
        ("Anselm Drey was the island's first poet laureate.", [("demo_bcp_014", "was the island's first poet laureate")]),
        ("A plaque on Harbour Street in Port Ilse marks the house where he lived.", [("demo_bcp_016", "A plaque on Harbour Street in Port Ilse")])], []),
    q(5): ("PROJECTABLE", [
        ("Teodor Lusk married the cellist Mirela Ostvan in 1952.", [("demo_bcp_003", "married the cellist Mirela Ostvan in 1952")]),
        ("Teodor Lusk and Mirela Ostvan had two daughters and one son.", [("demo_bcp_003", "they had two daughters")]),
        ("Teodor Lusk was a painter.", [("demo_bcp_003", "The painter Teodor Lusk")]),
        ("Teodor Lusk painted the ceiling of the Port Ilse ferry terminal in 1960.", [("demo_bcp_004", "painted the ceiling of the Port Ilse ferry terminal in 1960")])], []),
    q(6): ("PARTIAL", [("Tomas Hale kept the lighthouse at Cape Vardequist.", [("demo_doc_009", "kept the lighthouse at Cape Vardequist")])],
           [("The first ship he logged was the Grey Heron.", "unsupported")]),
    q(7): ("NOT", [("Joran Ilvas won the Amberly Regatta.", [("demo_bcp_007", "won the Amberly Regatta three times")])],
           [("Joran Ilvas became mayor of Velmaran.", "unsupported")]),
    q(8): ("PROJECTABLE", [
        ("The Brannoch yard on the Varrow estuary closed in 1962.", [("demo_bcp_009", "until it closed in 1962")]),
        ("The Brannoch yard built the cutter Grey Heron in 1931.", [("demo_bcp_008", "built at the Brannoch yard in 1931")]),
        ("The Grey Heron won the Amberly Regatta in 1948.", [("demo_bcp_007", "in 1948, 1950 and 1953, sailing the cutter Grey Heron")]),
        ("The Amberly Regatta is held at Port Ilse every August.", [("demo_bcp_006", "held at Port Ilse every August")])], []),
    q(10): ("PROJECTABLE", [
        ("Velmaran is the capital of Oskeria.", [("demo_doc_001", "Velmaran, the capital of Oskeria")]),
        ("Port Ilse is the second city of Oskeria.", [("demo_doc_001", "Port Ilse, the second city")]),
        ("The ferry line between Velmaran and Port Ilse opened in 1902.", [("demo_bcp_018", "between Velmaran and Port Ilse opened in 1902")])], []),
    q(11): ("PROJECTABLE", [
        ("Liesl Varga discovered Comet Varga.", [("demo_bcp_010", "who discovered the comet that bears her name")]),
        ("Comet Varga was first photographed from a balloon.", []),
        ("The balloon photograph was taken in 1903.", [])],
        [("Liesl Varga was an Oskerian astronomer.", "redundant")]),
    q(12): ("PROJECTABLE", [
        ("Davin Kesh was a cellist from Hadley.", [("demo_bcp_019", "Davin Kesh, a cellist from Hadley")]),
        ("Davin Kesh founded the Port Ilse Conservatory in 1880.", [("demo_bcp_019", "founded the Port Ilse Conservatory in 1880")]),
        ("Port Ilse is the second city of Oskeria.", [("demo_doc_001", "Port Ilse, the second city")])], []),
}
SKIPPED = q(9)

# scrutiny passes: per hop of the full decomposition (s1, n1, s2, n2, docs); comprehensive and missing_fact for pass 2
T, F = True, False
SCRUTINY = {
    q(1): ([(T, T, T, T, ["demo_bcp_001"]), (T, T, T, T, ["demo_doc_003"]), (T, T, T, T, ["demo_bcp_001", "demo_doc_014"]),
            (T, T, T, T, ["demo_bcp_001"]), (T, F, T, F, ["demo_bcp_002"])], True, ""),
    q(2): ([(T, T, T, T, ["demo_bcp_007", "demo_bcp_006"]), (T, T, T, T, ["demo_bcp_007"]), (T, T, T, T, ["demo_bcp_008"]),
            (T, T, T, T, ["demo_bcp_009"])], False, "the crowd of 12,400 is not a hop"),
    q(3): ([(T, T, T, T, ["demo_bcp_012"]), (T, T, T, T, ["demo_bcp_013"]), (F, T, T, T, ["demo_bcp_010"]),
            (T, T, T, T, ["demo_bcp_011"])], True, ""),
    q(4): ([(T, T, T, T, ["demo_bcp_015"]), (F, T, F, T, []), (T, T, T, T, ["demo_bcp_014"]), (T, T, T, T, ["demo_bcp_014"]),
            (T, T, T, T, ["demo_bcp_016"])], False, "the ferry-captain father is not grounded"),
    q(5): ([(F, T, F, T, []), (F, T, F, T, []), (T, T, T, T, ["demo_bcp_003"]), (T, T, T, T, ["demo_bcp_004"])], False,
           "no document names a son"),
    q(8): ([(F, T, F, T, []), (F, T, F, T, []), (F, T, F, T, []), (T, T, T, T, ["demo_bcp_006"])], False, "the yard's closure is not grounded"),
    q(10): ([(T, T, T, T, ["demo_doc_001"]), (T, T, T, T, ["demo_doc_001"]), (F, T, F, T, [])], False, "the opening year is not grounded"),
    q(11): ([(T, T, T, T, ["demo_bcp_010"]), (F, T, F, T, []), (F, T, F, T, []), (F, F, F, F, [])], False, "no balloon photograph"),
    q(12): ([(F, T, F, T, []), (F, T, F, T, []), (F, T, F, T, [])], False, "nothing found"),
}
COVERAGE = {q(1): [], q(2): ["a crowd of 12,400"], q(3): [], q(4): []}
EXTERNAL = {   # independent projections: status, hops (clue, corpus_doc_ids)
    q(6): ("success", [("Tomas Hale kept the lighthouse at Cape Vardequist.", ["demo_doc_009"]),
                       ("The first ship he logged was the Grey Heron.", ["demo_bcp_020"])]),
    q(7): ("fail", [("Joran Ilvas won the Amberly Regatta.", ["demo_bcp_007"])]),
    q(8): ("success", [("The Brannoch yard on the Varrow estuary closed in 1962.", ["demo_bcp_009"]),
                       ("The Brannoch yard built the Grey Heron in 1931.", ["demo_bcp_008"]),
                       ("The Grey Heron won the 1948 Amberly Regatta.", ["demo_bcp_007"]),
                       ("The Amberly Regatta is held at Port Ilse every August.", ["demo_bcp_006"])]),
    q(12): ("success", [("Davin Kesh, a cellist from Hadley, founded the Port Ilse Conservatory.", ["demo_bcp_019"]),
                        ("The Port Ilse Conservatory was founded in 1880.", ["demo_doc_008"]),
                        ("Port Ilse is the second city of Oskeria.", ["demo_bcp_017"])]),
}
EXTERNAL_AUDIT = {q(6): [(T, "demo_doc_009", "names the keeper and the cape"), (F, "", "a page about herons, not a ship")],
                  q(8): [(T, "demo_bcp_009", "states the closure"), (T, "demo_bcp_008", "states the yard and year"),
                         (T, "demo_bcp_007", "states the 1948 win"), (T, "demo_bcp_006", "states the place and month")],
                  q(12): [(T, "demo_bcp_019", "states the founder"), (T, "demo_doc_008", "states the year"),
                          (F, "", "a page about the lighthouse, not the city")]}
REPAIR = {q(4): ([(2, "demo_bcp_015", "the son of a ferry captain")], []),
          q(5): ([(1, "demo_bcp_003", "married the cellist Mirela Ostvan in 1952")], [(2, "the documents name two daughters and no son")]),
          q(10): ([], [(3, "no document found that dates the ferry line")]),
          q(12): ([(3, "demo_doc_001", "Port Ilse, the second city")], [])}
STRICT = {   # per hop (verdict, docs, snippet, rescued); uncovered phrases
    q(2): ([("SUPPORTED", ["demo_bcp_007"], "won the Amberly Regatta three times", F), ("SUPPORTED", ["demo_bcp_007"], "sailing the cutter Grey Heron", F),
            ("SUPPORTED", ["demo_bcp_008"], "built at the Brannoch yard in 1931", F), ("SUPPORTED", ["demo_bcp_009"], "on the Varrow estuary", F)],
           ["a crowd of 12,400"]),
    q(3): ([("SUPPORTED", ["demo_bcp_012"], "taught mathematics at the Velmaran Lyceum", F),
            ("SUPPORTED", ["demo_bcp_013"], "is the oldest secondary school on the island of Oskeria", F),
            ("SUPPORTED", ["demo_bcp_010"], "who discovered the comet that bears her name in 1903", F),
            ("SUPPORTED", ["demo_bcp_011"], "its last perihelion was in 1974", F)], []),
    q(5): ([("SUPPORTED", ["demo_bcp_003"], "married the cellist Mirela Ostvan in 1952", T), ("PARTIAL", ["demo_bcp_003"], "they had two daughters", F),
            ("SUPPORTED", ["demo_bcp_003"], "The painter Teodor Lusk", F),
            ("SUPPORTED", ["demo_bcp_004"], "painted the ceiling of the Port Ilse ferry terminal in 1960", F)], []),
    q(10): ([("SUPPORTED", ["demo_doc_001"], "Velmaran, the capital of Oskeria", F), ("SUPPORTED", ["demo_doc_001"], "Port Ilse, the second city", F),
             ("SUPPORTED", ["demo_bcp_018"], "between Velmaran and Port Ilse opened in 1902", T)], []),
    q(11): ([("SUPPORTED", ["demo_bcp_010"], "who discovered the comet that bears her name", F), ("UNSUPPORTED", [], "", F),
             ("UNSUPPORTED", [], "", F), ("SUPPORTED", ["demo_bcp_010"], "the astronomer Liesl Varga", T)], []),
    q(12): ([("SUPPORTED", ["demo_bcp_019"], "Davin Kesh, a cellist from Hadley", T),
             ("SUPPORTED", ["demo_bcp_019"], "founded the Port Ilse Conservatory in 1880", T),
             ("SUPPORTED", ["demo_doc_001"], "Port Ilse, the second city", T)], []),
}
REJUDGE = {q(2): "fail_coverage", q(5): "survive", q(11): "fail_core"}
WAIVED = {(q(5), 2): "and one son"}
ABSENT = {(q(11), 2), (q(11), 3)}
OVERRIDE_Q4 = [  # (clue, supporting documents): the hop list written by hand for the accepted repair
    ("Anselm Drey was born in Port Ilse in 1907; Port Ilse is a ferry port (the Velmaran line, 1902).",
     ["demo_bcp_015", "demo_bcp_018"]),
    ("Anselm Drey was the son of a ferry captain.", ["demo_bcp_015"]),
    ("Anselm Drey published his first collection, Salt Lanterns, in 1931.", ["demo_bcp_014"]),
    ("Anselm Drey was the island's first poet laureate (1958-1970).", ["demo_bcp_014"]),
    ("A plaque on Harbour Street in Port Ilse marks the house where he lived from 1940.", ["demo_bcp_016"]),
]


# ------------------------------------------------------------------------------------- helpers
def encrypt(value, key):
    if isinstance(value, str):
        raw = value.encode("utf-8")
        d = hashlib.sha256(key.encode("utf-8")).digest()
        pad = d * (len(raw) // len(d)) + d[: len(raw) % len(d)]
        return base64.b64encode(bytes(a ^ b for a, b in zip(raw, pad))).decode("ascii")
    if isinstance(value, list):
        return [encrypt(v, key) for v in value]
    if isinstance(value, dict):
        return {k: encrypt(v, key) for k, v in value.items()}
    return value


def put(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1))


def py(script, *args, env=None, cwd=None, ok=True):
    r = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True,
                       env={**os.environ, **(env or {})}, cwd=cwd)
    if ok and r.returncode != 0:
        sys.exit(f"FAILED: {Path(script).name} {' '.join(map(str, args))}\n{r.stdout}\n{r.stderr}")
    return r


def build(step):
    r = py(STAGE1 / "bcp_build.py", "--run", RUN, "--set", SET, step)
    for line in r.stdout.strip().splitlines():
        print(f"  [{step}] {line}")


def state(*args):
    return py(STAGE1 / "bcp_state.py", "--run", RUN, SET, *args)


def pending(step, *extra):
    r = state(step, "--pending", "--brief", "--json", *extra)
    return [x["qid"] for x in json.loads(r.stdout)["questions"]]


def agent_input(step, qid):
    """What the agent does first: write and read its input."""
    p = Path(state(step, qid, "--input").stdout.strip())
    return json.loads(p.read_text())


def write_output(step, qid, obj):
    """What the agent does last: write its output, then --check until OK."""
    put(RUN / "stage1" / SET / step / f"{qid}.json", obj)
    r = state(step, qid, "--check")
    assert r.stdout.startswith("OK"), r.stdout


def expect(name, got, want):
    if sorted(got) != sorted(want):
        sys.exit(f"FAILED: {name}: got {sorted(got)}, expected {sorted(want)}")


def ids(name):
    p = RUN / "stage1" / SET / f"{name}.jsonl"
    return [json.loads(l).get("record_id") or json.loads(l).get("qid") for l in p.read_text().splitlines() if l.strip()]


# ------------------------------------------------------------------------------------- the run, step by step
def main():
    if RUN.exists():
        shutil.rmtree(RUN)
    cm = [sys.executable, str(PIPE / "demo" / "demo_cm.py")]
    put(RUN / "cmass.json", {
        "corpus": {"name": "demo-corpus", "backend": "cli", "cli": {"search": cm + ["search"], "doc": cm + ["doc"]},
                   "min_gap_s": 0.0, "slots": 4},
        "sets": {SET: {"input": f"inputs/{SET}.jsonl", "family": "bcp", "group": "released"},
                 f"nm_{SET}": {"input": f"inputs/{SET}_near_misses.jsonl", "family": "bcp", "group": "near_miss"}}})

    print("Stage 0: task construction from an encrypted source file in the dataset's columns")
    src = RUN / "source" / "test.jsonl"
    src.parent.mkdir(parents=True)
    with open(src, "w") as f:
        for qid, question, answer in TASKS:
            row = {"query_id": qid, "query": encrypt(question, DEMO_KEY), "answer": encrypt(answer, DEMO_KEY),
                   "gold_docs": encrypt(HINTS[qid][:1], DEMO_KEY), "evidence_docs": encrypt(HINTS[qid], DEMO_KEY)}
            f.write(json.dumps(row) + "\n")
    print("  " + py(STAGE1 / "stage0_tasks.py", "--run", RUN, "--set", SET, "--source-jsonl", src,
                    env={"BROWSECOMP_PLUS_CANARY": DEMO_KEY}).stdout.strip())
    tasks = {t["record_id"]: t for t in Run(RUN).tasks(SET)}
    for qid, question, answer in TASKS:
        assert tasks[qid]["question"] == question and tasks[qid]["answer"] == answer, f"decryption failed for {qid}"
        assert len(tasks[qid]["hints"]) == 2, "gold and evidence documents are deduplicated"

    print("Step project (agents): one output per task; a refusal is recorded in manual/skips.json")
    expect("project pending", pending("project"), [t[0] for t in TASKS])
    put(RUN / "stage1" / SET / "manual" / "skips.json", {SKIPPED: {"reason": "the projection agent refused (content policy)"}})
    expect("project pending after the skip", pending("project"), [t[0] for t in TASKS if t[0] != SKIPPED])
    for qid, (verdict, disc, red) in PROJECT.items():
        inp = agent_input("project", qid)
        write_output("project", qid, {
            "record_id": qid, "question": inp["question"], "answer": inp["answer"], "verdict": verdict,
            "answer_uniquely_inferable": verdict == "PROJECTABLE",
            "discriminating_hops": [{"clue": c, "corpus_evidence": [{"doc_id": d, "snippet": s} for d, s in ev]} for c, ev in disc],
            "redundant_or_unsupported_hops": [{"clue": c, "status": st} for c, st in red],
            "reasoning_path": "demo reasoning path", "rationale": "demo rationale"})
    expect("project pending at the end", pending("project"), [])
    build("verdicts")
    projectable = [k for k, v in PROJECT.items() if v[0] == "PROJECTABLE"]
    expect("projectable", ids("projectable"), projectable)

    print("Steps scrutiny1 and scrutiny2 (agents): every hop of the full decomposition, two independent passes")
    build("scrutiny-inputs")
    for step, pas in (("scrutiny1", 1), ("scrutiny2", 2)):
        expect(f"{step} pending", pending(step), projectable)
        for qid in projectable:
            inp = agent_input(step, qid)
            hops_t, comp, missing = SCRUTINY[qid]
            assert len(hops_t) == inp["n_hops"], f"{qid}: demo table has {len(hops_t)} hops, input {inp['n_hops']}"
            hops = []
            for clue, (s1, n1, s2, n2, docs) in zip(inp["hops"], hops_t):
                s, n = (s1, n1) if pas == 1 else (s2, n2)
                hops.append({"clue": clue, "supported": s, "necessary": n, "doc_ids": docs if s else [],
                             "note": "demo: the document states the fact" if s else "demo: not found"})
            out = {"qid": qid, "n_hops": len(hops), "n_supported": sum(h["supported"] for h in hops),
                   "n_necessary": sum(h["necessary"] for h in hops), "all_supported": all(h["supported"] for h in hops),
                   "all_necessary": all(h["necessary"] for h in hops), "hops": hops}
            out["keep"] = out["all_supported"] and out["all_necessary"]
            if pas == 2:
                out.update(comprehensive=comp, missing_fact=missing)
                nec = [h for h in hops if h["necessary"]]
                out["keep"] = bool(nec) and all(h["supported"] for h in nec) and comp
            write_output(step, qid, out)
    print("  the corpus commands of the guides: each pass searches with its own log (cm.py --stage 1 --step)")
    cmpy = PIPE / "tools" / "cm.py"
    query = "cellist born in Hadley founded a string quartet"
    for step in ("scrutiny1", "scrutiny2"):     # the same query in both passes: neither sees the other's log
        r = py(cmpy, "--run", RUN, "search", "--stage", "1", "--step", step, "--set", SET, "--qid", q(1), query,
               "--hits", "3", "--chars", "120", "--center")
        assert "demo_bcp_001" in r.stdout, r.stdout
        assert (RUN / "stage1" / SET / step / f"{q(1)}.searchlog.jsonl").exists()
    r = py(cmpy, "--run", RUN, "search", "--stage", "1", "--step", "scrutiny1", "--set", SET, "--qid", q(1), query,
           "--hits", "3", "--center", ok=False)
    assert r.returncode == 3 and "REFUSED" in r.stdout, "a repeated query within one step's log is refused"
    py(cmpy, "--run", RUN, "doc", "--stage", "1", "--step", "scrutiny1", "--set", SET, "--qid", q(1), "demo_bcp_001", "--max", "200000")
    assert state("scrutiny1", q(1), "--check").stdout.startswith("OK"), "a step log beside the outputs is not an output"
    build("all-hops")
    expect("all hops grounded (union)", ids("all_grounded"), [q(1), q(2), q(3)])

    print("Step coverage (agents, no corpus access) on the all-hops-grounded questions")
    build("coverage-bundles")
    expect("coverage pending", pending("coverage"), [q(1), q(2), q(3)])
    for qid in pending("coverage"):
        agent_input("coverage", qid)
        unc = COVERAGE[qid]
        write_output("coverage", qid, {"qid": qid, "coverage_ok": not unc, "uncovered_phrases": unc, "keep": not unc,
                                       "reason": "demo: " + ("a number is not a hop" if unc else "every phrase has a hop")})
    build("coverage-final")
    expect("coverage final", ids("coverage_final"), [q(1), q(3)])

    print("Manual review (manual/review.json)")
    put(RUN / "stage1" / SET / "manual" / "review.json", {q(3): {"decision": "drop", "reason": "demo: removed by the reviewer"}})
    build("review")
    expect("reviewed", ids("reviewed"), [q(1)])

    print("Independent projections (external/) and their audit (agents)")
    for qid, (status, hops) in EXTERNAL.items():
        t = tasks[qid]
        put(RUN / "stage1" / SET / "external" / f"{qid}.json", {
            "qid": qid, "question": t["question"], "answer": t["answer"], "status": status,
            "hops": [{"clue": c, "corpus_doc_ids": d, "snippets": []} for c, d in hops]})
    build("external")
    expect("external audit pending", pending("external_audit"), [q(6), q(8), q(12)])
    for qid in pending("external_audit"):
        inp = agent_input("external_audit", qid)
        per = [{"clue": h["clue"], "grounded": g, "doc_used": d, "why": "demo: " + w}
               for h, (g, d, w) in zip(inp["hops"], EXTERNAL_AUDIT[qid])]
        allg = all(p["grounded"] for p in per)
        write_output("external_audit", qid, {"qid": qid, "all_hops_present": True, "chain_ok": True, "all_hops_grounded": allg,
                                             "keep": allg, "reason": "demo", "per_hop": per})
    build("external-final")
    expect("independent projections verified", ids("external_verified"), [q(8)])

    print("Near-miss repair (agents): phase A and phase B")
    build("repair-pool")
    expect("repair phase A", pending("repair", "--phase", "A"), [q(4), q(10), q(12)])
    expect("repair phase B", pending("repair", "--phase", "B"), [q(5)])
    for qid in pending("repair"):
        inp = agent_input("repair", qid)
        clue = {h["index"]: h["clue"] for h in inp["hops"]}
        fixed, unfix = REPAIR[qid]
        failing = {h["index"] for h in inp["hops"] if h["FAILING"]}
        write_output("repair", qid, {
            "qid": qid, "repaired_all": failing <= {i for i, _, _ in fixed},
            "fixed": [{"index": i, "clue": clue[i], "doc_id": d, "snippet": s} for i, d, s in fixed],
            "unfixable": [{"index": i, "clue": clue[i], "why": "demo: " + w} for i, w in unfix]})
    build("repaired")
    expect("claimed repairs", ids("repaired"), [q(4), q(12)])
    put(RUN / "stage1" / SET / "manual" / "repairs.json", {
        q(4): {"verified": True, "decision": "", "reason": "demo: the fixing document states the fact"},
        q(12): {"verified": False, "decision": "reject", "reason": "demo: the operator found the fixing document off-target"}})
    build("coverage-bundles")
    expect("coverage pending for the verified repair", pending("coverage"), [q(4)])
    agent_input("coverage", q(4))
    write_output("coverage", q(4), {"qid": q(4), "coverage_ok": True, "uncovered_phrases": [], "keep": True, "reason": "demo"})
    put(RUN / "stage1" / SET / "manual" / "repairs.json", {
        q(4): {"verified": True, "decision": "accept", "reason": "demo: accepted"},
        q(12): {"verified": False, "decision": "reject", "reason": "demo: the operator found the fixing document off-target"}})
    put(RUN / "stage1" / SET / "manual" / "overrides.json", {q(4): {
        "hops": [{"clue": c, "redundant": False, "supporting_doc_ids": d} for c, d in OVERRIDE_Q4],
        "reason": "demo: caveats written into the hops"}})
    build("candidates")
    expect("candidates", ids("candidates"), [q(1), q(4), q(8)])

    print("Expansion of the unused PROJECTABLE questions (agents): strict audit, then re-judging")
    build("expansion-pool")
    expect("strict audit pending", pending("strict_audit"), [q(2), q(3), q(5), q(10), q(11), q(12)])
    for qid in pending("strict_audit"):
        inp = agent_input("strict_audit", qid)
        rows, unc = STRICT[qid]
        hops = [{"index": h["index"], "clue": h["clue"], "verdict": v, "doc_ids": d, "snippet": s, "rescued": r}
                for h, (v, d, s, r) in zip(inp["hops"], rows)]
        allg = all(h["verdict"] == "SUPPORTED" for h in hops)
        write_output("strict_audit", qid, {"qid": qid, "hops": hops, "uncovered_phrases": unc, "coverage_ok": not unc,
                                           "all_hops_grounded": allg, "qualifies": allg and not unc, "reason": "demo"})
    expect("rejudge pending", pending("rejudge"), [q(2), q(5), q(11)])
    for qid in pending("rejudge"):
        inp = agent_input("rejudge", qid)
        hops = []
        for h, sh in zip(inp["hops"], inp["strict_audit"]["hops"]):
            k = (qid, h["index"])
            if k in WAIVED:
                hops.append({"index": h["index"], "clue": h["clue"], "fully_grounded": False,
                             "basis": f"core fact grounded; qualifier waived: {WAIVED[k]}", "missing_qualifier": WAIVED[k],
                             "snippet": sh["snippet"], "supporting_doc_ids": sh["doc_ids"]})
            elif k in ABSENT:
                hops.append({"index": h["index"], "clue": h["clue"], "fully_grounded": False, "basis": "core fact absent",
                             "missing_qualifier": "", "snippet": "", "supporting_doc_ids": []})
            else:
                hops.append({"index": h["index"], "clue": h["clue"], "fully_grounded": True, "basis": "verbatim grounded",
                             "missing_qualifier": "", "snippet": sh["snippet"], "supporting_doc_ids": sh["doc_ids"]})
        write_output("rejudge", qid, {"qid": qid, "hops": hops, "outcome": REJUDGE[qid], "reason": "demo"})
    build("expansion")
    expect("expansion survivors", ids("expansion_survivors"), [q(3), q(5), q(10), q(12)])

    print("Near misses: the manual trim and the qrels-review cut")
    put(RUN / "stage1" / SET / "manual" / "expansion_trim.json", {q(10): {"decision": "drop", "reason": "demo: removed by hand"}})
    put(RUN / "stage1" / SET / "manual" / "qrels_review.json", {q(1): {"reason": "demo: a required hop judged PARTIAL in the qrels review"}})
    build("near-misses")
    build("counts")
    print(state("--summary").stdout.rstrip())

    print("Stage 2 inputs: records and packets")
    run = Run(RUN)
    for sid, want in ((SET, [q(1), q(4), q(8)]), (f"nm_{SET}", [q(1), q(3), q(5), q(12)])):
        recs = run.records(sid)
        expect(f"inputs of {sid}", [r["record_id"] for r in recs], want)
        for r in recs:
            errs, _ = candidate_problems(r, run.config.set(sid)["answer_type"])
            assert not errs, f"{sid}/{r['record_id']}: {errs}"
        r = py(PIPE / "tools" / "packets.py", sid, cwd=RUN)
        print("  " + (r.stdout.strip().splitlines() or ["(no output)"])[-1])
        for rec in recs:
            assert run.packet(sid, rec["record_id"]).exists(), f"no packet for {sid}/{rec['record_id']}"
    nm = {r["record_id"]: r for r in run.records(f"nm_{SET}")}
    assert nm[q(1)]["stage_rank"] == 1 and nm[q(5)]["stage_rank"] == 2
    assert run.record(SET, q(4))["hops"][0]["supporting_doc_ids"] == ["demo_bcp_015", "demo_bcp_018"], "override applied"
    print(f"demo OK: {RUN}")


if __name__ == "__main__":
    main()
