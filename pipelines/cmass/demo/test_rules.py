#!/usr/bin/env python3
"""Offline tests of stage3/rules.py on invented documents: demo/rules_corpus/ (the fictional Oskeria, ids demo_doc_250
to demo_doc_299; no benchmark content). rules_corpus/rules.json plays the agent: the rule files it might write, version
by version.

  python3 pipelines/cmass/demo/test_rules.py [WORK_DIR]     # ends with "all rules demo checks passed"

1. inputs, in a temporary directory: the compact rows of rules_corpus/questions.json expanded into qrels_hops, qrels
   and quotes JSONL and a standards directory; a two-shard parquet corpus (ids shard_<NNNNN>_<row>); a config file
2. state before anything (packet; a derivation-only question: skip); packet: only hops with documents, recorded
   quotes from qrels_hops and from --quotes, a window around the clue's terms, the document start, the standard; the
   known file
3. doc: the text, and --grep counts on word boundaries and as bare substrings
4. invalid rules files: state write with the reason; check refuses them
5. demo_r1, hops whose known documents need two rules and an alias: check fails with the missed ids (fix_coverage);
   the second version passes, breadth, done; a changed rule makes the reports stale, restoring it makes them current
6. demo_r2, a hop that starts too broad: narrow; narrowed, the other hop is still broad (narrow); broad_ok on the wrong
   rule is not enough; broad_ok on the broad rule: done with no new run; custom samples (--sample 1, --shards)
   extrapolate and leave the state alone
7. collect: the rules of the done questions, re-checked together (and --no-recheck); the summary
8. a JSONL corpus with demo ids, options as flags instead of a config: packet, check, breadth
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RULES_PY = HERE.parent / "stage3" / "rules.py"
DATA = HERE / "rules_corpus"
PY = sys.executable
DOCS = json.loads((DATA / "docs.json").read_text())
QS = json.loads((DATA / "questions.json").read_text())
FIX = json.loads((DATA / "rules.json").read_text())
SET = "demo"

failures = 0


def ok(cond, what):
    global failures
    if not cond:
        failures += 1
        print(f"FAIL: {what}")


def run(args, expect=0):
    r = subprocess.run([PY, str(RULES_PY), *map(str, args)], capture_output=True, text=True)
    if expect is not None and r.returncode != expect:
        sys.exit(f"FAILED (exit {r.returncode}, expected {expect}): rules.py {' '.join(map(str, args))}\n"
                 f"{r.stdout[-4000:]}\n{r.stderr[-4000:]}")
    return r


def write_jsonl(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


# ------------------------------------------------------------------------------------------------------ 1. inputs
def make_inputs(work: Path, idmap: dict) -> None:
    m = idmap.get
    hops_rows, qrels_rows = [], []
    for qid, question, answer, hops in QS["question_rows"]:
        hs = []
        for hid, kind, clue, docs in hops:
            hs.append({"id": hid, "kind": kind, "clue": clue,
                       "docs": {m(d, d): ({"supports": sup, "quotes": qs} if qs else sup) for d, sup, qs in docs}})
        hops_rows.append({"id": qid, "hops": hs})
        qrels_rows.append({"id": qid, "question": question, "answer": answer})
    write_jsonl(work / "qrels_hops.jsonl", hops_rows)
    write_jsonl(work / "qrels.jsonl", qrels_rows)
    write_jsonl(work / "quotes.jsonl", [{"id": q, "hop": h, "doc_id": m(d, d), "quote": t}
                                        for q, h, d, t in QS["quote_rows"]])
    for qid, std in QS["standards"].items():
        (work / "standards").mkdir(parents=True, exist_ok=True)
        (work / "standards" / f"{qid}.json").write_text(json.dumps({"qid": qid, "hops": std}))


def make_parquet(work: Path) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq
    corpus = work / "parquet"
    corpus.mkdir()
    idmap = {}
    for num, part in ((0, DOCS[:25]), (1, DOCS[25:])):
        pq.write_table(pa.table({"text": [d["text"] for d in part]}), corpus / f"shard_{num:05d}.parquet",
                       row_group_size=8)
        for row, d in enumerate(part):
            idmap[d["docid"]] = f"shard_{num:05d}_{row}"
    return idmap


# --------------------------------------------------------------------------------------------------------- tests
class Demo:
    def __init__(self, work: Path, idmap: dict):
        self.work, self.idmap = work, idmap
        self.cfg = work / "config.json"
        inputs = {k: f"{k}.jsonl" for k in ("qrels_hops", "qrels", "quotes")}     # written by make_inputs
        self.cfg.write_text(json.dumps({**inputs, "standards": "standards", "corpus": "parquet", "set": SET,
                                        "out": "rules_out", "max_est": 10, "breadth_workers": 2}))
        self.dir = work / "rules_out" / SET

    def r(self, *args, expect=0):
        return run(["--config", self.cfg, *args], expect=expect)

    def id(self, n: int) -> str:
        return self.idmap[f"demo_doc_{n}"]

    def state(self, *qids) -> dict:
        out = json.loads(self.r("state", "--json", *qids).stdout)
        return {s["qid"]: s for s in out["questions"]}

    def write_rules(self, qid: str, rows) -> None:
        p = self.dir / f"{qid}.rules.jsonl"
        if isinstance(rows, str):
            p.write_text(rows + "\n")
        else:
            write_jsonl(p, rows)

    def expect_state(self, qid, nxt, what, contains=()):
        s = self.state(qid)[qid]
        ok(s["next"] == nxt, f"{what}: state of {qid} is {s['next']!r} ({s['detail']}), expected {nxt!r}")
        for c in contains:
            ok(c in s["detail"], f"{what}: state detail of {qid} lacks {c!r}: {s['detail']}")
        return s


def packet_sections(text: str) -> dict:
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("### "):
            cur = line[4:].split()[0]
            out[cur] = [line]
        elif line.startswith("## "):
            cur = None
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def test_packet(d: Demo) -> None:
    st = d.state()
    ok(sorted(st) == ["demo_r1", "demo_r2", "demo_r3", "demo_r4"], f"state lists every question ({sorted(st)})")
    ok(all(st[q]["next"] == "packet" for q in ("demo_r1", "demo_r2", "demo_r3")), "state before packets: packet")
    ok(st["demo_r4"]["next"] == "skip" and not st["demo_r4"]["done"], "a derivation-only question is skip, not done")
    r = d.r("packet", "demo_r1")
    ok("2 hops, 5 known documents (0 unavailable)" in r.stdout, f"packet summary line: {r.stdout.strip()}")
    md = (d.dir / "work" / "demo_r1.md").read_text()
    ok("## Hop 1  (group demo/demo_r1/1)" in md and "## Hop 2  (group demo/demo_r1/2)" in md, "packet: hops 1, 2")
    ok("## Hop 3" not in md and "## Hop 4" not in md, "packet: no derivation hop, no hop without documents")
    ok("not scanned: hop 3 (derivation), hop 4 (no documents)" in md, "packet names the hops it skips")
    ok("question: How many years passed" in md and "answer: 63" in md, "packet: question and answer from --qrels")
    sec = packet_sections(md)
    s250, s251, s252 = sec[d.id(250)], sec[d.id(251)], sec[d.id(252)]
    ok("recorded quote, in context" in s250 and "**ordered the old customs quay closed in 1934**" in s250
       and "> Edda Varn, harbour master" in s250, f"packet: the quote recorded in qrels_hops, in context ({s250!r})")
    ok("recorded quote" in s251 and "spring of '34**, the fish merchants" in s251,
       f"packet: the quote from --quotes ({s251!r})")
    ok("window around" in s252 and "customs quay" in s252 and "> ... " in s252 and "Edda Varn" in s252
       and "grew from a fishing village" not in s252, f"packet: the window around the clue's terms ({s252!r})")
    ok(len(s252.splitlines()[-1]) < 560, "packet: the window is about 500 characters")
    ok("(full; 793 characters;" in s252, "packet: support label and length")
    ok("$R check demo_r1" in md and str(d.cfg) in md, "packet: the commands, with this config")
    known = [json.loads(x) for x in (d.dir / "demo_r1.known.jsonl").read_text().splitlines()]
    ok(known == [{"group": "demo/demo_r1/1", "doc_id": d.id(n)} for n in (250, 251, 252)]
       + [{"group": "demo/demo_r1/2", "doc_id": d.id(n)} for n in (253, 254)], f"known file ({known})")
    d.r("packet", "demo_r3")
    md3 = (d.dir / "work" / "demo_r3.md").read_text()
    sec3 = packet_sections(md3)
    ok("document start (no clue term found)" in sec3[d.id(294)] and "> Programme note." in sec3[d.id(294)],
       f"packet: the document start when no clue term occurs ({sec3[d.id(294)]!r})")
    ok("  subject: Ilsa Renvaar" in md3 and "  reject: songs or prizes named after Renvaar" in md3,
       "packet: the hop standard")
    ok("recorded quote" in sec3[d.id(293)], "packet: a --quotes quote for demo_r3")
    d.r("packet", "demo_r2")
    r = d.r("packet", "demo_r4", expect=1)
    ok("nothing to write" in r.stderr, "packet refuses a question with no hop to scan")
    d.expect_state("demo_r1", "write", "after the packet", ["no rules file"])


def test_doc(d: Demo) -> None:
    r = d.r("doc", "demo_r1", d.id(251))
    ok("known for hop 1 (full) of demo_r1" in r.stdout and "customs wharf in the spring" in r.stdout, "doc: text")
    r = d.r("doc", "demo_r1", d.id(256), "--grep", "flood", "customs quay")
    ok("'flood': 1 on word boundaries, 2 as a bare substring" in r.stdout, f"doc --grep counts ({r.stdout})")
    ok("flood marks" in r.stdout and "'customs quay': 0 on word boundaries" in r.stdout, "doc --grep windows")
    ok("not a known document of demo_r1" in r.stdout, "doc: a document the question does not know")
    r = d.r("doc", "demo_r1", d.id(252), "--offset", "700", "--chars", "50")
    ok("more characters: --offset 750" in r.stdout, "doc --offset/--chars")
    r = d.r("doc", "demo_r1", "shard_00001_999", expect=1)
    ok("not found" in r.stderr, "doc: an unknown id")


def test_invalid(d: Demo) -> None:
    for name, why in (("json", "not a JSON line"), ("other_group", "is not a hop group of demo_r1"),
                      ("prefix", "must start with its group, demo/demo_r1/1/"),
                      ("short_reason", "meta.broad_ok must be the reason"),
                      ("hop_unruled", "hop 2: no rule (group demo/demo_r1/2)")):
        d.write_rules("demo_r1", FIX["invalid"][name])
        d.expect_state("demo_r1", "write", f"invalid rules ({name})", ["does not validate", why])
    r = d.r("check", "demo_r1", expect=2)
    ok("fix the rules file" in r.stdout and "no rule (group demo/demo_r1/2)" in r.stdout, "check refuses bad rules")
    d.r("breadth", "demo_r1", expect=2)


def test_r1(d: Demo) -> None:
    d.write_rules("demo_r1", FIX["demo_r1"]["v1"])
    d.expect_state("demo_r1", "check", "v1 before check", ["no check report"])
    r = d.r("check", "demo_r1", expect=1)
    ok(f"hop 1: 2/3 FAIL; missed: {d.id(251)}" in r.stdout and f"hop 2: 1/2 FAIL; missed: {d.id(254)}" in r.stdout,
       f"check prints per hop matched/known and the missed ids ({r.stdout[-600:]})")
    rep = json.loads((d.dir / "work" / "demo_r1.check.json").read_text())
    g = {x["group"]: x for x in rep["groups"]}
    ok(g["demo/demo_r1/1"]["missed"] == [d.id(251)] and g["demo/demo_r1/2"]["missed"] == [d.id(254)],
       "check.json: missed ids per group")
    ok(g["demo/demo_r1/2"]["why"][d.id(254)]["demo/demo_r1/2/light_1871"]["failed_clauses"] == [0, 1, 2],
       "check.json: why (every clause of the narrow rule fails on the alias spelling)")
    ok(rep["rules_digest"] and rep["known_digest"], "check.json carries the digests")
    s = d.expect_state("demo_r1", "fix_coverage", "v1 checked", [d.id(251), d.id(254)])
    ok([h["missed"] for h in s["hops"]] == [[d.id(251)], [d.id(254)]], "state: missed ids per hop")
    ok([h["matched"] for h in s["hops"]] == [2, 1], "state: matched per hop")

    d.write_rules("demo_r1", FIX["demo_r1"]["v2"])
    d.expect_state("demo_r1", "check", "v2 makes the check report stale", ["other rules"])
    d.r("check", "demo_r1")
    d.expect_state("demo_r1", "breadth", "v2 checked", ["no breadth report"])
    r = d.r("breadth", "demo_r1")
    ok("2 of 2 shards (sample 24)" in r.stdout and "group demo/demo_r1/1: 4 in the sample, est 4  ok" in r.stdout,
       f"breadth: both shards when the corpus has fewer than the sample ({r.stdout})")
    s = d.expect_state("demo_r1", "done", "v2 measured")
    ok(s["done"] and [h["est"] for h in s["hops"]] == [4, 2] and all(h["cap_ok"] for h in s["hops"]),
       f"state done: estimates per hop ({[h['est'] for h in s['hops']]})")
    br = json.loads((d.dir / "work" / "demo_r1.breadth.json").read_text())
    ok(br["rules"]["demo/demo_r1/1/varn_quay"]["sample"] == 3 and br["rules"]["demo/demo_r1/1/varn_wharf"]["sample"] == 1,
       "breadth.json: matches per rule")
    ok(br["rules"]["demo/demo_r1/1/varn_quay"]["examples"] == [d.id(n) for n in (250, 252, 257)],
       "breadth.json: example ids per rule")
    ok(br["rules"]["demo/demo_r1/1/varn_wharf"]["examples"] == [d.id(251)], "breadth.json: examples of a narrow rule")

    extra = FIX["demo_r1"]["v2"] + [{"rule_id": "demo/demo_r1/2/extra", "group": "demo/demo_r1/2",
                                     "all": [["Tomas Hale"], ["1890"]]}]
    d.write_rules("demo_r1", extra)
    d.expect_state("demo_r1", "check", "an added rule makes the reports stale")
    d.write_rules("demo_r1", FIX["demo_r1"]["v2"])
    d.expect_state("demo_r1", "done", "restoring the rules makes the reports current again")
    noted = [dict(x) for x in FIX["demo_r1"]["v2"]]
    noted[0]["meta"] = {"note": "a meta change is not a rule change"}
    d.write_rules("demo_r1", noted)
    d.expect_state("demo_r1", "done", "a meta-only change keeps the reports current")
    d.write_rules("demo_r1", FIX["demo_r1"]["v2"])


def test_r2(d: Demo) -> None:
    d.write_rules("demo_r2", FIX["demo_r2"]["v1"])
    d.r("check", "demo_r2")
    r = d.r("breadth", "demo_r2", expect=1)
    ok("group demo/demo_r2/1: 36 in the sample, est 36  OVER CAP" in r.stdout and "examples:" in r.stdout,
       f"breadth flags the too-broad hop and lists examples ({r.stdout})")
    s = d.expect_state("demo_r2", "narrow", "v1 too broad", ["hop 1: est 36 > cap 10", "hop 2: est 14 > cap 10"])
    ok([h["est"] for h in s["hops"]] == [36, 14] and not any(h["cap_ok"] for h in s["hops"]), "state: over-cap hops")

    r = d.r("breadth", "demo_r2", "--sample", "1", "--workers", "1", expect=1)
    ok("1 of 2 shards (sample 1, not the standard sample)" in r.stdout
       and "group demo/demo_r2/1: 19 in the sample, est 38" in r.stdout, f"--sample 1 extrapolates x2 ({r.stdout})")
    cust = json.loads((d.dir / "work" / "demo_r2.breadth_custom.json").read_text())
    ok(cust["factor"] == 2.0 and cust["shards"] == ["shard_00000"] and not cust["standard"], "custom report")
    d.expect_state("demo_r2", "narrow", "a custom sample does not change the state", ["est 36"])

    d.write_rules("demo_r2", FIX["demo_r2"]["v2"])
    d.expect_state("demo_r2", "check", "narrowed rules need a new check")
    d.r("check", "demo_r2")
    d.r("breadth", "demo_r2", expect=1)
    s = d.expect_state("demo_r2", "narrow", "v2: hop 1 narrowed, hop 2 still broad", ["hop 2: est 14 > cap 10"])
    ok("hop 1" not in s["detail"] and s["hops"][0]["est"] == 3 and s["hops"][0]["cap_ok"], "hop 1 within the cap")

    wrong = FIX["demo_r2"]["v2"] + [{"rule_id": "demo/demo_r2/2/herring", "group": "demo/demo_r2/2",
                                     "all": [["Port Ilse"], ["herring festival"]],
                                     "meta": {"broad_ok": "placed on the wrong rule of the group"}}]
    d.write_rules("demo_r2", wrong)
    d.r("check", "demo_r2")
    d.r("breadth", "demo_r2", expect=1)
    d.expect_state("demo_r2", "narrow", "broad_ok on another rule of the group",
                   ["over the cap without broad_ok: demo/demo_r2/2/mouth"])

    d.write_rules("demo_r2", FIX["demo_r2"]["v3"])
    d.r("check", "demo_r2")
    r = d.r("breadth", "demo_r2")
    ok("group demo/demo_r2/2: 14 in the sample, est 14  ok (broad_ok)" in r.stdout, f"breadth honours broad_ok ({r.stdout})")
    s = d.expect_state("demo_r2", "done", "v3 broad_ok", ["hop 2 est 14 (broad_ok)"])
    ok(s["hops"][1]["broad_ok"].startswith("the town's place"), "state: the broad_ok reason")
    r = d.r("breadth", "demo_r2", "--shards", "1:2")
    ok("group demo/demo_r2/2: 12 in the sample, est 24" in r.stdout and "group demo/demo_r2/1: 1 in the sample, est 2"
       in r.stdout, f"--shards 1:2 ({r.stdout})")
    d.expect_state("demo_r2", "done", "a custom shard range leaves the state alone")


def test_collect(d: Demo) -> None:
    d.expect_state("demo_r3", "write", "demo_r3 has a packet and no rules")
    out = d.work / "passing.jsonl"
    r = d.r("collect", "--out-rules", out)
    ids = [json.loads(x)["rule_id"] for x in out.read_text().splitlines()]
    want = [x["rule_id"] for x in FIX["demo_r1"]["v2"] + FIX["demo_r2"]["v3"]]
    ok(ids == want, f"collect: the rules of the done questions, as written ({ids})")
    ok(json.loads(out.read_text().splitlines()[1]) == FIX["demo_r1"]["v2"][1], "collect keeps rules as written")
    summ = json.loads((d.dir / "collect" / "summary.json").read_text())
    ok(summ["done"] == ["demo_r1", "demo_r2"] and summ["not_done"] == {"demo_r3": "write"}
       and summ["by_state"] == {"done": 2, "skip": 1, "write": 1}, f"collect summary: questions ({summ})")
    ok(summ["rules"] == 5 and summ["groups"] == 4 and summ["est_total"] == 4 + 2 + 3 + 14, "collect summary: totals")
    ok([b["group"] for b in summ["broad_ok"]] == ["demo/demo_r2/2"] and summ["broad_ok"][0]["est"] == 14,
       "collect summary: the broad_ok group with its estimate")
    ok("2 done of 4 questions" in r.stdout, f"collect prints a summary ({r.stdout[-400:]})")
    out2 = d.work / "passing_norecheck.jsonl"
    d.r("collect", "--out-rules", out2, "--no-recheck")
    ok(out2.read_text() == out.read_text(), "collect --no-recheck writes the same rules")


def test_jsonl_flags(work: Path) -> None:
    corpus = write_jsonl(work / "j" / "docs.jsonl", DOCS)
    make_inputs(work / "j", {})
    base = ["--qrels-hops", work / "j" / "qrels_hops.jsonl", "--corpus", corpus, "--set", SET, "--out", work / "j" / "out"]
    run(base + ["packet", "demo_r1"])
    write_jsonl(work / "j" / "out" / SET / "demo_r1.rules.jsonl", FIX["demo_r1"]["v2"])
    r = run(["check", "demo_r1"] + base)                      # options after the subcommand work too
    ok("hop 1: 3/3 PASS" in r.stdout and "hop 2: 2/2 PASS" in r.stdout, "JSONL corpus: check passes")
    md = (work / "j" / "out" / SET / "work" / "demo_r1.md").read_text()
    ok("question:" not in md and "--qrels-hops" in md, "no --qrels: no question line; the packet repeats the flags")
    r = run(base + ["--max-est", "3", "breadth", "demo_r1"], expect=1)
    ok("1 of 1 shards" in r.stdout and "est 4  OVER CAP" in r.stdout, f"JSONL corpus: breadth, --max-est ({r.stdout})")
    s = json.loads(run(base + ["--max-est", "3", "state", "--json", "--brief", "demo_r1"]).stdout)
    ok(s["questions"][0]["next"] == "narrow" and set(s["questions"][0]) == {"set", "qid", "next", "done", "detail"},
       "state --brief with a lower cap")
    s = json.loads(run(base + ["state", "--json", "--brief", "--todo"]).stdout)
    ok([x["qid"] for x in s["questions"]] == ["demo_r2", "demo_r3"] and s["count"]["done"] == 1
       and s["count"]["skip"] == 1, f"state --todo: questions left to do; count over all ({s})")


def main() -> None:
    keep = len(sys.argv) > 1
    work = Path(sys.argv[1]).resolve() if keep else Path(tempfile.mkdtemp(prefix="cmass_rules_"))
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    try:
        idmap = make_parquet(work)
        make_inputs(work, idmap)
        d = Demo(work, idmap)
        test_packet(d)
        test_doc(d)
        test_invalid(d)
        test_r1(d)
        test_r2(d)
        test_collect(d)
        test_jsonl_flags(work)
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    if failures:
        sys.exit(f"{failures} rules demo check(s) FAILED")
    print("all rules demo checks passed")


if __name__ == "__main__":
    main()
