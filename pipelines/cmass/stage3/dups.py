#!/usr/bin/env python3
"""Stage 3, phase 2: duplicate expansion, the deterministic part. A retriever that returns a copy of a judged document
should not be scored as wrong because the copy's id was never pooled.

  dups.py --set S lookup --round N     duplicates of the kept documents -> qrels/S/phase2/pairs_rN.jsonl (+ _summary.json)
  dups.py --set S fetch --round N      cache the text of every new pair and its parent (through cmass.corpus)
  dups.py --set S chunks --round N     render the new near duplicates for verification: phase2/rN/work/QID/chunk_NN_*.md
  (judge them: workflow phase2.js, or by hand with `qrels.py --phase2 N show|check|second|compare|adj-check`)
  dups.py --set S apply --round N      exact duplicates added, verified near duplicates added -> phase2/added_rN.jsonl
  dups.py --set S lookup --round N+1   ... until a round has no new pair (closure)
  dups.py --set S build                phase2/{qrels.jsonl, qrels_hops.jsonl, rejections.jsonl, expansion.jsonl,
                                       conflicts.jsonl, stats.json, qrels.trec}; then `qrels.py verify --phase phase2`
  dups.py --set S status               where each round stands

Rules (the paper's duplicate expansion):
  exact duplicate  byte-identical to a kept document (the map groups texts by SHA-256 over text.strip(); apply checks
                   the two texts are equal): added without reading, with exactly its parent's hops and support labels.
  near duplicate   5-gram word-shingle Jaccard >= threshold (0.7) with a kept document: added only after an agent has
                   verified, on the duplicate's own text, that it supports a live hop (same verdict format and tiered
                   review as phase 1); being similar to its parent proves nothing.
  closure          round N+1 looks up the duplicates of what round N added.
A duplicate already in the question's qrels is `already_kept`; one the judges dropped for that question (phase 1 or an
earlier round) is `judged_dropped` and its verdict stands; a duplicate reachable from several parents is listed once,
with the closest parent (exact first, then highest Jaccard). An exact duplicate that was judged and dropped, or whose
text is not identical to its parent's, is not added; it is listed in conflicts for a reviewer.

The duplicate map (run config, "duplicates"): the `corpus_duplicates` config of the Hugging Face dataset
castorini/cmass, one row per document that has any duplicate: doc_id, exact_duplicates [doc_id], near_duplicates
[{doc_id, jaccard}] (Jaccard >= 0.7, highest first), n_exact, n_near. A document with no duplicate has no row.

  "duplicates": {"path": "/data/cmass/corpus_duplicates",   # a directory of parquet files, one .parquet file, or a
                                                           # .json / .jsonl file of the same rows (no pyarrow needed)
                 "threshold": 0.7,
                 "id_pad": 5,                              # the map writes the row of shard_<shard>_<row> zero-padded to
                 "id_regex": "^(shard_\\d+)_(\\d+)$"}       # 5 digits, the corpus service unpadded; null: ids as they are
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qrels as Q  # noqa: E402
from qrels import natural_key, read_jsonl, write_json, write_jsonl  # noqa: E402


# ----------------------------------------------------------------------------- the duplicate map
class DupMap:
    def __init__(self, run):
        cfg = run.config.data.get("duplicates") or {}
        if not cfg.get("path"):
            raise SystemExit('no "duplicates": {"path": ...} in the run config (a local copy of corpus_duplicates)')
        p = Path(cfg["path"])
        self.path = p if p.is_absolute() else run.root / p
        if not self.path.exists():
            raise SystemExit(f"duplicate map not found: {self.path}")
        self.threshold = float(cfg.get("threshold", 0.7))
        self.pad = cfg.get("id_pad")
        self.rx = re.compile(cfg.get("id_regex") or r"^(shard_\d+)_(\d+)$")

    # Two spellings of one id: the map pads the row (shard_00012_00345), the corpus service does not (shard_00012_345).
    # Look up in the map's spelling, write back in the corpus's; an unnormalised join silently finds fewer duplicates.
    def to_map(self, d: str) -> str:
        m = self.rx.match(d) if self.pad else None
        return f"{m.group(1)}_{int(m.group(2)):0{int(self.pad)}d}" if m else d

    def to_corpus(self, d: str) -> str:
        m = self.rx.match(d) if self.pad else None
        return f"{m.group(1)}_{int(m.group(2))}" if m else d

    def _raw_rows(self, wanted: set[str]) -> list[dict]:
        p = self.path
        if p.is_file() and p.suffix in (".json", ".jsonl"):
            rows = json.loads(p.read_text()) if p.suffix == ".json" else read_jsonl(p)
            return [r for r in rows if r.get("doc_id") in wanted]
        files = sorted(_glob.glob(str(p / "*.parquet"))) if p.is_dir() else [str(p)]
        if not files:
            raise SystemExit(f"no parquet files under {p}")
        try:
            import pyarrow as pa
            import pyarrow.dataset as pads
        except ImportError:
            raise SystemExit("reading the parquet duplicate map needs pyarrow (pip install pyarrow), or point "
                             "duplicates.path at a .json / .jsonl copy of the rows")
        dset = pads.dataset(files, format="parquet")
        # filter before materialising: one list-of-struct row per corpus document is gigabytes as Python objects
        tab = dset.to_table(columns=["doc_id", "exact_duplicates", "near_duplicates"],
                            filter=pads.field("doc_id").isin(pa.array(sorted(wanted))))
        return tab.to_pylist()

    def rows(self, doc_ids) -> dict[str, dict]:
        """{doc_id (corpus spelling): {"exact": [ids], "near": [(id, jaccard)]}} for the documents with any duplicate."""
        back = {self.to_map(d): d for d in doc_ids}
        out = {}
        for r in self._raw_rows(set(back)):
            d = back[r["doc_id"]]
            out[d] = {"exact": [self.to_corpus(x) for x in r.get("exact_duplicates") or []],
                      "near": [(self.to_corpus(n["doc_id"]), round(float(n["jaccard"]), 4))
                               for n in r.get("near_duplicates") or [] if float(n["jaccard"]) >= self.threshold]}
        return out


# ----------------------------------------------------------------------------- state across rounds
def p1_out(q) -> Path:
    out = q.dir / "phase1"
    if not (out / "qrels_hops.jsonl").exists():
        raise SystemExit(f"no phase-1 qrels in {out}: run `qrels.py --set {q.sid} build` first")
    return out


def rounds_on_disk(q) -> list[int]:
    return sorted(int(m.group(1)) for p in q.p2_dir.glob("pairs_r*.jsonl")
                  if (m := re.fullmatch(r"pairs_r(\d+)\.jsonl", p.name)))


def kept_so_far(q, upto: int) -> dict[str, dict[str, dict]]:
    """{qid: {doc: {hop: info}}}: phase-1 hop maps plus every document added in rounds before `upto`."""
    kept = defaultdict(lambda: defaultdict(dict))
    for r in read_jsonl(p1_out(q) / "qrels_hops.jsonl"):
        for h in r["hops"]:
            for d, info in h["docs"].items():
                kept[r["id"]][d][h["id"]] = info
    for r in read_jsonl(p1_out(q) / "qrels.jsonl"):
        for d in r["qrels"]:
            kept[r["id"]].setdefault(d, {})
    for k in range(1, upto):
        for x in read_jsonl(q.p2_dir / f"added_r{k}.jsonl"):
            for hid, info in x["hops"].items():
                kept[x["id"]][x["doc_id"]][hid] = info
    return kept


def settled_elsewhere(q, upto: int) -> tuple[set, set]:
    """(question, doc) pairs judged and dropped (phase 1 and earlier rounds), and earlier conflicts."""
    dropped = {(r["id"], r["doc_id"]) for r in read_jsonl(p1_out(q) / "rejections.jsonl")}
    conflicts = set()
    for k in range(1, upto):
        dropped |= {(r["id"], r["doc_id"]) for r in read_jsonl(q.p2_dir / f"rejected_r{k}.jsonl")}
        conflicts |= {(r["id"], r["doc_id"]) for r in read_jsonl(q.p2_dir / f"conflicts_r{k}.jsonl")}
    return dropped, conflicts


def parent_hops(info: dict[str, dict]) -> dict[str, dict]:
    return {hid: {"supports": v.get("supports"), "quotes": list(v.get("quotes") or [])}
            for hid, v in sorted(info.items(), key=lambda kv: natural_key(kv[0]))}


# ----------------------------------------------------------------------------- lookup
def cmd_lookup(q, a) -> None:
    if a.round > 1 and not (q.p2_dir / f"added_r{a.round - 1}.jsonl").exists():
        raise SystemExit(f"round {a.round - 1} is not applied yet: run `apply --round {a.round - 1}` first")
    dm = DupMap(q.run)
    kept = kept_so_far(q, a.round)
    dropped, conflicts = settled_elsewhere(q, a.round)
    if a.round == 1:
        parents = [(x, d) for x, docs in kept.items() for d in docs]
    else:
        parents = [(r["id"], r["doc_id"]) for r in read_jsonl(q.p2_dir / f"added_r{a.round - 1}.jsonl")]
    rows = dm.rows({d for _, d in parents})
    best = {}
    for x, d in parents:
        row = rows.get(d)
        if not row:
            continue
        cands = [(e, "exact", None) for e in row["exact"]] + [(n, "near", j) for n, j in row["near"]]
        for dup, kind, jac in cands:
            if dup == d:
                continue
            key = (x, dup)
            rank = (0 if kind == "exact" else 1, -(jac or 1.0), natural_key(d))
            if key in best and rank >= best[key][0]:
                continue
            status = ("already_kept" if dup in kept[x] else "judged_dropped" if key in dropped
                      else "conflict" if key in conflicts else "new")
            best[key] = (rank, {"id": x, "doc_id": dup, "parent": d, "kind": kind, "jaccard": jac, "status": status,
                                "parent_hops": parent_hops(kept[x][d])})
    pos = {r["id"]: i for i, r in enumerate(read_jsonl(p1_out(q) / "qrels.jsonl"))}
    out = sorted((v for _, v in best.values()),
                 key=lambda r: (pos.get(r["id"], len(pos)), r["kind"], natural_key(r["doc_id"])))
    write_jsonl(q.p2_dir / f"pairs_r{a.round}.jsonl", out)
    summ = Counter(f"{r['kind']}_{r['status']}" for r in out)
    s = {"set": q.sid, "round": a.round, "parents": len(parents), "parents_with_duplicates": len(rows),
         "pairs": dict(sorted(summ.items())), "new_exact": summ["exact_new"], "new_near": summ["near_new"],
         "questions_with_new_pairs": len({r["id"] for r in out if r["status"] == "new"}),
         "closure": not (summ["exact_new"] or summ["near_new"])}
    write_json(q.p2_dir / f"pairs_r{a.round}_summary.json", s)
    print(json.dumps(s))


# ----------------------------------------------------------------------------- fetch, chunks
def round_set(q, rnd: int):
    return Q.QSet(q.run, q.sid, rnd)


def near_qids(qr) -> list[str]:
    return list(dict.fromkeys(x["id"] for x in qr.pairs() if x["kind"] == "near" and x["status"] == "new"))


def cmd_fetch(q, a) -> None:
    qr = round_set(q, a.round)
    Q.cmd_fetch(qr, argparse.Namespace(qids=list(dict.fromkeys(x["id"] for x in qr.pairs() if x["status"] == "new"))))


def cmd_chunks(q, a) -> None:
    qr = round_set(q, a.round)
    qids = near_qids(qr)
    res = []
    for x in qids:
        if Q.standard_errors(qr, qr.row(x)):
            print(f"warning: {x} has no clean standard (standards/{x}.json); its judges need one", file=sys.stderr)
        res.append(Q.cmd_chunks(qr, argparse.Namespace(qid=x)))
    write_json(q.p2_dir / f"r{a.round}" / "qids.json", qids)
    print(json.dumps({"set": q.sid, "round": a.round, "questions": qids,
                      "near_docs": sum(r["to_judge"] + r["already_chunked"] for r in res),
                      "new_chunks": sum(r["n_chunks"] for r in res), "not_in_corpus": sum(r["not_in_corpus"] for r in res),
                      "workflow_args": {"set": q.sid, "round": a.round, "qids": qids}}))


# ----------------------------------------------------------------------------- apply
def cmd_apply(q, a) -> None:
    import qrels_state

    qr = round_set(q, a.round)
    pairs = qr.pairs()
    incomplete = {}
    for x in near_qids(qr):
        s = qrels_state.state(qr, x)
        if not s["complete"]:
            incomplete[x] = s["blocking"]
    if incomplete and not a.allow_partial:
        for x, why in incomplete.items():
            print(f"{x}: verification incomplete: {why}")
        raise SystemExit(f"round {a.round}: {len(incomplete)} questions incomplete; finish them or pass --allow-partial")
    added, rejected, conflicts, pending = [], [], [], []
    verdicts = {}
    for x in pairs:
        key = (x["id"], x["doc_id"])
        base = {"id": x["id"], "doc_id": x["doc_id"], "parent": x["parent"], "kind": x["kind"], "jaccard": x["jaccard"],
                "round": a.round}
        if x["kind"] == "exact":
            if x["status"] == "judged_dropped":
                pt, dt = q.text(x["parent"]), q.text(x["doc_id"])
                conflicts.append(base | {"issue": "exact_duplicate_judged_dropped",
                                         "identical": pt is not None and dt is not None and pt.strip() == dt.strip()})
                continue
            if x["status"] != "new":
                continue
            pt, dt = q.text(x["parent"]), q.text(x["doc_id"])
            if pt is None or dt is None:
                conflicts.append(base | {"issue": "text_not_found"})
            elif pt.strip() != dt.strip():
                conflicts.append(base | {"issue": "not_identical"})
            elif not x["parent_hops"]:
                conflicts.append(base | {"issue": "parent_has_no_hops"})
            else:
                added.append(base | {"source": "exact_dup", "hops": {
                    hid: {"supports": v["supports"], "source": "exact_dup", "parent": x["parent"], "quotes": v["quotes"]}
                    for hid, v in x["parent_hops"].items()}})
            continue
        if x["status"] != "new":
            continue
        if x["doc_id"] in qr.missing(x["id"]):
            conflicts.append(base | {"issue": "text_not_found"})
            continue
        if x["id"] not in verdicts:
            verdicts[x["id"]] = (qr.final_verdicts(x["id"]), qr.adjudicated(x["id"]))
        final, adj = verdicts[x["id"]]
        v = final.get(x["doc_id"])
        if v is None or x["id"] in incomplete:
            pending.append(key)
            continue
        if v.get("keep"):
            added.append(base | {"source": "adjudicated" if x["doc_id"] in adj else "judged", "hops": {
                str(h["hop"]): {"supports": h["supports"], "source": "near_dup", "parent": x["parent"],
                                "jaccard": x["jaccard"], "quotes": [h["quote"]]} for h in v["hops"]}})
        else:
            rejected.append(base | {"reason": v.get("reason"), "note": v.get("note"), "adjudicated": x["doc_id"] in adj})
    write_jsonl(q.p2_dir / f"added_r{a.round}.jsonl", added)
    write_jsonl(q.p2_dir / f"rejected_r{a.round}.jsonl", rejected)
    write_jsonl(q.p2_dir / f"conflicts_r{a.round}.jsonl", conflicts)
    s = {"set": q.sid, "round": a.round, "exact_added": sum(r["kind"] == "exact" for r in added),
         "near_added": sum(r["kind"] == "near" for r in added), "near_rejected": len(rejected),
         "conflicts": dict(Counter(c["issue"] for c in conflicts)), "pending_unjudged": len(pending),
         "next": f"dups.py --set {q.sid} lookup --round {a.round + 1}"}
    write_json(q.p2_dir / f"apply_r{a.round}_summary.json", s)
    print(json.dumps(s))


# ----------------------------------------------------------------------------- build
def cmd_build(q, a) -> None:
    rnds = rounds_on_disk(q)
    if not rnds:
        raise SystemExit(f"no lookup round in {q.p2_dir}: run `lookup --round 1` first")
    last = rnds[-1]
    last_new = [x for x in read_jsonl(q.p2_dir / f"pairs_r{last}.jsonl") if x["status"] == "new"]
    applied = [k for k in rnds if (q.p2_dir / f"added_r{k}.jsonl").exists()]
    closure = not last_new
    if not closure and not a.allow_open:
        nxt = (f"apply --round {last}" if last not in applied else f"lookup --round {last + 1}")
        raise SystemExit(f"closure not reached: round {last} has {len(last_new)} new pairs; run `{nxt}` "
                         f"(or build with --allow-open)")
    p1 = p1_out(q)
    order = [r["id"] for r in read_jsonl(p1 / "qrels.jsonl")]
    qrels = {r["id"]: dict(r, qrels=list(r["qrels"])) for r in read_jsonl(p1 / "qrels.jsonl")}
    hops = {r["id"]: r for r in read_jsonl(p1 / "qrels_hops.jsonl")}
    p1_sets = {x: set(r["qrels"]) for x, r in qrels.items()}
    expansion, rejections = [], [dict(r, phase=1) for r in read_jsonl(p1 / "rejections.jsonl")]
    conflicts = []
    per_round = {}
    for k in applied:
        added = read_jsonl(q.p2_dir / f"added_r{k}.jsonl")
        rej = read_jsonl(q.p2_dir / f"rejected_r{k}.jsonl")
        conf = read_jsonl(q.p2_dir / f"conflicts_r{k}.jsonl")
        for x in added:
            if x["id"] not in qrels:
                raise SystemExit(f"round {k}: {x['id']} is not in the phase-1 qrels")
            if x["doc_id"] not in qrels[x["id"]]["qrels"]:
                qrels[x["id"]]["qrels"].append(x["doc_id"])
            hm = {h["id"]: h for h in hops[x["id"]]["hops"]}
            for hid, info in x["hops"].items():
                if hid not in hm:
                    raise SystemExit(f"round {k}: {x['id']} {x['doc_id']} names hop {hid}, not a live hop")
                hm[hid]["docs"].setdefault(x["doc_id"], info)
            expansion.append({kk: v for kk, v in x.items() if kk != "hops"} | {"hops": sorted(x["hops"], key=natural_key)})
        rejections += [dict(r, phase=2, provenance=[f"{r['kind']}_dup_of:{r['parent']}"]) for r in rej]
        conflicts += conf
        per_round[str(k)] = {"exact_added": sum(r["kind"] == "exact" for r in added),
                             "near_added": sum(r["kind"] == "near" for r in added), "near_rejected": len(rej),
                             "conflicts": len(conf)}
    for r in qrels.values():
        r["qrels"] = sorted(r["qrels"], key=natural_key)
    for h in hops.values():
        for hop in h["hops"]:
            hop["docs"] = dict(sorted(hop["docs"].items(), key=lambda kv: natural_key(kv[0])))
    not_superset = [x for x in p1_sets if not p1_sets[x] <= set(qrels[x]["qrels"])]
    if not_superset:
        raise SystemExit(f"phase 2 is not a superset of phase 1 for {not_superset}")
    out = q.p2_dir
    old_stats = json.loads((p1 / "stats.json").read_text()).get("questions", {}) if (p1 / "stats.json").exists() else {}
    stats = {}
    for x, r in qrels.items():
        add = [e for e in expansion if e["id"] == x]
        stats[x] = {"phase1": len(p1_sets[x]), "kept": len(r["qrels"]), "exact_added": sum(e["kind"] == "exact" for e in add),
                    "near_added": sum(e["kind"] == "near" for e in add),
                    "uncovered_hops": (old_stats.get(x) or {}).get("uncovered_hops", [])}
    for f in ("qrels.jsonl", "qrels_hops.jsonl", "rejections.jsonl"):
        (out / f).unlink(missing_ok=True)       # rebuilt in full from phase 1 and every round
    Q.write_outputs(q, out, list(qrels.values()), list(hops.values()), rejections, stats, order)
    write_jsonl(out / "expansion.jsonl", expansion)
    write_jsonl(out / "conflicts.jsonl", conflicts)
    s = json.loads((out / "stats.json").read_text())
    s["expansion"] = {"rounds": rnds, "applied": applied, "closure": closure, "per_round": per_round,
                      "phase1_pairs": sum(len(v) for v in p1_sets.values()),
                      "phase2_pairs": sum(len(r["qrels"]) for r in qrels.values()),
                      "exact_added": sum(e["kind"] == "exact" for e in expansion),
                      "near_added": sum(e["kind"] == "near" for e in expansion), "conflicts": len(conflicts)}
    write_json(out / "stats.json", s)
    print(json.dumps(s["expansion"]))


# ----------------------------------------------------------------------------- status
def cmd_status(q, a) -> None:
    import qrels_state

    for k in rounds_on_disk(q):
        summ = json.loads((q.p2_dir / f"pairs_r{k}_summary.json").read_text())
        qr = round_set(q, k)
        st = [qrels_state.state(qr, x) for x in near_qids(qr)] if (q.p2_dir / f"r{k}").exists() else []
        print(json.dumps({"round": k, "pairs": summ["pairs"], "closure": summ["closure"],
                          "chunked": (q.p2_dir / f"r{k}" / "qids.json").exists(),
                          "verified": sum(s["complete"] for s in st), "questions": len(near_qids(qr)),
                          "incomplete": {s["qid"]: s["blocking"] for s in st if not s["complete"]},
                          "applied": (q.p2_dir / f"added_r{k}.jsonl").exists()}))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run")
    ap.add_argument("--set", dest="sid")
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("lookup", "fetch", "chunks", "apply"):
        s = sp.add_parser(name)
        s.add_argument("--round", type=int, default=1)
        if name == "apply":
            s.add_argument("--allow-partial", action="store_true", help="leave unverified near duplicates out")
    s = sp.add_parser("build")
    s.add_argument("--allow-open", action="store_true", help="build although the last round still has new pairs")
    sp.add_parser("status")
    a = ap.parse_args()
    run = Q.Run(a.run)
    q = Q.QSet(run, a.sid or Q.default_set(run))
    {"lookup": cmd_lookup, "fetch": cmd_fetch, "chunks": cmd_chunks, "apply": cmd_apply, "build": cmd_build,
     "status": cmd_status}[a.cmd](q, a)


if __name__ == "__main__":
    main()
