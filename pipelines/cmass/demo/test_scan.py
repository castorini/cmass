#!/usr/bin/env python3
"""Offline tests of stage3/scan.py on invented documents: demo/scan_corpus/ (the fictional Oskeria, ids demo_doc_200
onwards; no benchmark content).

  python3 pipelines/cmass/demo/test_scan.py [WORK_DIR]     # ends with "all scan demo checks passed"

1. matching, per matcher (token always; aho when pyahocorasick is importable): hand-written expectations for every
   demo rule and group (alternatives, "none", word boundaries such as "IUU" inside "Taliuuna" or "IUU_2",
   case-insensitivity with non-ASCII letters, whitespace normalisation, overlapping and punctuation phrases, a
   one-word document), then both matchers against a brute-force reference on random strings
2. rule validation: unknown keys, bad or duplicate ids, empty phrases, ids whose files would collide
3. CLI on a JSONL corpus: scan, merge, counts.json, matches/<group>.txt and rule_matches/<rule_id>.txt
4. CLI on a two-shard parquet corpus (ids shard_<NNNNN>_<row>, unpadded): --workers 2, --shards, the refusal to redo a
   done shard, --resume, merge refusing a partial scan, --allow-partial, --max-per-rule / --max-per-group (exact
   counts, the first N ids recorded), stale temporary files, a changed rules file refused
5. check: per group pass/fail and exit status, missed ids, the rules carrying each document, a second rule fixing a
   hop, --write-passing, a group with no rule, an unreadable id, texts from parquet (a padded id spelling) and --texts
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCAN = HERE.parent / "stage3" / "scan.py"
sys.path.insert(0, str(SCAN.parent))
import scan  # noqa: E402

PY = sys.executable
DOCS = json.loads((HERE / "scan_corpus" / "docs.json").read_text())
RULES = json.loads((HERE / "scan_corpus" / "rules.json").read_text())
EXTRA = json.loads((HERE / "scan_corpus" / "rules_extra.json").read_text())
KNOWN = json.loads((HERE / "scan_corpus" / "known.json").read_text())
ORDER = [d["docid"] for d in DOCS]


def ids(*nums):
    return [f"demo_doc_{n}" for n in nums]


EXPECT_RULES = {
    "demo/q1/h1/iuu": ids(200, 201, 203, 221, 222),
    "demo/q1/h1/cod_quota": ids(201, 222),
    "demo/q2/h1/keeper": ids(207),
    "demo/q3/h1/harbour": ids(209, 225),
    "demo/q4/h1/cafe": ids(212, 213),
    "ardune_any": ids(210, 212, 213, 214, 215, 216, 217, 218),
    "cxx": ids(211),
    "gulf_not_amberly": ids(224),
    "ferry": ids(216),
}
GROUP_OF = {r["rule_id"]: r.get("group", r["rule_id"]) for r in RULES}
EXPECT_GROUPS: dict[str, list] = {}
for _r, _ds in EXPECT_RULES.items():
    _g = EXPECT_GROUPS.setdefault(GROUP_OF[_r], [])
    _g.extend(d for d in _ds if d not in _g)
for _g in EXPECT_GROUPS:
    EXPECT_GROUPS[_g].sort(key=ORDER.index)

failures = 0


def ok(cond, what):
    global failures
    if not cond:
        failures += 1
        print(f"FAIL: {what}")


def run(args, expect=0):
    r = subprocess.run([PY, str(SCAN), *map(str, args)], capture_output=True, text=True)
    if expect is not None and r.returncode != expect:
        sys.exit(f"FAILED (exit {r.returncode}, expected {expect}): scan.py {' '.join(map(str, args))}\n"
                 f"{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return r


def write_jsonl(path: Path, rows) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def matchers():
    names = ["token"]
    try:
        import ahocorasick  # noqa: F401
        names.append("aho")
    except ImportError:
        print("note: pyahocorasick is not importable here; the aho matcher is not tested")
    return names


# ------------------------------------------------------------------------------------------------- 1. matching
def ref_contains(t: str, p: str) -> bool:
    """Brute force: p occurs in t with the boundary rule (both already normalised)."""
    i = t.find(p)
    while i >= 0:
        j = i + len(p)
        if ((not scan.is_word(p[0]) or i == 0 or not scan.is_word(t[i - 1]))
                and (not scan.is_word(p[-1]) or j == len(t) or not scan.is_word(t[j]))):
            return True
        i = t.find(p, i + 1)
    return False


def test_matching(work: Path, names) -> None:
    rules = scan.load_rules(write_jsonl(work / "rules.jsonl", RULES))
    engine = scan.Engine(rules)
    texts = scan.normalize([d["text"] for d in DOCS])
    ok(scan.normalize(["A  B\n\tC  "]) == ["a b c "], "normalisation: case and every whitespace run")
    for name in names:
        m = scan.MATCHERS[name](engine.phrases)
        got = {r.rule_id: [] for r in rules}
        for d, t in zip(ORDER, texts):
            found = m.find(t)
            ok(found == {pid for pid, p in enumerate(engine.phrases) if ref_contains(t, p)},
               f"{name}: phrases found in {d} differ from the reference")
            for r in engine.evaluate(found):
                got[rules[r].rule_id].append(d)
        for rid, exp in EXPECT_RULES.items():
            ok(got[rid] == exp, f"{name}: rule {rid} matched {got[rid]}, expected {exp}")
    rng = random.Random(11)
    alphabet = list("abab") + ["é", "É", "İ", "_", "-", ".", "1", "+", " ", "  ", "\n", "\t", " "]
    for round_ in range(6):
        raw = ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 5))) for _ in range(80)]
        phrases = list(dict.fromkeys(p.strip() for p in scan.normalize(raw) if p.strip()))
        docs = scan.normalize(["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40))) for _ in range(1500)])
        ms = {n: scan.MATCHERS[n](phrases) for n in names}
        bad = 0
        for t in docs:
            ref = {i for i, p in enumerate(phrases) if ref_contains(t, p)}
            bad += sum(ms[n].find(t) != ref for n in names)
        ok(bad == 0, f"random round {round_}: {bad} documents where a matcher differs from the reference")


# ------------------------------------------------------------------------------------------- 2. rule validation
def test_validation(work: Path) -> None:
    cases = {
        "unknown key": [{"rule_id": "a", "any": [["x"]]}],
        "rule_id": [{"rule_id": "a b", "all": [["x"]]}],
        "rule_id ": [{"rule_id": "../a", "all": [["x"]]}],
        "group": [{"rule_id": "a", "group": "a//b", "all": [["x"]]}],
        "duplicate": [{"rule_id": "a", "all": [["x"]]}, {"rule_id": "a", "all": [["y"]]}],
        "non-empty": [{"rule_id": "a", "all": [[]]}],
        "empty after": [{"rule_id": "a", "all": [["x"]], "none": ["  \n "]}],
        "needs a directory": [{"rule_id": "a", "group": "g", "all": [["x"]]},
                              {"rule_id": "b", "group": "g.txt/h", "all": [["y"]]}],
    }
    for i, (msg, rows) in enumerate(cases.items()):
        try:
            scan.load_rules(write_jsonl(work / f"bad_{i}.jsonl", rows))
            ok(False, f"rules with a {msg!r} error were accepted")
        except SystemExit as e:
            ok(msg.strip() in str(e), f"error for {msg!r}: {e}")


# ------------------------------------------------------------------------------------------------- 3./4. scan
def read_out(out: Path):
    counts = json.loads((out / "counts.json").read_text())
    groups = {g: (out / "matches" / f"{g}.txt").read_text().split() for g in counts["group_counts"]}
    rules = {r: (out / "rule_matches" / f"{r}.txt").read_text().split() for r in counts["rule_counts"]}
    return counts, groups, rules


def check_full(out: Path, idmap: dict, what: str) -> None:
    counts, groups, rules = read_out(out)
    ok(counts["complete"], f"{what}: counts.json not complete")
    for rid, exp in EXPECT_RULES.items():
        exp = [idmap[d] for d in exp]
        ok(rules[rid] == exp, f"{what}: rule_matches/{rid}.txt is {rules[rid]}, expected {exp}")
        ok(counts["rule_counts"][rid] == len(exp), f"{what}: count of rule {rid}")
    for g, exp in EXPECT_GROUPS.items():
        exp = [idmap[d] for d in exp]
        ok(groups[g] == exp, f"{what}: matches/{g}.txt is {groups[g]}, expected {exp}")
        ok(counts["group_counts"][g] == len(exp), f"{what}: count of group {g}")
    ok(counts["groups"]["demo/q1/h1"] == ["demo/q1/h1/iuu", "demo/q1/h1/cod_quota"], f"{what}: rules of a group")
    ok(not counts["rule_recorded"] and not counts["group_recorded"], f"{what}: nothing should be capped")


def test_scan_jsonl(work: Path, names) -> None:
    corpus = write_jsonl(work / "corpus.jsonl", DOCS)
    rules = write_jsonl(work / "rules.jsonl", RULES)
    for name in names:
        out = work / f"out_jsonl_{name}"
        run(["scan", "--corpus", corpus, "--rules", rules, "--out", out, "--matcher", name, "--batch-rows", "7"])
        run(["merge", "--out", out])
        check_full(out, {d: d for d in ORDER}, f"jsonl/{name}")


def make_parquet(work: Path) -> tuple[Path, dict]:
    import pyarrow as pa
    import pyarrow.parquet as pq
    corpus = work / "parquet"
    corpus.mkdir()
    idmap, split = {}, 13
    for num, part in ((12, DOCS[:split]), (13, DOCS[split:])):
        pq.write_table(pa.table({"text": [d["text"] for d in part]}), corpus / f"shard_{num:05d}.parquet",
                       row_group_size=4)
        for row, d in enumerate(part):
            idmap[d["docid"]] = f"shard_{num:05d}_{row}"
    return corpus, idmap


def test_scan_parquet(work: Path, corpus: Path, idmap: dict, names) -> None:
    rules = write_jsonl(work / "rules.jsonl", RULES)
    for name in names:
        out = work / f"out_parquet_{name}"
        run(["scan", "--corpus", corpus, "--rules", rules, "--out", out, "--matcher", name, "--workers", "2"])
        run(["merge", "--out", out])
        check_full(out, idmap, f"parquet/{name}")
    ok(idmap["demo_doc_207"] == "shard_00012_7", "climbmix ids: shard_<NNNNN>_<row>, the row unpadded")

    out = work / "out_resume"
    run(["scan", "--corpus", corpus, "--rules", rules, "--out", out, "--shards", "12:13"])
    ok(sorted(p.name for p in (out / "shards").glob("*.done.json")) == ["shard_00012.done.json"], "--shards 12:13")
    r = run(["merge", "--out", out], expect=None)
    ok(r.returncode != 0 and "not scanned yet" in r.stderr, "merge refuses a partial scan")
    run(["merge", "--out", out, "--allow-partial"])
    ok(json.loads((out / "counts.json").read_text())["complete"] is False, "a partial merge says complete: false")
    r = run(["scan", "--corpus", corpus, "--rules", rules, "--out", out], expect=None)
    ok(r.returncode != 0 and "--resume" in r.stderr, "a scan refuses to redo a done shard without --resume")
    (out / "shards" / ".shard_00013.matches.jsonl.tmp424242").write_text("partial\n")
    before = (out / "shards" / "shard_00012.done.json").stat().st_mtime_ns
    r = run(["scan", "--corpus", corpus, "--rules", rules, "--out", out, "--resume", "--workers", "2"])
    ok("1 already done" in r.stderr, "--resume skips the done shard")
    ok((out / "shards" / "shard_00012.done.json").stat().st_mtime_ns == before, "--resume leaves a done shard alone")
    ok(not list((out / "shards").glob(".*.tmp*")), "stale temporary files are removed")
    run(["merge", "--out", out])
    check_full(out, idmap, "parquet/resumed")
    changed = write_jsonl(work / "rules_changed.jsonl", RULES[:-1])
    r = run(["scan", "--corpus", corpus, "--rules", changed, "--out", out, "--resume"], expect=None)
    ok(r.returncode != 0 and "rules_digest" in r.stderr, "a resumed scan refuses a changed rules file")

    out = work / "out_capped"
    run(["scan", "--corpus", corpus, "--rules", rules, "--out", out, "--max-per-rule", "1", "--max-per-group", "2"])
    run(["merge", "--out", out])
    counts, groups, rules_ = read_out(out)
    shard_of = {d: idmap[d].rsplit("_", 1)[0] for d in ORDER}
    for kind, exp_all, got, cap, ckey, rkey in (("rule", EXPECT_RULES, rules_, 1, "rule_counts", "rule_recorded"),
                                                ("group", EXPECT_GROUPS, groups, 2, "group_counts", "group_recorded")):
        for k, exp in exp_all.items():
            first = [idmap[d] for s in ("shard_00012", "shard_00013")
                     for d in [d for d in exp if shard_of[d] == s][:cap]]
            ok(counts[ckey][k] == len(exp), f"capped: {kind} {k} count {counts[ckey][k]} must stay exact ({len(exp)})")
            ok(got[k] == first, f"capped: {kind} {k} recorded {got[k]}, expected the first {cap} per shard {first}")
            ok(counts[rkey].get(k, counts[ckey][k]) == len(first), f"capped: {kind} {k} recorded count")


# ------------------------------------------------------------------------------------------------------ 5. check
def test_check(work: Path, corpus: Path, idmap: dict) -> None:
    rules = write_jsonl(work / "rules.jsonl", RULES)
    both = write_jsonl(work / "rules_both.jsonl", RULES + EXTRA)
    padded = idmap["demo_doc_203"].rsplit("_", 1)
    padded = f"{padded[0]}_{int(padded[1]):05d}"                     # the duplicate map's spelling of the same row
    known_rows = [{"group": k["group"], "doc_id": padded if k["doc_id"] == "demo_doc_203" else idmap[k["doc_id"]]}
                  for k in KNOWN]
    known = write_jsonl(work / "known.jsonl", known_rows)
    rep = work / "check1.json"
    r = run(["check", "--rules", rules, "--known", known, "--corpus", corpus, "--json", rep, "--why"], expect=1)
    g = {x["group"]: x for x in json.loads(rep.read_text())["groups"]}
    ok(g["demo/q1/h1"]["passed"] and g["demo/q3/h1"]["passed"], "check: q1 and q3 pass")
    ok(not g["demo/q2/h1"]["passed"] and g["demo/q2/h1"]["missed"] == [idmap["demo_doc_208"]],
       f"check: q2 fails with exactly its missed id ({g['demo/q2/h1']['missed']})")
    ok(f"missed (1): {idmap['demo_doc_208']}" in r.stdout, "check prints the missed ids")
    ok(g["demo/q1/h1"]["docs"][idmap["demo_doc_201"]] == ["demo/q1/h1/iuu", "demo/q1/h1/cod_quota"],
       "check: the rules carrying a document")
    ok(g["demo/q1/h1"]["rule_docs_alone"] == {"demo/q1/h1/iuu": 3, "demo/q1/h1/cod_quota": 0},
       f"check: documents each rule carries alone ({g['demo/q1/h1']['rule_docs_alone']})")
    ok(g["demo/q1/h1"]["docs"].get(padded) is not None, "check reads a zero-padded id as its row")
    ok(g["demo/q2/h1"]["why"][idmap["demo_doc_208"]]["demo/q2/h1/keeper"]["failed_clauses"] == [0],
       "check --why: the clause that found no phrase")
    ok("Taliuuna" not in r.stdout and "Vardequist was" not in r.stdout, "check prints no document text")

    passing = work / "passing.jsonl"
    rep2 = work / "check2.json"
    run(["check", "--rules", both, "--known", known, "--corpus", corpus, "--json", rep2,
         "--write-passing", passing], expect=0)
    s = json.loads(rep2.read_text())["summary"]
    ok(s["groups_passed"] == 3 and s["groups_failed"] == 0, f"check with the second q2 rule: all pass ({s})")
    ok(sorted(s["groups_unchecked"]) == sorted(["demo/q4/h1", "ardune_any", "cxx", "gulf_not_amberly", "ferry"]),
       f"check: groups without known documents are unchecked ({s['groups_unchecked']})")
    kept = [json.loads(line)["rule_id"] for line in passing.read_text().splitlines()]
    ok(kept == ["demo/q1/h1/iuu", "demo/q1/h1/cod_quota", "demo/q2/h1/keeper", "demo/q3/h1/harbour",
                "demo/q2/h1/keepers"], f"--write-passing keeps exactly the passing groups' rules ({kept})")
    ok(json.loads(passing.read_text().splitlines()[0]) == RULES[0], "--write-passing keeps rules as written")
    out = work / "out_passing"
    run(["scan", "--corpus", corpus, "--rules", passing, "--out", out])
    run(["merge", "--out", out])
    counts, groups, _ = read_out(out)
    ok(groups["demo/q2/h1"] == [idmap["demo_doc_207"], idmap["demo_doc_208"]], "the fixed hop scans both documents")

    texts = write_jsonl(work / "texts.jsonl", DOCS)
    known_demo = write_jsonl(work / "known_demo.jsonl", KNOWN + [{"group": "demo/q5/h1", "doc_id": "demo_doc_219"},
                                                                 {"group": "demo/q3/h1", "doc_id": "demo_doc_299"}])
    rep3 = work / "check3.json"
    r = run(["check", "--rules", both, "--known", known_demo, "--texts", texts, "--json", rep3], expect=1)
    g = {x["group"]: x for x in json.loads(rep3.read_text())["groups"]}
    ok(g["demo/q1/h1"]["passed"] and g["demo/q2/h1"]["passed"], "check --texts: q1, q2 pass")
    ok(not g["demo/q5/h1"]["passed"] and "no rule for this group" in r.stdout, "check: a group with no rule fails")
    ok(not g["demo/q3/h1"]["passed"] and g["demo/q3/h1"]["unavailable"] == ["demo_doc_299"]
       and g["demo/q3/h1"]["fraction"] == round(2 / 3, 4), "check: an unreadable known document fails its group")


def main() -> None:
    keep = len(sys.argv) > 1
    work = Path(sys.argv[1]).resolve() if keep else Path(tempfile.mkdtemp(prefix="cmass_scan_"))
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    names = matchers()
    test_matching(work, names)
    test_validation(work)
    test_scan_jsonl(work, names)
    corpus, idmap = make_parquet(work)
    test_scan_parquet(work, corpus, idmap, names)
    test_check(work, corpus, idmap)
    if not keep:
        shutil.rmtree(work)
    if failures:
        sys.exit(f"{failures} scan demo check(s) FAILED")
    print(f"all scan demo checks passed (matchers: {', '.join(names)})")


if __name__ == "__main__":
    main()
