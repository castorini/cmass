#!/usr/bin/env python3
"""After the strict audit: the verdict table, the strict-audited question list and the inputs of the four gates.

  python3 build_strict_list.py [--run RUN] [--set trqa] [--partial]

Writes, under stage1/<set>/:
  strict_verdicts.jsonl  one row per audited question: {stem, subset, strict_keep, constraints_covered,
                         all_grounded, aggregate_ok, complete}, strict_keep = the audit's keep
  strict_list.jsonl      the kept questions (87 in the real run), sorted by id: {record_id, subset, question,
                         gold_answer, operator, target_attribute, entity_set_description, n_hops, hops}; each hop
                         from the audit file: a hop citing documents is {clue, kind "evidence",
                         supporting_doc_ids, snippet (first 300 characters)}, a hop citing none is
                         {clue, kind "derivation", supporting_doc_ids [], snippet}
  gate_inputs.jsonl      the same questions as the gates read them: hops numbered from 1, {index, kind, clue,
                         doc_ids, snippet ("" when none)}

MANUAL STEP (it happened once in the real run): a kept question whose audit file wrote its hops in another
shape (no `clue`) cannot be converted. The script stops and names it; a person writes
stage1/<set>/strict_backfill/<qid>.json = {"hops": [{"clue", "kind": "evidence", "supporting_doc_ids": [...],
"value": "<attribute value>"}, ...]} from the projector's evidence for that question (one hop per enumerated
member: a clue naming the member and its attribute value, the member's cited document, the value; no snippet),
and reruns. The backfilled hops replace the audit's hops verbatim.

Reconstructed: the private repo has the outputs (artifacts/trqa/strict_audit/strict_verdicts.jsonl,
strict_keep_stems.json, projected_questions_strict.jsonl, and final_pipeline/inputs/) but not the scripts;
these rules reproduce all of them exactly from the audit files and the one backfill.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from cmass.run import Run, read_jsonl, write_jsonl  # noqa: E402
from trqa_state import Stage1, needs_backfill, validate  # noqa: E402

CHECKS = ("constraints_covered", "all_grounded", "aggregate_ok", "complete")
TOP = ("record_id", "subset", "question", "gold_answer", "operator", "target_attribute", "entity_set_description")


def hops_from_audit(audit: dict) -> list[dict]:
    out = []
    for h in audit.get("hops") or []:
        docs = h.get("doc_ids") or []
        if docs:
            out.append({"clue": h["clue"], "kind": "evidence", "supporting_doc_ids": docs,
                        "snippet": (h.get("snippet") or "")[:300]})
        else:
            out.append({"clue": h["clue"], "kind": "derivation", "supporting_doc_ids": [], "snippet": h.get("snippet")})
    return out


def load_backfill(path: Path) -> list[dict]:
    d = json.loads(path.read_text())
    hops = d.get("hops") if isinstance(d, dict) else None
    if not isinstance(hops, list) or not hops:
        raise SystemExit(f"{path}: needs {{\"hops\": [...]}}")
    for i, h in enumerate(hops, 1):
        if not isinstance(h, dict) or not str(h.get("clue") or "").strip() or h.get("kind") not in ("evidence", "derivation"):
            raise SystemExit(f"{path}: hop {i} needs a clue and kind evidence / derivation")
        if h["kind"] == "evidence" and not h.get("supporting_doc_ids"):
            raise SystemExit(f"{path}: evidence hop {i} needs at least one supporting document")
    return hops


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run")
    p.add_argument("--set", default="trqa")
    p.add_argument("--partial", action="store_true", help="build from the audits that exist")
    a = p.parse_args()
    s1 = Stage1(Run(a.run), a.set)
    inputs = {r["stem"]: r for r in read_jsonl(s1.list_file("strict_inputs.jsonl"))}
    if not inputs:
        raise SystemExit("no strict_inputs.jsonl: run build_strict_inputs.py first")
    verdicts, kept, missing, need = [], [], [], []
    for stem in sorted(inputs):
        errs, _ = validate(s1, "strict", stem)
        if errs:
            missing.append(stem)
            continue
        audit, inp = s1.load("strict", stem), inputs[stem]
        verdicts.append({"stem": stem, "subset": inp["subset"], "strict_keep": audit["keep"],
                         **{k: audit[k] for k in CHECKS}})
        if not audit["keep"]:
            continue
        if needs_backfill(audit):
            bf = s1.backfill_file(stem)
            if not bf.exists():
                need.append(stem)
                continue
            hops = load_backfill(bf)
        else:
            hops = hops_from_audit(audit)
        rec = {"record_id": stem, **{k: inp[k] for k in TOP[1:]}, "n_hops": len(hops), "hops": hops}
        if any(h["kind"] == "evidence" and not h.get("supporting_doc_ids") for h in hops):
            raise SystemExit(f"{stem}: an evidence hop has no supporting document")
        kept.append(rec)
    if missing and not a.partial:
        raise SystemExit(f"{len(missing)} strict audit(s) missing or invalid (e.g. {missing[:3]}); "
                         "run stage1_strict_audit.js or pass --partial")
    if need:
        raise SystemExit("MANUAL STEP: these kept questions wrote hops without the {clue, grounded, doc_ids, snippet} "
                         "shape; hand-write stage1/<set>/strict_backfill/<qid>.json for each (see this script's "
                         f"docstring and README.md), then rerun: {need}")
    gate = [{**{k: r[k] for k in TOP},
             "hops": [{"index": i, "kind": h["kind"], "clue": h["clue"], "doc_ids": h.get("supporting_doc_ids") or [],
                       "snippet": h.get("snippet") or ""} for i, h in enumerate(r["hops"], 1)]} for r in kept]
    write_jsonl(s1.list_file("strict_verdicts.jsonl"), verdicts)
    write_jsonl(s1.list_file("strict_list.jsonl"), kept)
    write_jsonl(s1.list_file("gate_inputs.jsonl"), gate)
    rej = [v for v in verdicts if not v["strict_keep"]]
    print(f"strict audit: {len(kept)} of {len(verdicts)} kept" + (f" ({len(missing)} not audited yet)" if missing else ""))
    print("dropped, by failed check (a question can fail several): "
          + ", ".join(f"{k}={sum(1 for v in rej if not v[k])}" for k in CHECKS))
    subsets = sorted({v["subset"] for v in verdicts})
    print("kept by subset: " + ", ".join(f"{s} {sum(1 for r in kept if r['subset'] == s)}/"
                                          f"{sum(1 for v in verdicts if v['subset'] == s)}" for s in subsets))
    print(f"wrote strict_verdicts.jsonl, strict_list.jsonl and gate_inputs.jsonl under {s1.dir}")


if __name__ == "__main__":
    main()
