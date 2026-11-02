#!/usr/bin/env python3
"""Stage 3, phase 3: system pooling. Once retrieval systems (here, search agents) have been evaluated, the documents
they surfaced or opened are pooled, as TREC pools participants' runs, and the ones the qrels never judged are judged:
a pooled document is judged only if its text matches one of its question's hop rules (`rules.py`, RULES.md), the kept
ones are added to the phase-2 qrels (phase3_pool/), and their duplicates are expanded as in phase 2
(`dups.py --layer phase3_pool_dups`). The judges see neither the systems nor the matched rule: each candidate is judged
against all live hops with the question's hop standards, by the same tiered review as phase 1.

  pool.py --set S candidates --pool POOL.jsonl --rules RULES.jsonl [--corpus PATH] [--id-scheme climbmix]
                                         [--matcher auto|aho|token] [--skip-unknown]
                                         every pooled pair -> phase3_pool/pool_pairs.jsonl (its status); the pairs never
                                         judged whose text matches a rule of the question -> phase3_pool/candidates.jsonl
  pool.py --set S fetch                  cache the candidates' text (through cmass.corpus)
  pool.py --set S chunks                 render them: phase3_pool/work/QID/chunk_NN_*.md; prints workflow_args
  (judge: workflow phase1.js with args {set, qids, pool: true}, or by hand with
   `qrels.py --pool show|check|second|second-check|compare|adj-check`)
  pool.py --set S status                 where each question stands (qrels_state.py --pool)
  pool.py --set S build                  phase2/ + the kept candidates -> phase3_pool/{qrels.jsonl, qrels_hops.jsonl,
                                         rejections.jsonl, expansion.jsonl, conflicts.jsonl, stats.json, qrels.trec}
  dups.py --set S --layer phase3_pool_dups lookup|fetch|chunks|apply --round N ... build   (as phase 2)
  qrels.py --set S verify --phase phase3_pool_dups
  pool.py --set S stats [--layer phase3_pool_dups]   per system combination: the funnel, and every added qrel
                                         credited to the systems that retrieved it (a duplicate to those of the pooled
                                         document it copies)

Inputs.
  POOL.jsonl   one row per (question, document) any system retrieved: {"query_id", "doc_id", "systems"}, where
               "systems" is {name: {"surfaced": bool, "opened": bool}} (the format of evaluation/export_system_runs.py's
               pools/<dataset>.jsonl) or a list of names. Rows whose question is not in the set are an error unless
               --skip-unknown.
  RULES.jsonl  match rules in scan.py's format, grouped per hop as `<set>/<qid>/<hop>` (`rules.py collect` writes it);
               a group must name a live hop of its question. Questions without rules contribute no candidate.
  --corpus     read the texts from a local corpus as scan.py does (ClimbMix parquet shards, or JSONL) and store them in
               the run's document cache, so the judges read exactly the text the rules matched; without it the texts
               come from the corpus service (slow for large pools).

A pooled pair is `already_qrel` (in the phase-2 qrels), `judged_before` (a verdict in phase 1 or a phase-2 round, or a
phase-2 rejection), `no_rules`, `not_in_corpus`, `unmatched` (no rule of its question matches) or `candidate`.
`candidates` refuses to replace a candidate list that judging has started on with a different one.

What the CMASS release ran: the pools of the three agents evaluated in the paper (GPT-6.1 Sol, Gemma 4 31B, Qwen 3.5
9B), the hop rules written under RULES.md's coverage contract (every hop's rules match all of its established qrels;
breadth cap 20,000 estimated documents), the phase-1 tiers per dataset, and near_dup_mode quote_first for the
duplicates of the additions. Stdlib, plus pyarrow for the rule matcher's normalisation (scan.py).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qrels as Q  # noqa: E402
from qrels import natural_key, read_jsonl, write_json, write_jsonl  # noqa: E402

STATUSES = ("already_qrel", "judged_before", "no_rules", "not_in_corpus", "unmatched", "candidate")


def pool_set(q) -> Q.QSet:
    return Q.QSet(q.run, q.sid, pool=True)


def base_dir(q) -> Path:
    b = q.dir / "phase2"
    if not (b / "qrels_hops.jsonl").exists():
        raise SystemExit(f"no phase-2 qrels in {b}: finish phase 2 (`dups.py --set {q.sid} build`) first")
    return b


def systems_of(row: dict) -> tuple[list[str], bool, bool]:
    """(sorted system names, surfaced by any, opened by any) of a pool row."""
    s = row.get("systems") or {}
    if isinstance(s, list):
        return sorted(map(str, s)), True, False
    if not isinstance(s, dict):
        raise SystemExit(f"pool row {row.get('query_id')} {row.get('doc_id')}: systems must be an object or a list")
    return (sorted(s), any(bool((v or {}).get("surfaced")) for v in s.values()),
            any(bool((v or {}).get("opened")) for v in s.values()))


def combo(systems) -> str:
    return " + ".join(sorted(systems)) or "(none)"


# ----------------------------------------------------------------------------- what was judged before
def judged_before(q) -> set[tuple[str, str]]:
    """(question, document) pairs with a verdict in phase 1 or a phase-2 round, or a phase-2 rejection."""
    out = {(r["id"], r["doc_id"]) for r in read_jsonl(base_dir(q) / "rejections.jsonl")}
    dirs = [q.dir / "judged"] + sorted((q.dir / "phase2").glob("r*/judged"))
    for d in dirs:
        for p in d.glob("*/*.json"):
            try:
                vs = Q.load_verdicts(p)
            except (ValueError, json.JSONDecodeError):
                continue
            out |= {(p.parent.name, v["doc_id"]) for v in vs if isinstance(v, dict) and v.get("doc_id")}
    return out


# ----------------------------------------------------------------------------- candidates
def load_rules(q, path: str, live: dict[str, set[str]]):
    """{qid: [scan.Rule]} for the rules whose group is <set>/<qid>/<hop>, after checking every group names a live hop."""
    import scan
    by_q = defaultdict(list)
    for r in scan.load_rules(path):
        parts = r.group.split("/")
        if len(parts) != 3 or parts[0] != q.sid:
            continue                            # another set's rules
        qid, hop = parts[1], parts[2]
        if qid not in live:
            raise SystemExit(f"{path}: rule {r.rule_id} names question {qid}, not in set {q.sid}")
        if hop not in live[qid]:
            raise SystemExit(f"{path}: rule {r.rule_id} names hop {hop}, not a live factual hop of {qid}")
        by_q[qid].append(r)
    return by_q


def read_texts(q, docs: list[str], a) -> dict[str, str]:
    """The texts the rules are matched against and the judges will read: from a local corpus (stored in the run's
    document cache) or from the cache and the corpus service."""
    corpus = q.corpus
    if a.corpus:
        import scan
        local = scan.fetch_texts(docs, a.corpus, None, scan.ID_SCHEMES[a.id_scheme])
        differs = []
        for d, t in local.items():
            c = corpus.cached_doc(d)
            if c is None:
                corpus.save_doc(d, t)
            elif c != t:
                differs.append(d)
        if differs:
            raise SystemExit(f"{len(differs)} cached documents differ from {a.corpus} (first: {differs[0]}); the judges "
                             f"must read the text the rules matched")
        return local
    out = {}
    for d in docs:
        t = q.text(d)
        if t is not None:
            out[d] = t
    return out


def cmd_candidates(q, a) -> None:
    import scan
    inv = q.inventory()
    base = base_dir(q)
    live = {x: {h["id"] for h in r["hops"] if not h["derivation"]} for x, r in inv.items()}
    qrels = {r["id"]: set(r["qrels"]) for r in read_jsonl(base / "qrels.jsonl")}
    judged = judged_before(q)
    rules = load_rules(q, a.rules, live)
    rows, unknown, seen = [], [], set()
    for r in read_jsonl(Path(a.pool)):
        x, d = str(r.get("query_id") or r.get("id") or ""), r.get("doc_id")
        if not x or not d:
            raise SystemExit(f"{a.pool}: a row without query_id or doc_id")
        if x not in inv:
            unknown.append(x)
            continue
        if (x, d) in seen:
            raise SystemExit(f"{a.pool}: {x} {d} listed twice")
        seen.add((x, d))
        names, surf, opened = systems_of(r)
        status = ("already_qrel" if d in qrels.get(x, ()) else "judged_before" if (x, d) in judged
                  else "no_rules" if x not in rules else "check")
        rows.append({"id": x, "doc_id": d, "systems": names, "surfaced": surf, "opened": opened, "status": status})
    if unknown and not a.skip_unknown:
        raise SystemExit(f"{len(set(unknown))} questions of {a.pool} are not in set {q.sid} (first: {unknown[0]}); "
                         f"pass --skip-unknown to leave them out")
    todo = [r for r in rows if r["status"] == "check"]
    texts = read_texts(q, sorted({r["doc_id"] for r in todo}, key=natural_key), a)
    matcher = scan.MATCHERS[scan.resolve_matcher(a.matcher)]
    by_q = defaultdict(list)
    for r in todo:
        by_q[r["id"]].append(r)
    for x, items in by_q.items():
        eng = scan.Engine(rules[x])
        m = matcher(eng.phrases)
        have = [r for r in items if r["doc_id"] in texts]
        for r in items:
            if r["doc_id"] not in texts:
                r["status"] = "not_in_corpus"
        for r, t in zip(have, scan.normalize([texts[r["doc_id"]] for r in have])):
            groups = sorted({eng.rules[i].group.split("/")[-1] for i in eng.evaluate(m.find(t))}, key=natural_key)
            r["status"] = "candidate" if groups else "unmatched"
            if groups:
                r["groups"] = groups
    pos = {x: i for i, x in enumerate(inv)}
    rows.sort(key=lambda r: (pos[r["id"]], natural_key(r["doc_id"])))
    cands = [{k: r[k] for k in ("id", "doc_id", "systems", "surfaced", "opened", "groups")}
             for r in rows if r["status"] == "candidate"]
    qp = pool_set(q)
    cpath = q.dir / Q.POOL / "candidates.jsonl"
    if cpath.exists():
        old = [(r["id"], r["doc_id"]) for r in read_jsonl(cpath)]
        started = any(qp.work.glob("*/chunks.json"))
        if old != [(r["id"], r["doc_id"]) for r in cands] and started:
            raise SystemExit(f"{cpath} exists with other candidates and judging has started on it "
                             f"({qp.work}); move phase3_pool/ aside to start over")
    write_jsonl(q.dir / Q.POOL / "pool_pairs.jsonl", rows)
    write_jsonl(cpath, cands)
    by_status = Counter(r["status"] for r in rows)
    summ = {"set": q.sid, "pool_pairs": len(rows), **{s: by_status[s] for s in STATUSES},
            "unknown_question_rows": len(unknown), "questions_with_candidates": len({r["id"] for r in cands}),
            "candidates": len(cands), "candidates_opened": sum(r["opened"] for r in cands),
            "by_systems": {c: dict(Counter(r["status"] for r in rows if combo(r["systems"]) == c))
                           for c in sorted({combo(r["systems"]) for r in rows})}}
    write_json(q.dir / Q.POOL / "candidates_summary.json", summ)
    print(json.dumps(summ))


# ----------------------------------------------------------------------------- fetch, chunks, status
def cmd_fetch(q, a) -> None:
    Q.cmd_fetch(pool_set(q), argparse.Namespace(qids=[]))


def cmd_chunks(q, a) -> None:
    qp = pool_set(q)
    qids = list(qp.pool_candidates())
    res = []
    for x in qids:
        if Q.standard_errors(qp, qp.row(x)):
            print(f"warning: {x} has no clean standard (standards/{x}.json); its judges need one", file=sys.stderr)
        res.append(Q.cmd_chunks(qp, argparse.Namespace(qid=x)))
    print(json.dumps({"set": q.sid, "questions": qids, "to_judge": sum(r["to_judge"] + r["already_chunked"] for r in res),
                      "new_chunks": sum(r["n_chunks"] for r in res), "not_in_corpus": sum(r["not_in_corpus"] for r in res),
                      "workflow_args": {"set": q.sid, "qids": qids, "pool": True}}))


def states(q) -> list[dict]:
    import qrels_state
    qp = pool_set(q)
    return [qrels_state.state(qp, x) for x in qp.pool_candidates()]


def cmd_status(q, a) -> None:
    st = states(q)
    for s in st:
        if a.verbose or not s["complete"]:
            print(json.dumps({"qid": s["qid"], "chunks": len(s["chunks"]), "blocking": s["blocking"]}))
    print(json.dumps({"set": q.sid, "questions": len(st), "complete": sum(s["complete"] for s in st),
                      "incomplete": {s["qid"]: s["blocking"] for s in st if not s["complete"]}}))


# ----------------------------------------------------------------------------- build
def cmd_build(q, a) -> None:
    st = {s["qid"]: s for s in states(q)}
    bad = {x: s["blocking"] for x, s in st.items() if not s["complete"]}
    if bad:
        for x, why in bad.items():
            print(f"{x}: judging incomplete: {why}")
        raise SystemExit(f"{len(bad)} questions incomplete; finish them first (pool.py status)")
    qp = pool_set(q)
    base = base_dir(q)
    cands = qp.pool_candidates()
    order = [r["id"] for r in read_jsonl(base / "qrels.jsonl")]
    qrels = {r["id"]: dict(r, qrels=list(r["qrels"])) for r in read_jsonl(base / "qrels.jsonl")}
    hops = {r["id"]: r for r in read_jsonl(base / "qrels_hops.jsonl")}
    base_sets = {x: set(r["qrels"]) for x, r in qrels.items()}
    rejections = [dict(r) if "phase" in r else dict(r, phase=1) for r in read_jsonl(base / "rejections.jsonl")]
    old_stats = json.loads((base / "stats.json").read_text()).get("questions", {}) if (base / "stats.json").exists() else {}
    expansion, stats = [], {}
    for x in order:
        n = Counter()
        if x in cands:
            row = qp.row(x)
            live = {h["id"] for h in row["hops"]}
            first, adj = qp.first_verdicts(x), qp.adjudicated(x)
            second = {d for m in qp.rounds(x).values() for ch in m["chunks"].values() for d in ch}
            hm = {h["id"]: h for h in hops[x]["hops"]}
            for d in qp.to_judge(row):
                c, v = cands[x][d], adj.get(d) or first.get(d)
                if v is None:
                    raise SystemExit(f"{x}: {d} not judged")
                src = "adjudicated" if d in adj else "judged"
                if v.get("keep"):
                    if d in base_sets[x]:
                        raise SystemExit(f"{x}: {d} is already a phase-2 qrel; it should never have been a candidate")
                    qrels[x]["qrels"].append(d)
                    got = {}
                    for h in v["hops"]:
                        hid = str(h["hop"])
                        if hid not in live:
                            raise SystemExit(f"{x}: {d} cites hop {hid}, not a live hop")
                        got.setdefault(hid, {"supports": h["supports"], "quote": h["quote"]})
                        hm[hid]["docs"].setdefault(d, {"supports": h["supports"], "source": "system_pool",
                                                       "quotes": [h["quote"]], "systems": c["systems"]})
                    expansion.append({"id": x, "doc_id": d, "source": "system_pool", "systems": c["systems"],
                                      "surfaced": c["surfaced"], "opened": c["opened"], "groups": c["groups"],
                                      "verdict_source": src, "second_judged": d in second,
                                      "hops": dict(sorted(got.items(), key=lambda kv: natural_key(kv[0])))})
                    n["kept"] += 1
                else:
                    rejections.append({"id": x, "doc_id": d, "reason": v.get("reason"), "note": v.get("note"),
                                       "provenance": [f"system_pool:{s}" for s in c["systems"]], "groups": c["groups"],
                                       "adjudicated": d in adj, "second_judged": d in second, "phase": 3,
                                       "layer": Q.POOL})
                    n["dropped"] += 1
                    n[f"drop_{v.get('reason')}"] += 1
                n["adjudicated"] += d in adj
                n["second_judged"] += d in second
            n["candidates"] = len(cands[x])
            n["not_in_corpus"] = len(qp.missing(x))
        qrels[x]["qrels"] = sorted(qrels[x]["qrels"], key=natural_key)
        for h in hops[x]["hops"]:
            h["docs"] = dict(sorted(h["docs"].items(), key=lambda kv: natural_key(kv[0])))
        stats[x] = {"base": len(base_sets[x]), "kept_total": len(qrels[x]["qrels"]), **dict(n),
                    "uncovered_hops": (old_stats.get(x) or {}).get("uncovered_hops", [])}
    if any(not base_sets[x] <= set(qrels[x]["qrels"]) for x in qrels):
        raise SystemExit("phase3_pool is not a superset of phase 2")
    out = q.dir / Q.POOL
    for f in ("qrels.jsonl", "qrels_hops.jsonl", "rejections.jsonl", "stats.json"):
        (out / f).unlink(missing_ok=True)        # rebuilt in full from phase 2 and the verdicts
    Q.write_outputs(q, out, list(qrels.values()), list(hops.values()), rejections, stats, order)
    write_jsonl(out / "expansion.jsonl", expansion)
    write_jsonl(out / "conflicts.jsonl", read_jsonl(base / "conflicts.jsonl"))    # so the next lookup sees them
    s = json.loads((out / "stats.json").read_text())
    n_base = sum(map(len, base_sets.values()))
    s["pool"] = {"base": "phase2", "base_pairs": n_base, "pairs": n_base + len(expansion), "added": len(expansion),
                 "candidates": sum(map(len, cands.values())), "questions_with_candidates": len(cands),
                 "questions_with_additions": len({e["id"] for e in expansion}),
                 "added_by_system": dict(Counter(sys_ for e in expansion for sys_ in e["systems"]))}
    write_json(out / "stats.json", s)
    print(json.dumps(s["pool"]))


# ----------------------------------------------------------------------------- per-system statistics
def cmd_stats(q, a) -> None:
    """The funnel per combination of systems (pooled, already qrels, judged before, rule-matched, kept), and every qrel
    the final layer adds over phase 2 credited to the systems that retrieved it: a pooled addition to its own systems,
    a duplicate to those of the pooled document it copies (traced through earlier duplicate rounds, even when that
    original was later removed)."""
    pdir = q.dir / Q.POOL
    pairs = read_jsonl(pdir / "pool_pairs.jsonl")
    if not pairs:
        raise SystemExit(f"no {pdir / 'pool_pairs.jsonl'}: run `pool.py candidates` first")
    final = q.dir / a.layer
    if not (final / "qrels.jsonl").exists():
        raise SystemExit(f"no {final / 'qrels.jsonl'}: build {a.layer} first")
    origin = {(r["id"], r["doc_id"]): combo(r["systems"]) for r in pairs}        # every pooled pair, kept or not
    added = {(r["id"], r["doc_id"]) for r in read_jsonl(pdir / "expansion.jsonl")}
    funnel = defaultdict(Counter)
    for r in pairs:
        c = combo(r["systems"])
        funnel[c]["pooled"] += 1
        funnel[c][r["status"]] += 1
        funnel[c]["kept"] += (r["id"], r["doc_id"]) in added
    dups = read_jsonl(final / "expansion.jsonl") if a.layer != Q.POOL else []
    for d in sorted(dups, key=lambda d: d.get("round", 1)):        # later rounds copy earlier duplicates
        origin[(d["id"], d["doc_id"])] = origin.get((d["id"], d.get("parent")), "unattributed")
    p2 = {(r["id"], d) for r in read_jsonl(base_dir(q) / "qrels.jsonl") for d in r["qrels"]}
    fin = {(r["id"], d) for r in read_jsonl(final / "qrels.jsonl") for d in r["qrels"]}
    new = fin - p2
    credit = defaultdict(Counter)
    for k in new:
        kind = "pooled_kept" if k in added else "duplicate"
        credit[origin.get(k, "unattributed")][kind] += 1
    names = sorted({s for r in pairs for s in r["systems"]})
    any_sys = {s: Counter() for s in names}
    for c, v in credit.items():
        for s in c.split(" + "):
            if s in any_sys:
                any_sys[s].update(v)
    res = {"set": q.sid, "layer": a.layer, "phase2_pairs": len(p2), "pairs": len(fin), "added": len(new),
           "removed_from_phase2": len(p2 - fin), "unattributed": sum(credit.get("unattributed", Counter()).values()),
           "funnel": {c: dict(v) for c, v in sorted(funnel.items())},
           "new_qrels": {c: dict(v, total=sum(v.values())) for c, v in sorted(credit.items())},
           "new_qrels_any": {s: dict(v, total=sum(v.values())) for s, v in any_sys.items()}}
    write_json(pdir / f"systems_{a.layer}.json", res)
    print(json.dumps(res))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run")
    ap.add_argument("--set", dest="sid")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("candidates")
    s.add_argument("--pool", required=True, help="the system pool: JSONL of query_id, doc_id, systems")
    s.add_argument("--rules", required=True, help="match rules, groups <set>/<qid>/<hop> (rules.py collect)")
    s.add_argument("--corpus", help="a local corpus to read the texts from (scan.py's --corpus)")
    s.add_argument("--id-scheme", default="climbmix", help="scan.py's id scheme for a parquet --corpus")
    s.add_argument("--matcher", default="auto", choices=("auto", "aho", "token"))
    s.add_argument("--skip-unknown", action="store_true", help="leave out rows whose question is not in the set")
    sp.add_parser("fetch")
    sp.add_parser("chunks")
    s = sp.add_parser("status"); s.add_argument("-v", "--verbose", action="store_true")
    sp.add_parser("build")
    s = sp.add_parser("stats"); s.add_argument("--layer", default="phase3_pool_dups", choices=(Q.POOL, "phase3_pool_dups"))
    a = ap.parse_args()
    run = Q.Run(a.run)
    q = Q.QSet(run, a.sid or Q.default_set(run))
    {"candidates": cmd_candidates, "fetch": cmd_fetch, "chunks": cmd_chunks, "status": cmd_status, "build": cmd_build,
     "stats": cmd_stats}[a.cmd](q, a)


if __name__ == "__main__":
    main()
