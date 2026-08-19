#!/usr/bin/env python3
"""Step 5: expand the qrels over the corpus duplicate graph.

A judged document usually has twins in the corpus. If one copy is relevant its
copies are too, so leaving them unjudged makes a retriever look wrong for
returning an identical document. Two edge types, handled differently:

  exact duplicates - identical text. Inherit the parent's hops with no reading.
  near duplicates  - Jaccard >= threshold. NOT inherited: a near-duplicate can
                     drop the very sentence that grounded a hop, so each one is
                     read and judged on its own, against ALL live hops of the
                     query rather than only the parent's.

Run to closure: accepting a document adds its own neighbours to the frontier,
so `scan` -> read -> `apply` repeats until `scan` reports nothing pending.

  scan    find undecided neighbours of the current qrels and queue them
  status  how much is pending, per query
  render  print the queued documents in full, for reading
  apply   record decisions for one query and update the qrels

Usage:
  python3 step5_expand_duplicates.py scan   --qrels work/qrels.jsonl
  python3 step5_expand_duplicates.py status
  python3 step5_expand_duplicates.py render --qid 78 --max-chars 4500
  python3 step5_expand_duplicates.py apply  --qid 78 --decisions d78.json

decisions file: {"<doc_id>": {"hops": [0, 2], "why": "..."}}   hops [] = reject.

DOCUMENT ID NORMALISATION
-------------------------
The corpus analysis artifacts zero-pad the sequence part of a document id
(shard_00045_06622) while the retrieval API and the qrels do not
(shard_00045_6622). Joining the two without normalising silently finds nothing
for every id whose sequence is under five digits - it does not error, it just
returns fewer duplicates than exist. Both spellings are normalised on every
join here; do not remove `_ana` / `_api`.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

import config

_ID = re.compile(r"^(shard_\d+)_(\d+)$")


def _ana(doc_id: str) -> str:
    """Analysis-artifact spelling: sequence zero-padded to five digits."""
    m = _ID.match(doc_id)
    return f"{m.group(1)}_{int(m.group(2)):05d}" if m else doc_id


def _api(doc_id: str) -> str:
    """Retrieval/qrels spelling: sequence with no leading zeros."""
    m = _ID.match(doc_id)
    return f"{m.group(1)}_{int(m.group(2))}" if m else doc_id


def _state_path(p) -> Path:
    return p["expand"] / "state.json"


def _load_state(p) -> dict:
    return json.loads(_state_path(p).read_text())


def _save_state(p, state) -> None:
    _state_path(p).parent.mkdir(parents=True, exist_ok=True)
    _state_path(p).write_text(json.dumps(state))


# ---------------------------------------------------------------- scan
def scan_exact(path: str, wanted: set[str]) -> dict[str, list[str]]:
    """Group id -> members, for every exact-duplicate group touching `wanted`.

    One pass, set intersection per line. Testing membership substring-wise
    instead turns this into hours on a corpus-sized file.
    """
    groups: dict[str, list[str]] = {}
    want_ana = {_ana(d) for d in wanted}
    with open(path) as fh:
        for line in fh:
            if not line.strip():
                continue
            ids = json.loads(line).get("doc_ids") or []
            if want_ana.intersection(ids):
                members = [_api(x) for x in ids]
                groups[members[0]] = members
    return groups


def scan_near(parquet_dir: str, wanted: set[str], threshold: float) -> dict[str, dict]:
    """doc -> {"near": [{doc_id, jaccard}], "exact": [doc_id]} for `wanted`."""
    import pyarrow.parquet as pq

    want = {_ana(d): d for d in wanted}
    out: dict[str, dict] = {}
    files = sorted(glob.glob(str(Path(parquet_dir) / "*.parquet")))
    if not files:
        raise SystemExit(f"no parquet files under {parquet_dir}")
    for n, path in enumerate(files, 1):
        ids = pq.read_table(path, columns=["doc_id"]).column("doc_id").to_pylist()
        idx = [i for i, d in enumerate(ids) if d in want]
        if not idx:
            continue
        # .take() BEFORE to_pylist(): materialising a corpus-sized list-of-struct
        # column costs minutes per shard and gigabytes of memory.
        sub = pq.read_table(path, columns=["near_duplicates", "exact_duplicates"]).take(idx)
        near = sub.column("near_duplicates").to_pylist()
        exact = sub.column("exact_duplicates").to_pylist()
        for j, i in enumerate(idx):
            out[want[ids[i]]] = {
                "near": [{"doc_id": _api(e["doc_id"]), "jaccard": e["jaccard"]}
                         for e in near[j] if e["jaccard"] >= threshold],
                "exact": [_api(x) for x in exact[j]],
            }
        if n % 50 == 0:
            print(f"  {n}/{len(files)} shards, {len(out)} matched", file=sys.stderr)
    return out


def cmd_scan(args) -> None:
    p = config.paths(args)
    p["expand"].mkdir(parents=True, exist_ok=True)

    if _state_path(p).exists():
        state = _load_state(p)
    else:
        state = {"queries": {}, "threshold": args.threshold}
        for line in Path(args.qrels).read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            state["queries"][str(r["qid"])] = {
                "included": {d: list(h) for d, h in r["qrels"].items()},
                "skipped": [h["index"] for h in r["hops"] if h.get("skipped")],
                "decided": {}, "checked": [], "pending": [],
            }

    included = {d for q in state["queries"].values() for d in q["included"]}
    unchecked = {d for q in state["queries"].values()
                 for d in q["included"] if d not in set(q["checked"])}
    print(f"qrels documents {len(included)}, unscanned {len(unchecked)}")
    if not unchecked:
        print("nothing to scan; closure reached")
        return

    if args.exact_dups:
        groups = scan_exact(args.exact_dups, unchecked)
        added = 0
        for q in state["queries"].values():
            for doc, hops in list(q["included"].items()):
                for g in groups.values():
                    if doc in g:
                        for other in g:
                            if other != doc and other not in q["included"]:
                                q["included"][other] = sorted(hops)
                                added += 1
        print(f"exact duplicates inherited: {added} document(s)")

    neigh = scan_near(args.near_dup_dir, unchecked, state["threshold"])
    total = 0
    for q in state["queries"].values():
        inc, dec = set(q["included"]), set(q["decided"])
        best: dict[str, dict] = {}
        for parent in list(q["included"]):
            if parent in set(q["checked"]) or parent not in neigh:
                continue
            for e in neigh[parent]["near"]:
                d = e["doc_id"]
                if d in inc or d in dec:
                    continue
                if d not in best or e["jaccard"] > best[d]["jaccard"]:
                    best[d] = {"doc_id": d, "parent": parent, "jaccard": e["jaccard"]}
        q["pending"] = sorted(best.values(), key=lambda c: -c["jaccard"])
        total += len(q["pending"])
    _save_state(p, state)
    print(f"queued {total} candidate(s) across "
          f"{sum(1 for q in state['queries'].values() if q['pending'])} query/queries")
    if total:
        ids = sorted({c["doc_id"] for q in state["queries"].values() for c in q["pending"]})
        out = p["expand"] / "fetch_ids.json"
        out.write_text(json.dumps(ids))
        print(f"wrote {out} - cache them with: step1_fetch_docs.py --ids-file {out}")


# ---------------------------------------------------------------- status
def cmd_status(args) -> None:
    state = _load_state(config.paths(args))
    rows = [(len(q["pending"]), qid) for qid, q in state["queries"].items() if q["pending"]]
    print(f"pending {sum(n for n, _ in rows)} across {len(rows)} query/queries")
    for n, qid in sorted(rows, reverse=True):
        print(f"  {qid:>6}  {n}")


# ---------------------------------------------------------------- render
def cmd_render(args) -> None:
    p = config.paths(args)
    state = _load_state(p)
    q = state["queries"][args.qid]
    qrels = {json.loads(l)["qid"]: json.loads(l)
             for l in Path(args.qrels).read_text().splitlines() if l.strip()} if args.qrels else {}
    meta = qrels.get(args.qid) or qrels.get(int(args.qid)) if qrels else None

    if meta:
        print(f"QID {args.qid}  {meta['question']}\nANSWER: {meta['answer']}\n")
        for h in meta["hops"]:
            mark = "  [skipped]" if h.get("skipped") else ""
            print(f"  h{h['index']} ({h['hop_type']}){mark}: {h['clue']}")
        print()
    print(f"{len(q['pending'])} candidate(s)\n" + "=" * 72)
    for c in q["pending"]:
        path = p["doc_cache"] / f"{c['doc_id']}.json"
        text = (json.loads(path.read_text()).get("doc") if path.exists()
                else None) or "<NOT CACHED - run step1_fetch_docs.py --ids-file>"
        print(f"\n{c['doc_id']}  parent={c['parent']} (hops "
              f"{q['included'].get(c['parent'])})  jaccard={c['jaccard']:.2f}")
        print(f"    length={len(text)} chars")
        print(text[:args.max_chars])
        if len(text) > args.max_chars:
            print(f"    ... TRUNCATED at {args.max_chars}; raise --max-chars before judging")
        print("-" * 72)


# ---------------------------------------------------------------- apply
def cmd_apply(args) -> None:
    p = config.paths(args)
    state = _load_state(p)
    q = state["queries"][args.qid]
    decisions = json.loads(Path(args.decisions).read_text())
    pending = {c["doc_id"]: c for c in q["pending"]}

    missing = [d for d in pending if d not in decisions]
    if missing:
        raise SystemExit(f"no decision for {len(missing)} candidate(s): {missing[:5]}")

    skipped = set(q["skipped"])
    accepted = rejected = 0
    for doc, verdict in decisions.items():
        hops = [h for h in verdict.get("hops", []) if h not in skipped]
        q["decided"][doc] = {"hops": hops, "why": verdict.get("why", ""),
                             "parent": pending[doc]["parent"],
                             "jaccard": pending[doc]["jaccard"]}
        if not hops:
            rejected += 1
            continue
        accepted += 1
        q["included"][doc] = sorted(set(q["included"].get(doc, [])) | set(hops))

    q["checked"] = sorted(set(q["checked"]) | {c["parent"] for c in q["pending"]})
    q["pending"] = []
    _save_state(p, state)
    print(f"qid {args.qid}: accepted {accepted}, rejected {rejected} | "
          f"{len(q['included'])} documents now included")


def cmd_export(args) -> None:
    """Write the expanded qrels back out in the step-4 schema."""
    p = config.paths(args)
    state = _load_state(p)
    out = Path(args.out)
    with out.open("w") as fh:
        for line in Path(args.qrels).read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            inc = state["queries"][str(r["qid"])]["included"]
            r["qrels"] = {d: sorted(h) for d, h in sorted(inc.items())}
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(sp):
        config.add_work_dir(sp)
        return sp

    sp = common(sub.add_parser("scan", help="queue undecided duplicate neighbours"))
    sp.add_argument("--qrels", required=True, help="step-4 qrels JSONL (seeds the state)")
    sp.add_argument("--near-dup-dir", default=config.NEAR_DUP_DIR,
                    help="parquet dir of near-duplicate edges (env QRELS_NEAR_DUP_DIR)")
    sp.add_argument("--exact-dups", default=config.EXACT_DUPS,
                    help="JSONL of exact-duplicate groups (env QRELS_EXACT_DUPS)")
    sp.add_argument("--threshold", type=float, default=0.7)
    sp.set_defaults(func=cmd_scan)

    sp = common(sub.add_parser("status", help="pending counts per query"))
    sp.set_defaults(func=cmd_status)

    sp = common(sub.add_parser("render", help="print queued documents in full"))
    sp.add_argument("--qid", required=True)
    sp.add_argument("--qrels", help="step-4 qrels JSONL, to print the hops alongside")
    sp.add_argument("--max-chars", type=int, default=4500)
    sp.set_defaults(func=cmd_render)

    sp = common(sub.add_parser("apply", help="record decisions for one query"))
    sp.add_argument("--qid", required=True)
    sp.add_argument("--decisions", required=True)
    sp.set_defaults(func=cmd_apply)

    sp = common(sub.add_parser("export", help="write the expanded qrels JSONL"))
    sp.add_argument("--qrels", required=True, help="step-4 qrels JSONL to rewrite")
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_export)

    args = parser.parse_args()
    if args.cmd == "scan":
        config.require(args.near_dup_dir, "--near-dup-dir", "QRELS_NEAR_DUP_DIR")
    args.func(args)


if __name__ == "__main__":
    main()
