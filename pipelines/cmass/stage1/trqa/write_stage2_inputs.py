#!/usr/bin/env python3
"""End of Stage 1: the Stage 2 inputs, in the record format of cmass/records.py.

  python3 write_stage2_inputs.py [--run RUN] [--set trqa] [--partial]

  inputs/<set>.jsonl               the gate survivors (stage1/<set>/final_list.jsonl; 50 in the real run)
  inputs/<set>_near_misses.jsonl   the near misses: every strict-audited question (strict_list.jsonl, 87) that
                                   is not a survivor, i.e. it failed exactly one of the four gates (37 in the real run)

(The paths come from cmass.json when the sets <set> and nm_<set> are configured there.)

Near misses carry the hops as they stood after the strict audit (composite snippets, not re-verified), and
what killed them: failed_gate (annotator, coherence, completeness, extreme), stage_rank (1 = died at the last
gate ... 4 = at the first), the gate agent's reason (first 600 characters), and for completeness the missed
members {entity, changes_answer, doc_id} and recomputed_answer, for extreme the ungrounded_hops and
uncovered_atoms. They are sorted by (stage_rank, id), as the real near-miss list was (private repo, branch
hotpotqa-climbmix, artifacts/near_miss/build_near_miss.py). PARTIAL and NOT projections and the strict
audit's rejects are not near misses.

Every record: record_id, question, answer (the gold value, a string), answer_type "aggregate", hops [{clue,
doc_id, supporting_doc_ids, snippet, kind evidence | derivation}] (TRQA's Stage 1 never marked hops
redundant, so `redundant` is absent), source (the TRQA id), and the dataset fields subset, operator,
target_attribute, entity_set_description (the projector's reading). No hints.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from cmass.run import Run, read_jsonl, write_jsonl  # noqa: E402
from trqa_state import GATES, Stage1, state  # noqa: E402

RANK = {"annotator": 1, "coherence": 2, "completeness": 3, "extreme": 4}
DATASET_FIELDS = ("subset", "operator", "target_attribute", "entity_set_description")


def out_path(run: Run, sid: str, default: str) -> Path:
    s = run.config.sets.get(sid)
    return run.root / (s["input"] if s else default)


def record(rec: dict, task: dict, hops: list[dict]) -> dict:
    return {"record_id": rec["record_id"], "question": rec["question"], "answer": str(rec["gold_answer"]),
            "answer_type": "aggregate", "hops": hops, "source": task.get("source"),
            **{k: rec.get(k) for k in DATASET_FIELDS}}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run")
    p.add_argument("--set", default="trqa")
    p.add_argument("--partial", action="store_true", help="write although some strict-audited questions are still in the gates")
    a = p.parse_args()
    run = Run(a.run)
    s1 = Stage1(run, a.set)
    tasks = s1.tasks()
    strict = read_jsonl(s1.list_file("strict_list.jsonl"))
    final = read_jsonl(s1.list_file("final_list.jsonl"))
    if not strict:
        raise SystemExit("no strict_list.jsonl: run build_strict_list.py first")
    fin_ids = {r["record_id"] for r in final}

    main_rows = [record(r, tasks[r["record_id"]],
                        [{"clue": h["clue"], "doc_id": h["doc_id"], "supporting_doc_ids": h["supporting_doc_ids"],
                          "snippet": h["snippet"], "kind": h["kind"]} for h in r["hops"]]) for r in final]

    near, pending = [], []
    for r in strict:
        q = r["record_id"]
        if q in fin_ids:
            continue
        st = state(s1, q)
        if st["next"] != "done" or st["stage"] not in GATES or st["outcome"] != "dropped":
            pending.append(q)
            continue
        gate, run_file = st["stage"], s1.load(st["stage"], q)
        hops = []
        for h in r["hops"]:
            hop = {"clue": h["clue"], "doc_id": (h.get("supporting_doc_ids") or [""])[0],
                   "supporting_doc_ids": h.get("supporting_doc_ids") or [], "kind": h["kind"]}
            for k in ("snippet", "value"):          # a backfilled hop has a value and no snippet
                if k in h:
                    hop[k] = h[k]
            hops.append(hop)
        rec = record(r, tasks[q], hops) | {"failed_gate": gate, "stage_rank": RANK[gate],
                                            "reason": str(run_file.get("reason") or "")[:600]}
        if gate == "completeness":
            rec["missed_members"] = [{"entity": m.get("entity"), "changes_answer": m.get("changes_answer"),
                                      "doc_id": m.get("doc_id")} for m in (run_file.get("extra_members") or [])]
            rec["recomputed_answer"] = run_file.get("recomputed_answer")
        if gate == "extreme":
            rec["ungrounded_hops"] = run_file.get("ungrounded_hops")
            rec["uncovered_atoms"] = run_file.get("uncovered_atoms")
        near.append(rec)
    if pending and not a.partial:
        raise SystemExit(f"{len(pending)} strict-audited question(s) have not finished the gates (e.g. {pending[:3]}); "
                         "run stage1_final_gates.js, or pass --partial")
    near.sort(key=lambda x: (x["stage_rank"], str(x["record_id"])))
    p_main = out_path(run, a.set, f"inputs/{a.set}.jsonl")
    p_near = out_path(run, f"nm_{a.set}", f"inputs/{a.set}_near_misses.jsonl")
    write_jsonl(p_main, main_rows)
    write_jsonl(p_near, near)
    print(f"wrote {len(main_rows)} candidates to {p_main}")
    print(f"wrote {len(near)} near misses to {p_near}: {dict(Counter(x['failed_gate'] for x in near))}")
    if pending:
        print(f"({len(pending)} still in the gates, left out)")


if __name__ == "__main__":
    main()
