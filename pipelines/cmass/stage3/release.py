#!/usr/bin/env python3
"""Stage 3: write one set's qrels in the obfuscated release format of the Hugging Face dataset castorini/cmass, the
format scripts/deobfuscate.py decodes. Four views of the same judgments, one row per (question, document):

  qrels.jsonl            {"query_id", "iteration": "0", "doc_id", "relevance": 1, "canary"}
  qrel_roles.jsonl       the same rows plus "role": "inclusion" | "exclusion"
  qrels_inclusion.jsonl  the rows whose role is inclusion
  qrels_exclusion.jsonl  the rows whose role is exclusion

query_id and doc_id are base64 over the UTF-8 bytes XORed with the repeated SHA-256 digest of the row's canary. A
document's role is exclusion only if every hop it supports is an exclusion hop (it rules out a rival); a document that
supports any inclusion hop is inclusion, so the two views partition the qrels. Questions follow the order of the
released queries file (or of the layer's qrels.jsonl), documents are sorted within a question, and each row is
json.dumps with its default separators.

  release.py --set S [--layer phase3_pool_dups] --out DIR (--queries RELEASED_QUERIES.jsonl | --canary TEXT)
             [--hop-roles ROLES.jsonl]

--queries takes the canary and the question order from the split's released queries.jsonl, so a new qrels version
keeps the canary and order of the questions it accompanies. --hop-roles overrides the roles of qrels_hops.jsonl with
JSONL rows {"id", "hop", "role"}. Every written file is decoded again and checked against the layer before the tool
exits. The output is benchmark data: write it outside this repository.

The CMASS release v2026.10.25 was written this way from phase3_pool_dups, with the hop roles of the paper's role
analysis; rebuilt from phase 2, the same procedure reproduces every qrel file of v2026.10.20 byte for byte.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qrels as Q  # noqa: E402
from qrels import natural_key, read_jsonl  # noqa: E402

VIEWS = ("qrels", "qrel_roles", "qrels_inclusion", "qrels_exclusion")


def _key(canary: str) -> bytes:
    return hashlib.sha256(canary.encode("utf-8")).digest()


def encode(s: str, canary: str) -> str:
    k = _key(canary)
    return base64.b64encode(bytes(c ^ k[i % len(k)] for i, c in enumerate(s.encode("utf-8")))).decode("ascii")


def decode(v: str, canary: str) -> str:
    k = _key(canary)
    return bytes(c ^ k[i % len(k)] for i, c in enumerate(base64.b64decode(v, validate=True))).decode("utf-8")


def roles(hops_rows: list[dict], override: dict) -> dict[tuple[str, str], str]:
    """{(qid, doc): role}: exclusion only if every hop the document supports is an exclusion hop."""
    out = {}
    for r in hops_rows:
        per_doc = {}
        for h in r["hops"]:
            role = override.get((r["id"], str(h["id"])), h.get("role") or "inclusion")
            if role not in ("inclusion", "exclusion"):
                raise SystemExit(f"{r['id']} hop {h['id']}: role {role!r} is not inclusion or exclusion")
            for d in h["docs"]:
                per_doc.setdefault(d, []).append(role)
        for d, rs in per_doc.items():
            out[(r["id"], d)] = "exclusion" if all(x == "exclusion" for x in rs) else "inclusion"
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run")
    ap.add_argument("--set", dest="sid")
    ap.add_argument("--layer", default="phase3_pool_dups", choices=Q.LAYERS)
    ap.add_argument("--out", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--queries", help="the split's released queries.jsonl: canary and question order")
    src.add_argument("--canary", help="the canary string, with the layer's question order")
    ap.add_argument("--hop-roles", help="JSONL {id, hop, role} overriding the hop roles of qrels_hops.jsonl")
    a = ap.parse_args()
    run = Q.Run(a.run)
    q = Q.QSet(run, a.sid or Q.default_set(run))
    layer = q.dir / a.layer
    qrels = {r["id"]: r["qrels"] for r in read_jsonl(layer / "qrels.jsonl")}
    if not qrels:
        raise SystemExit(f"no {layer / 'qrels.jsonl'}: build the {a.layer} layer first")
    if a.queries:
        rows = read_jsonl(Path(a.queries))
        canaries = {r.get("canary") for r in rows}
        if len(canaries) != 1 or not next(iter(canaries)):
            raise SystemExit(f"{a.queries}: expected one canary for the whole split, found {len(canaries)}")
        canary = canaries.pop()
        order = [decode(r["id"], canary) for r in rows]
    else:
        canary, order = a.canary, list(qrels)
    if set(order) != set(qrels):
        raise SystemExit(f"the questions differ: {len(set(order) - set(qrels))} released without qrels, "
                         f"{len(set(qrels) - set(order))} with qrels but not released")
    override = {(str(r["id"]), str(r["hop"])): r["role"] for r in read_jsonl(Path(a.hop_roles))} if a.hop_roles else {}
    role = roles(read_jsonl(layer / "qrels_hops.jsonl"), override)
    views = {v: [] for v in VIEWS}
    for x in order:
        for d in sorted(qrels[x]):
            if (x, d) not in role:
                raise SystemExit(f"{x} {d}: in qrels.jsonl but supports no hop in qrels_hops.jsonl")
            row = {"query_id": encode(x, canary), "iteration": "0", "doc_id": encode(d, canary), "relevance": 1,
                   "canary": canary}
            views["qrels"].append(row)
            views["qrel_roles"].append(row | {"role": role[(x, d)]})
            views[f"qrels_{role[(x, d)]}"].append(row)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for v, rs in views.items():
        (out / f"{v}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rs))
    # decode what was written and compare with the layer
    back = {v: [(decode(r["query_id"], r["canary"]), decode(r["doc_id"], r["canary"]), r.get("role"))
                for r in map(json.loads, (out / f"{v}.jsonl").read_text().splitlines())] for v in VIEWS}
    pairs = {(x, d) for x, d, _ in back["qrels"]}
    assert pairs == {(x, d) for x, ds in qrels.items() for d in ds} and len(pairs) == len(back["qrels"])
    assert [(x, d) for x, d, _ in back["qrel_roles"]] == [(x, d) for x, d, _ in back["qrels"]]
    inc, exc = ({(x, d) for x, d, _ in back[v]} for v in ("qrels_inclusion", "qrels_exclusion"))
    assert inc | exc == pairs and not inc & exc
    assert all(role[(x, d)] == r for x, d, r in back["qrel_roles"])
    print(json.dumps({"set": q.sid, "layer": a.layer, "out": str(out), "questions": len(order), "qrels": len(pairs),
                      "inclusion": len(inc), "exclusion": len(exc),
                      "sha256": {v: hashlib.sha256((out / f"{v}.jsonl").read_bytes()).hexdigest() for v in VIEWS}}))


if __name__ == "__main__":
    main()
