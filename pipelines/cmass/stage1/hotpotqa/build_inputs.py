#!/usr/bin/env python3
"""End of HotpotQA Stage 1: write the Stage 2 inputs from the gate outputs (deterministic).

  build_inputs.py [--run RUN] SET [--near-miss-set NM_SET]

Candidates (a port of pipeline/survivors.py and pipeline/build_final.py): every selected question whose
annotator output keeps it and whose HotpotQA type is bridge (hpqa_state.py: next "done"). Its hops are the
extreme audit's hops, in index order. The annotator's repairs are applied under the guard: a "fix" is
taken only when its document is one the extreme audit verified for that hop and its snippet occurs
verbatim (hpqa_common.verbatim_any) across those documents; then its clue replaces the hop's clue too.
Otherwise the hop keeps the extreme audit's evidence ("fix-rejected"). Every released snippet is then
verified against the documents it cites; documents not in the run's cache are fetched (the extreme audit
may have found replacements by searching, outside the screen's candidate documents). An unverified
snippet is reported, not dropped, as in the original; investigate it before Stage 2.

Near misses (a port of near_miss/build_near_miss.py): every bridge question that reached a gate and was
dropped there. A question stops at the first gate it fails, so each failed exactly one; stage_rank orders
how late (1 = annotator, 2 = coherence, 3 = uniqueness, 4 = extreme). Each carries the dropping agent's
reason, the extreme audit's hops as they stood, and gate detail: for uniqueness the rivals, the corpus
answer and failure_kind (answer-multiplicity: the evidence does not single out the gold answer;
stale-or-unsupported-gold: the corpus contradicts the gold answer; both); for extreme the ungrounded hops
and uncovered atoms.

Writes the inputs of the two Stage 2 sets, in the record format of cmass/records.py:
  inputs/<set>.jsonl                 (or the set's configured input)
  inputs/<set>_near_misses.jsonl     (or the configured input of NM_SET, default nm_<set>)
and stage1/<set>/build_report.json (hop outcomes, snippet verification, counts).
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hpqa_common import file, load_json, natural_key, run_of, tasks_by_id, verbatim_any, write_json, write_jsonl  # noqa: E402
from hpqa_state import states  # noqa: E402
from cmass.corpus import Corpus  # noqa: E402

RANK = {"annotator": 1, "coherence": 2, "uniqueness": 3, "extreme": 4}


def answer_of(gold: list):
    return gold[0] if isinstance(gold, list) and len(gold) == 1 else gold


def base_record(task: dict, gin: dict, st: dict) -> dict:
    gold = gin.get("gold_answers") or task.get("gold_answers") or [task["answer"]]
    return {"record_id": st["qid"], "question": gin.get("question") or task["question"], "answer": answer_of(gold),
            "answer_type": "single", "gold_answers": gold, "hops": [], "hints": task.get("hints") or [],
            "source": task.get("source"), "hotpot_type": task.get("hotpot_type"),
            "provenance": {"origin": st["origin"], "batch": st["batch"] or None},
            "candidate_doc_ids": list(gin.get("doc_ids") or [])}


def hop_record(eh: dict, clue: str, doc_id: str, snippet: str, allowed) -> dict:
    return {"index": eh["index"], "clue": clue, "is_bridge": bool(eh.get("is_bridge")), "doc_id": doc_id,
            "supporting_doc_ids": sorted(allowed), "snippet": snippet, "kind": "fact", "redundant": False}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="run directory (default: $CMASS_RUN or the current directory)")
    ap.add_argument("set")
    ap.add_argument("--near-miss-set", help="the near-miss set of cmass.json (default nm_<set>)")
    a = ap.parse_args()
    run = run_of(a.run)
    sid = a.set
    run.config.set(sid)
    nm_sid = a.near_miss_set or f"nm_{sid}"
    nm_path = run.input_file(nm_sid) if nm_sid in run.config.sets else run.root / "inputs" / f"{sid}_near_misses.jsonl"
    tasks = tasks_by_id(run, sid)
    corpus = Corpus(run)
    texts = {}

    def text(d):
        if d not in texts:
            texts[d] = corpus.get_document(d) or ""
        return texts[d]

    out, near, outcomes, unverified = [], [], Counter(), []
    for st in states(run, sid):
        q = st["qid"]
        gin = load_json(file(run, sid, "gate_input", q)) or {}
        E = load_json(file(run, sid, "extreme", q)) or {}
        if st["next"] == "done":                                     # annotator keep and bridge
            A = load_json(file(run, sid, "annotator", q)) or {}
            rec = base_record(tasks[q], gin, st)
            e_hops = {h["index"]: h for h in E.get("hops") or []}
            a_hops = {h["index"]: h for h in A.get("hops") or []}
            for i in sorted(e_hops):
                eh, ah = e_hops[i], a_hops.get(i, {})
                allowed = set(eh.get("doc_ids") or []) | ({eh["doc_id"]} if eh.get("doc_id") else set())
                clue, docid, snip = eh.get("clue") or "", eh.get("doc_id") or "", eh.get("snippet") or ""
                if ah.get("verdict") == "fix":
                    nd, ns = (ah.get("doc_id") or docid), (ah.get("snippet") or snip)
                    if nd in allowed and verbatim_any(ns, [text(d) for d in sorted(allowed)]):
                        docid, snip = nd, ns
                        if ah.get("clue"):
                            clue = ah["clue"]
                        outcomes["fix-applied"] += 1
                    else:
                        outcomes["fix-rejected"] += 1
                else:
                    outcomes[ah.get("verdict", "keep")] += 1
                if not verbatim_any(snip, [text(d) for d in sorted(allowed)]):
                    unverified.append({"qid": q, "hop": i, "doc_id": docid, "snippet": snip[:150]})
                rec["hops"].append(hop_record(eh, clue, docid, snip, allowed))
            rec["n_hops"] = len(rec["hops"])
            out.append(rec)
        elif st["next"] == "dropped" and st["stage"] in RANK and st["hotpot_type"] == "bridge":
            gate = st["stage"]
            G = load_json(file(run, sid, gate, q)) or {}
            rec = base_record(tasks[q], gin, st)
            for h in E.get("hops") or []:
                allowed = set(h.get("doc_ids") or []) | ({h["doc_id"]} if h.get("doc_id") else set())
                r = hop_record(h, h.get("clue") or "", h.get("doc_id") or "", h.get("snippet") or "", allowed)
                r.update(grounded=bool(h.get("grounded")), note=h.get("note") or "")
                rec["hops"].append(r)
            rec.update(n_hops=len(rec["hops"]), failed_gate=gate, stage_rank=RANK[gate],
                       reason=str(G.get("reason") or "")[:600])
            if gate == "uniqueness":
                rec.update(answer_unique=G.get("answer_unique"), gold_supported=G.get("gold_supported"),
                           corpus_answer=G.get("corpus_answer"),
                           rivals=[{"candidate": x.get("candidate"), "why_not_excluded": x.get("why_not_excluded")}
                                   for x in G.get("rivals") or []],
                           failure_kind=("stale-or-unsupported-gold" if (G.get("answer_unique") and not G.get("gold_supported"))
                                         else "answer-multiplicity" if (not G.get("answer_unique") and G.get("gold_supported"))
                                         else "both"))
            if gate == "extreme":
                rec.update(ungrounded_hops=G.get("ungrounded_hops"), uncovered_atoms=G.get("uncovered_atoms"))
            near.append(rec)

    out.sort(key=lambda r: natural_key(r["record_id"]))
    near.sort(key=lambda r: (r["stage_rank"], str(r["record_id"])))
    write_jsonl(run.input_file(sid), out)
    write_jsonl(nm_path, near)
    nh = sum(r["n_hops"] for r in out)
    docs = {d for r in out for h in r["hops"] for d in h["supporting_doc_ids"]}
    report = {"candidates": len(out), "near_misses": len(near),
              "near_misses_by_gate": dict(Counter(r["failed_gate"] for r in near)),
              "uniqueness_failure_kinds": dict(Counter(r["failure_kind"] for r in near if r.get("failure_kind"))),
              "by_origin": dict(Counter(f"{r['provenance']['origin']}" + (f" batch {r['provenance']['batch']}" if r['provenance']['batch'] else "") for r in out)),
              "hops": nh, "hop_outcomes": dict(outcomes), "snippets_verified": nh - len(unverified),
              "unverified": unverified, "distinct_documents": len(docs)}
    write_json(run.stage1_dir(sid) / "build_report.json", report)
    print(f"candidates: {len(out)} -> {run.input_file(sid)} | hops: {nh} | "
          f"bridge hops marked: {sum(1 for r in out for h in r['hops'] if h['is_bridge'])}")
    print(f"hop outcomes: {dict(outcomes)}")
    print(f"snippets verbatim-verified: {nh - len(unverified)}/{nh}")
    for u in unverified[:8]:
        print(f"  UNVERIFIED: {u['qid']} hop {u['hop']} {u['doc_id']}")
    print(f"distinct documents: {len(docs)} | hops/question mean {nh / max(1, len(out)):.2f}")
    print(f"near misses: {len(near)} -> {nm_path} | {report['near_misses_by_gate']} | uniqueness kinds "
          f"{report['uniqueness_failure_kinds']}")


if __name__ == "__main__":
    main()
