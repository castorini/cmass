#!/usr/bin/env python3
"""The deterministic steps of BrowseComp-Plus Stage 1, in the order they ran (README.md).

  bcp_build.py [--run RUN] [--set bcp] verdicts          # project/ -> verdicts.jsonl, projectable.jsonl
  bcp_build.py ... scrutiny-inputs                        # projectable -> scrutiny_inputs.jsonl (full decomposition)
  bcp_build.py ... all-hops                               # scrutiny1/ + scrutiny2/ -> all_hops_v1, all_hops_v2,
                                                          #   comprehensive (superseded), all_grounded (the rule that ran)
  bcp_build.py ... coverage-bundles                       # all_grounded (+ verified repairs) -> coverage_bundles.jsonl
  bcp_build.py ... coverage-final                         # coverage/ -> coverage_final.jsonl, coverage_rejected.jsonl
  bcp_build.py ... review                                 # manual/review.json -> reviewed.jsonl
  bcp_build.py ... external                               # external/ -> external_candidates.jsonl
  bcp_build.py ... external-final                         # external_audit/ -> external_verified.jsonl
  bcp_build.py ... repair-pool                            # -> repair_pool.jsonl (phases A and B)
  bcp_build.py ... repaired                               # repair/ -> repaired.jsonl (claimed repairs)
  bcp_build.py ... candidates                             # -> candidates.jsonl and inputs/<set>.jsonl
  bcp_build.py ... expansion-pool                         # PROJECTABLE minus candidates -> expansion_pool.jsonl
  bcp_build.py ... expansion                              # strict_audit/ + rejudge/ -> expansion_survivors.jsonl
  bcp_build.py ... near-misses                            # -> inputs/<set>_near_misses.jsonl
  bcp_build.py ... counts                                 # the count at every step -> counts.json

Every decision is recomputed here from the agents' per-hop fields; an agent's own aggregate flag is never
trusted (the original builders did the same). A step refuses to run while an agent output it needs is
missing or invalid; --partial builds from what exists and says what it left out (for trial runs only).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bcp_common import (SKIP_VERDICT, Run, Stage1, all_grounded, by_qid, decomposition, projected_hops,  # noqa: E402
                        qkey, qsort, release_hops, union_hops, validate, write_json, write_jsonl)
from bcp_state import State, strict_qualifies  # noqa: E402


class Build:
    def __init__(self, run: Run, sid: str, partial: bool):
        self.run, self.sid, self.partial = run, sid, partial
        self.st = Stage1(run, sid)
        self.state = State(run, sid)

    # ------------------------------------------------------------------ helpers
    def need(self, *names: str) -> None:
        for n in names:
            if not self.st.has(n):
                raise SystemExit(f"{self.st.lst(n)} is missing: run the step that builds it first (README order)")

    def outputs(self, step: str, targets: dict[str, dict]) -> dict[str, dict]:
        """Valid outputs of a step for its targets; stops on a missing or invalid one unless --partial."""
        got, bad = {}, []
        for q, inp in targets.items():
            p = self.st.out(step, q)
            if not p.exists():
                bad.append((q, "missing"))
                continue
            try:
                out = json.loads(p.read_text())
            except json.JSONDecodeError:
                bad.append((q, "not JSON"))
                continue
            errs = validate(step, q, out, inp)
            if errs:
                bad.append((q, errs[0]))
            else:
                got[q] = out
        if bad:
            msg = f"{step}: {len(bad)} of {len(targets)} output(s) missing or invalid, e.g. " + \
                  "; ".join(f"{q}: {e}" for q, e in bad[:5]) + f" (python3 bcp_state.py {self.sid} {step} --pending)"
            if not self.partial:
                raise SystemExit(msg)
            print(f"--partial: {msg}; building from the {len(got)} valid one(s)")
        return got

    def task_record(self, row: dict, extra: dict) -> dict:
        """A candidate in the record format of cmass/records.py (the Stage 2 input)."""
        t = self.st.tasks()[row["record_id"]]
        hops = []
        for h in row["hops"]:
            docs = list(dict.fromkeys(h.get("supporting_doc_ids") or []))
            hop = {"clue": h["clue"], "doc_id": docs[0] if docs else "", "supporting_doc_ids": docs,
                   "snippet": h.get("snippet", "")}
            for k in ("redundant", "fully_grounded", "basis", "missing_qualifier"):
                if k in h:
                    hop[k] = h[k]
            hops.append(hop)
        rec = {"record_id": row["record_id"], "question": t["question"], "answer": t["answer"],
               "answer_type": t.get("answer_type", "single"), "hops": hops, "hints": t.get("hints") or [],
               "source": t.get("source", row["record_id"])}
        rec.update(extra)
        return rec

    # ------------------------------------------------------------------ steps
    def verdicts(self):
        """build_verdicts.py + build_projected_list.py."""
        tasks = self.st.tasks()
        skips = self.st.manual("skips")
        unknown = set(skips) - set(tasks)
        if unknown:
            raise SystemExit(f"manual/skips.json names questions that are not tasks: {qsort(unknown)[:5]}")
        outs = self.outputs("project", self.state.targets("project"))
        rows, projectable = [], []
        for q in qsort(tasks):
            if q in skips:
                rows.append({"record_id": q, "verdict": SKIP_VERDICT, "method": "content_policy_block"})
            elif q in outs:
                d = outs[q]
                rows.append({"record_id": q, "verdict": d["verdict"], "method": "single_agent"})
                if d["verdict"] == "PROJECTABLE":
                    hops = projected_hops(d)
                    docs = [e["doc_id"] for h in hops for e in h["corpus_evidence"]]
                    projectable.append({"record_id": q, "question": tasks[q]["question"], "answer": tasks[q]["answer"],
                                        "projection_method": "single_agent", "n_hops": len(hops), "hops": hops,
                                        "support_doc_ids": list(dict.fromkeys(docs))})
        self.st.write("verdicts", rows)
        self.st.write("projectable", projectable)
        tally = Counter(r["verdict"] for r in rows)
        print(f"verdicts: {len(rows)} of {len(tasks)} tasks; {dict(sorted(tally.items()))}")
        print(f"projectable: {len(projectable)} questions -> {self.st.lst('projectable')}")

    def scrutiny_inputs(self):
        """build_scrutiny_inputs.py: the full decomposition (discriminating + redundant/unsupported hops)."""
        self.need("projectable")
        outs = self.st.outputs("project")
        rows = []
        for r in self.st.read("projectable"):
            q = r["record_id"]
            hops = decomposition(outs[q])
            rows.append({"qid": q, "question": r["question"], "answer": r["answer"], "n_hops": len(hops), "hops": hops})
        self.st.write("scrutiny_inputs", rows)
        n = [r["n_hops"] for r in rows] or [0]
        print(f"scrutiny inputs: {len(rows)} questions; hops per question min {min(n)} median "
              f"{int(statistics.median(n))} max {max(n)}")

    def all_hops(self):
        """build_all_hops_list.py (pass 1, 154 in the run), build_all_hops_list_v2.py (pass 2, 177),
        build_comprehensive_final.py (115): superseded intermediates, kept for the counts; then
        build_all_grounded.py (63): the rule that produced the candidates."""
        self.need("scrutiny_inputs")
        tg = self.state.targets("scrutiny1")
        p1 = self.outputs("scrutiny1", tg)
        p2 = self.outputs("scrutiny2", tg)
        inp = by_qid(self.st.read("scrutiny_inputs"), "qid")

        def nec_rows(runs, extra_ok):
            rows = []
            for q in qsort(runs):
                nec = [h for h in runs[q]["hops"] if h.get("necessary")]
                if nec and all(h.get("supported") for h in nec) and extra_ok(q):
                    rows.append({"record_id": q, "question": inp[q]["question"], "answer": inp[q]["answer"],
                                 "n_necessary_hops": len(nec),
                                 "hops": [{"clue": h.get("clue", ""), "supporting_doc_ids": h.get("doc_ids") or []} for h in nec]})
            return rows

        v1 = nec_rows(p1, lambda q: True)
        v2 = nec_rows(p2, lambda q: p2[q].get("comprehensive") is True)
        comp_false = {q for q, d in p2.items() if d.get("comprehensive") is False}
        comp = [r for r in v1 if r["record_id"] not in comp_false]
        self.st.write("all_hops_v1", v1)
        self.st.write("all_hops_v2", v2)
        self.st.write("comprehensive", comp)

        kept, rejected = [], []
        for q in qsort(p1):
            hops = union_hops(p1[q], p2.get(q))
            row = {"record_id": q, "question": inp[q]["question"], "answer": inp[q]["answer"], "n_hops": len(hops)}
            if all_grounded(p1[q], p2.get(q)):
                kept.append({**row, "hops": hops})
            else:
                rejected.append({**row, "ungrounded": [h["index"] for h in hops if not h["grounded"]]})
        self.st.write("all_grounded", kept)
        self.st.write("all_grounded_rejected", rejected)
        alt = {m: sum(all_grounded(p1[q], p2.get(q), m) for q in p1) for m in ("pass1", "both")}
        print(f"pass 1 (trim redundant, every necessary hop supported): {len(v1)}  [superseded]")
        print(f"pass 2 (+ comprehensive): {len(v2)}  [superseded]")
        print(f"pass 1 minus pass-2 non-comprehensive: {len(comp)}  [superseded]")
        print(f"all hops grounded (union of the two passes): {len(kept)} of {len(p1)}; "
              f"pass 1 alone would give {alt['pass1']}, both passes agreeing {alt['both']}")

    def coverage_bundles(self):
        """The coverage auditor's data (audit_coverage.js embedded it in the prompt; coverage_repaired.js did
        the same for the verified repairs). The original bundle builder was not committed: here each hop's
        clue is pass 2's wording where it has one (as in the original bundles), its snippet the grounding
        note of the pass that supported it, its doc_ids the union."""
        self.need("all_grounded")
        rows = []
        for r in self.st.read("all_grounded"):
            rows.append({"qid": r["record_id"], "kind": "all_grounded", "question": r["question"], "answer": r["answer"],
                         "hops": [{"clue": h.get("pass2_clue") or h["clue"], "grounded": h["grounded"],
                                   "snippet": h.get("note", ""), "doc_ids": h["supporting_doc_ids"]} for h in r["hops"]]})
        n_rep = 0
        if self.st.has("repaired"):
            verified = {q for q, d in self.st.manual("repairs").items() if d.get("verified") is True}
            for r in self.st.read("repaired"):
                if r["record_id"] in verified:
                    n_rep += 1
                    rows.append({"qid": r["record_id"], "kind": "repaired", "question": r["question"], "answer": r["answer"],
                                 "hops": [{"clue": h["clue"], "grounded": True, "snippet": h.get("snippet", ""),
                                           "doc_ids": h["supporting_doc_ids"]} for h in r["hops"]]})
        self.st.write("coverage_bundles", rows)
        print(f"coverage bundles: {len(rows) - n_rep} all-hops-grounded + {n_rep} verified repairs")

    def coverage_final(self):
        """build_coverage_final.py: drop an all-hops-grounded question with a missing sub-clue."""
        self.need("all_grounded", "coverage_bundles")
        tg = {q: v for q, v in self.state.targets("coverage").items() if v.get("kind") == "all_grounded"}
        cov = self.outputs("coverage", tg)
        kept, dropped = [], []
        for r in self.st.read("all_grounded"):
            q = r["record_id"]
            if q not in cov:
                continue                                   # --partial only
            if cov[q]["uncovered_phrases"]:
                dropped.append({"record_id": q, "uncovered": cov[q]["uncovered_phrases"], "reason": cov[q].get("reason", "")})
            else:
                kept.append({"record_id": q, "question": r["question"], "answer": r["answer"], "n_hops": r["n_hops"],
                             "hops": release_hops(r["hops"])})
        self.st.write("coverage_final", kept)
        self.st.write("coverage_rejected", dropped)
        print(f"coverage audit: kept {len(kept)}, dropped {len(dropped)} for a missing sub-clue")

    def review(self):
        """The manual review of the coverage-audited set (MANUAL.md, 'review'; 58 -> 50 in the run)."""
        self.need("coverage_final")
        dec = self.st.manual("review", required=True)
        rows = self.st.read("coverage_final")
        ids = {r["record_id"] for r in rows}
        if set(dec) - ids:
            raise SystemExit(f"manual/review.json names questions outside coverage_final: {qsort(set(dec) - ids)[:5]}")
        bad = [q for q, d in dec.items() if d.get("decision") not in ("drop", "keep")]
        if bad:
            raise SystemExit(f"manual/review.json: decision must be drop or keep ({bad[:5]})")
        kept = [r for r in rows if dec.get(r["record_id"], {}).get("decision") != "drop"]
        self.st.write("reviewed", kept)
        print(f"manual review: {len(rows)} -> {len(kept)} ({len(rows) - len(kept)} dropped)")

    def external(self):
        """Independent projections (the 11 of the run): audit targets among the ones whose status is success."""
        self.need("verdicts", "reviewed")
        verdict = {r["record_id"]: r["verdict"] for r in self.st.read("verdicts")}
        reviewed = {r["record_id"] for r in self.st.read("reviewed")}
        tasks = self.st.tasks()
        groups, rows, mism = Counter(), [], 0
        for q, d in sorted(self.st.external().items(), key=lambda kv: qkey(kv[0])):
            if str(d.get("qid", q)) != q:
                raise SystemExit(f"external/{q}.json: qid field {d.get('qid')!r} does not match the file name")
            if d.get("status") != "success":
                groups["not_success"] += 1
                continue
            v = verdict.get(q)
            if q in reviewed:
                g = "overlap"
            elif v == "PROJECTABLE":
                g = "new"
            elif v in ("PARTIAL", "NOT"):
                g = "not_projectable"
            else:
                g = "other"
            groups[g] += 1
            if g not in ("new", "not_projectable"):
                continue
            if (d.get("question"), d.get("answer")) != (tasks[q]["question"], tasks[q]["answer"]):
                mism += 1
            rows.append({"qid": q, "group": g, "question": tasks[q]["question"], "answer": tasks[q]["answer"],
                         "hops": [{"clue": h.get("clue", ""), "corpus_doc_ids": list(h.get("corpus_doc_ids") or []),
                                   "snippets": list(h.get("snippets") or [])} for h in d.get("hops") or []]})
        self.st.write("external_candidates", rows)
        print(f"independent projections with status success: {sum(v for k, v in groups.items() if k != 'not_success')}; "
              f"in the PROJECTABLE set and not yet kept: {groups['new']}; already kept: {groups['overlap']}; "
              f"PARTIAL or NOT here: {groups['not_projectable']}; other: {groups['other']}; "
              f"status not success: {groups['not_success']}")
        if mism:
            print(f"warning: {mism} independent projection(s) carry a question or answer that differs from the task; "
                  "the task's text is used")

    def external_final(self):
        """audit_sahel.js / audit_sahel33.js outcome: keep = hops present, chain ok, every hop grounded."""
        self.need("external_candidates")
        tg = self.state.targets("external_audit")
        outs = self.outputs("external_audit", tg)
        cand = by_qid(self.st.read("external_candidates"), "qid")
        kept, np_pass = [], []
        for q in qsort(outs):
            o = outs[q]
            ok = o["all_hops_present"] and o["chain_ok"] and all(h["grounded"] for h in o["per_hop"])
            if not ok:
                continue
            if cand[q]["group"] == "not_projectable":
                np_pass.append(q)
                continue
            kept.append({"record_id": q, "question": cand[q]["question"], "answer": cand[q]["answer"],
                         "n_hops": len(cand[q]["hops"]),
                         "hops": [{"clue": h["clue"], "supporting_doc_ids": h["corpus_doc_ids"]} for h in cand[q]["hops"]]})
        self.st.write("external_verified", kept)
        print(f"independent projections verified: {len(kept)} (of {sum(1 for r in cand.values() if r['group'] == 'new')} audited)")
        print(f"answerability check of the PARTIAL/NOT group: {len(np_pass)} of "
              f"{sum(1 for r in cand.values() if r['group'] == 'not_projectable')} pass "
              "(the run found none; such a question is reported here and not added)")

    def repair_pool(self):
        """The near-miss pools of the repair program (nearmiss_repair/SUMMARY.md)."""
        self.need("scrutiny_inputs", "reviewed", "external_verified")
        tg = self.state.targets("scrutiny1")
        p1 = self.outputs("scrutiny1", tg)
        p2 = self.outputs("scrutiny2", tg)
        inp = by_qid(self.st.read("scrutiny_inputs"), "qid")
        final = {r["record_id"] for r in self.st.read("reviewed")} | {r["record_id"] for r in self.st.read("external_verified")}
        drops = {q for q, d in self.st.manual("review", required=True).items() if d.get("decision") == "drop"}
        excluded = final | drops
        rows, ours = [], {}
        for q in qsort(p1):
            if q in excluded:
                continue
            hops = union_hops(p1[q], p2.get(q))
            n = sum(not h["grounded"] for h in hops)
            if n in (1, 2):
                ours[q] = n
                rows.append({"qid": q, "phase": "A" if n == 1 else "B", "source": "ours",
                             "question": inp[q]["question"], "answer": inp[q]["answer"],
                             "hops": [{"index": h["index"], "clue": h["clue"], "grounded": h["grounded"],
                                       "doc_ids": h["supporting_doc_ids"], "prior_note": h["note"],
                                       "FAILING": not h["grounded"]} for h in hops]})
        ext_nm = 0
        if self.st.has("external_candidates"):
            cand = by_qid(self.st.read("external_candidates"), "qid")
            for q, o in sorted(self.st.outputs("external_audit").items()):
                c = cand.get(q)
                if not c or c["group"] != "new" or validate("external_audit", q, o, c):
                    continue
                n = sum(not h["grounded"] for h in o["per_hop"])
                if not (o["chain_ok"] and o["all_hops_present"] and n in (1, 2)):
                    continue
                ext_nm += 1
                if q in ours or q in excluded:
                    continue
                rows.append({"qid": q, "phase": "A", "source": "external", "question": c["question"], "answer": c["answer"],
                             "hops": [{"index": i, "clue": h["clue"], "grounded": ph["grounded"], "doc_ids": h["corpus_doc_ids"],
                                       "prior_note": ph.get("why", ""), "FAILING": not ph["grounded"]}
                                      for i, (h, ph) in enumerate(zip(c["hops"], o["per_hop"]), 1)]})
        rows.sort(key=lambda r: (r["phase"], qkey(r["qid"])))
        self.st.write("repair_pool", rows)
        c = Counter((r["phase"], r["source"]) for r in rows)
        print(f"near-miss pools: ours {c[('A', 'ours')]} one hop short + {c[('B', 'ours')]} two hops short; "
              f"independent near misses {ext_nm}, of which {c[('A', 'external')]} not already in our pools (phase A)")
        print(f"phase A: {c[('A', 'ours')] + c[('A', 'external')]} targets; phase B: {c[('B', 'ours')]} targets")

    def repaired(self):
        """Claimed repairs: every FAILING hop fixed by a document the agent read (repair_nearmiss.js)."""
        self.need("repair_pool")
        pool = by_qid(self.st.read("repair_pool"), "qid")
        outs = self.outputs("repair", self.state.targets("repair"))
        rows = []
        for q in qsort(outs):
            o, r = outs[q], pool[q]
            fixed: dict[int, list[dict]] = {}
            for f in o["fixed"]:
                fixed.setdefault(f["index"], []).append(f)
            failing = [h["index"] for h in r["hops"] if h["FAILING"]]
            if not all(i in fixed for i in failing):
                continue
            red = {}
            if r["source"] == "ours":
                red = {h["index"]: h["redundant"] for h in self._union(q)}
            hops = []
            for h in r["hops"]:
                if h["FAILING"]:
                    docs = list(dict.fromkeys(f["doc_id"] for f in fixed[h["index"]]))
                    snip = " ... ".join(f["snippet"] for f in fixed[h["index"]])
                else:
                    docs, snip = h["doc_ids"], h.get("prior_note", "")
                hops.append({"index": h["index"], "clue": h["clue"], "redundant": red.get(h["index"], False),
                             "supporting_doc_ids": docs, "snippet": snip})
            rows.append({"record_id": q, "question": r["question"], "answer": r["answer"], "phase": r["phase"],
                         "pool": r["source"], "n_hops": len(hops), "hops": hops})
        self.st.write("repaired", rows)
        c = Counter(r["phase"] for r in rows)
        print(f"claimed repairs: {len(rows)} of {len(outs)} judged (phase A {c['A']}, phase B {c['B']}); "
              "next: the operator verifies them against the documents (manual/repairs.json, MANUAL.md)")

    def _union(self, q):
        p1 = json.loads(self.st.out("scrutiny1", q).read_text())
        p2p = self.st.out("scrutiny2", q)
        return union_hops(p1, json.loads(p2p.read_text()) if p2p.exists() else None)

    def candidates(self):
        """The projected candidate list (stage2/projected_questions_final.jsonl in the run) and inputs/<set>.jsonl."""
        self.need("reviewed", "external_verified")
        rows = [{**r, "projection_source": "ours", "stage1_path": "all_hops_grounded"} for r in self.st.read("reviewed")]
        rows += [{**r, "projection_source": "independent", "stage1_path": "independent_projection"}
                 for r in self.st.read("external_verified")]
        if self.st.has("repaired") and self.st.read("repaired"):
            dec = self.st.manual("repairs", required=True)
            unknown = set(dec) - {r["record_id"] for r in self.st.read("repaired")}
            if unknown:
                raise SystemExit(f"manual/repairs.json names questions with no claimed repair: {qsort(unknown)[:5]}")
            cov_tg = {q: v for q, v in self.state.targets("coverage").items() if v.get("kind") == "repaired"}
            cov = self.outputs("coverage", cov_tg)
            for r in self.st.read("repaired"):
                d = dec.get(r["record_id"], {})
                if d.get("verified") is True and d.get("decision") == "accept":
                    if r["record_id"] not in cov:
                        raise SystemExit(f"{r['record_id']}: accepted in manual/repairs.json but not coverage-audited: "
                                         "run coverage-bundles and the coverage workflow for it first")
                    if cov[r["record_id"]]["uncovered_phrases"]:
                        raise SystemExit(f"{r['record_id']}: accepted in manual/repairs.json but its coverage audit failed")
                    rows.append({"record_id": r["record_id"], "question": r["question"], "answer": r["answer"],
                                 "n_hops": r["n_hops"], "hops": release_hops(r["hops"]),
                                 "projection_source": "ours" if r["pool"] == "ours" else "independent",
                                 "stage1_path": "near_miss_repair"})
        ids = [r["record_id"] for r in rows]
        if len(ids) != len(set(ids)):
            raise SystemExit("a question reached the candidate list twice")
        ov = self.st.manual("overrides")
        if set(ov) - set(ids):
            raise SystemExit(f"manual/overrides.json names non-candidates: {qsort(set(ov) - set(ids))[:5]}")
        for r in rows:
            o = ov.get(r["record_id"])
            if o:
                if not isinstance(o.get("hops"), list) or not all(h.get("clue") for h in o["hops"]):
                    raise SystemExit(f"manual/overrides.json {r['record_id']}: hops must be a list of {{clue, ...}}")
                r["hops"] = release_hops(o["hops"])
                r["n_hops"] = len(r["hops"])
                r["override"] = o.get("reason", "")
        rows.sort(key=lambda r: qkey(r["record_id"]))
        self.st.write("candidates", rows)
        recs = [self.task_record(r, {"projection_source": r["projection_source"], "stage1_path": r["stage1_path"]})
                for r in rows]
        out = self.run.root / "inputs" / f"{self.sid}.jsonl"
        write_jsonl(out, recs)
        c = Counter(r["stage1_path"] for r in rows)
        print(f"candidates: {len(rows)} = {c['all_hops_grounded']} all-hops-grounded and reviewed + "
              f"{c['independent_projection']} independent + {c['near_miss_repair']} near-miss repairs "
              f"({len(ov)} hop list(s) overridden) -> {out}")

    def expansion_pool(self):
        """The unused PROJECTABLE questions (261 in the run), with their prior union grounding (EXPANSION_326.md)."""
        self.need("candidates", "scrutiny_inputs")
        cand = {r["record_id"] for r in self.st.read("candidates")}
        inp = by_qid(self.st.read("scrutiny_inputs"), "qid")
        rows = []
        for q in qsort(inp):
            if q in cand:
                continue
            hops = self._union(q)
            n = sum(not h["grounded"] for h in hops)
            rows.append({"qid": q, "question": inp[q]["question"], "answer": inp[q]["answer"],
                         "stratum": str(n) if n < 3 else "3+",
                         "hops": [{"index": h["index"], "clue": h["clue"], "prior_grounded": h["grounded"],
                                   "doc_ids": h["supporting_doc_ids"], "prior_note": h["note"]} for h in hops]})
        self.st.write("expansion_pool", rows)
        c = Counter(r["stratum"] for r in rows)
        print(f"expansion pool: {len(rows)} PROJECTABLE questions outside the candidates; ungrounded hops "
              f"0: {c['0']}, 1: {c['1']}, 2: {c['2']}, 3+: {c['3+']}")

    def expansion(self):
        """Strict qualifiers plus the lenient re-judging's survivors (expansion_candidates49.jsonl in the run)."""
        self.need("expansion_pool")
        pool = by_qid(self.st.read("expansion_pool"), "qid")
        strict = self.outputs("strict_audit", self.state.targets("strict_audit"))
        rej = self.outputs("rejudge", self.state.targets("rejudge"))
        rows, out_c = [], Counter()
        for q in qsort(pool):
            if q in strict and strict_qualifies(strict[q]):
                out_c["strict"] += 1
                rows.append({"record_id": q, "question": pool[q]["question"], "answer": pool[q]["answer"],
                             "route": "strict", "n_hops": len(strict[q]["hops"]),
                             "hops": [{"index": h["index"], "clue": h["clue"], "fully_grounded": True,
                                       "basis": "verbatim grounded", "missing_qualifier": "", "snippet": h["snippet"],
                                       "supporting_doc_ids": h["doc_ids"]} for h in strict[q]["hops"]]})
            elif q in rej:
                o = rej[q]
                waived_ok = all(h["fully_grounded"] or h["basis"].startswith("core fact grounded") for h in o["hops"])
                outcome = o["outcome"] if (o["outcome"] != "survive" or waived_ok) else "fail_core"
                out_c[outcome] += 1
                if outcome == "survive":
                    rows.append({"record_id": q, "question": pool[q]["question"], "answer": pool[q]["answer"],
                                 "route": "rejudge", "n_hops": len(o["hops"]),
                                 "hops": [{k: h[k] for k in ("index", "clue", "fully_grounded", "basis", "missing_qualifier",
                                                             "snippet", "supporting_doc_ids")} for h in o["hops"]]})
        self.st.write("expansion_survivors", rows)
        waived = sum(1 for r in rows for h in r["hops"] if not h["fully_grounded"])
        print(f"expansion: strict qualifiers {out_c['strict']}; re-judged: survive {out_c['survive']}, "
              f"fail on a missing core fact {out_c['fail_core']}, fail on coverage {out_c['fail_coverage']}; "
              f"survivors {len(rows)} ({sum(r['n_hops'] for r in rows)} hops, {waived} on a waived qualifier)")

    def near_misses(self):
        """inputs/<set>_near_misses.jsonl: tier 1, candidates the later manual qrels review cut; tier 2, the
        expansion survivors left after the manual trim (artifacts/near_miss/build_near_miss.py in the run)."""
        self.need("candidates", "expansion_survivors")
        cand = by_qid(self.st.read("candidates"))
        qr = self.st.manual("qrels_review", required=True)
        trim = self.st.manual("expansion_trim", required=True)
        if set(qr) - set(cand):
            raise SystemExit(f"manual/qrels_review.json names non-candidates: {qsort(set(qr) - set(cand))[:5]}")
        surv = by_qid(self.st.read("expansion_survivors"))
        if set(trim) - set(surv):
            raise SystemExit(f"manual/expansion_trim.json names non-survivors: {qsort(set(trim) - set(surv))[:5]}")
        recs = []
        for q in qsort(qr):
            recs.append(self.task_record(cand[q], {
                "failed_gate": "manual qrels review", "stage_rank": 1,
                "reason": qr[q].get("reason") or "cut by the manual review of the candidates' qrels: at least one "
                                                  "required hop judged PARTIAL or UNSUPPORTED",
                "projection_source": cand[q]["projection_source"]}))
        n2 = 0
        for q in qsort(surv):
            if trim.get(q, {}).get("decision") == "drop" or q in cand:
                continue
            n2 += 1
            recs.append(self.task_record(surv[q], {
                "failed_gate": "never reached human annotation", "stage_rank": 2,
                "reason": "from the unused PROJECTABLE candidates; fails the strict verbatim bar but survives "
                          "re-judging at the human reviewers' standard (core entity and relation grounded, "
                          "secondary qualifier waived)" if surv[q]["route"] == "rejudge" else
                          "from the unused PROJECTABLE candidates; passes the strict audit",
                "projection_source": "ours"}))
        out = self.st.near_miss_file()
        write_jsonl(out, recs)
        print(f"near misses: {len(recs)} = {len(qr)} tier 1 (cut by the qrels review; also candidates) + "
              f"{n2} tier 2 (expansion survivors after the manual trim of {len(trim)}) -> {out}")

    def counts(self):
        st, c = self.st, {}
        try:
            c["tasks"] = len(st.tasks())
        except SystemExit:
            pass
        if st.has("verdicts"):
            c["verdicts"] = dict(sorted(Counter(r["verdict"] for r in st.read("verdicts")).items()))
        for n in ("projectable", "all_hops_v1", "all_hops_v2", "comprehensive", "all_grounded", "coverage_final",
                  "coverage_rejected", "reviewed", "external_candidates", "external_verified", "repair_pool",
                  "repaired", "candidates", "expansion_pool", "expansion_survivors"):
            if st.has(n):
                c[n] = len(st.read(n))
        if st.has("candidates"):
            c["candidates_by_path"] = dict(Counter(r["stage1_path"] for r in st.read("candidates")))
        nm = read_rows(st.near_miss_file())
        if nm:
            c["near_misses_by_tier"] = dict(Counter(f"tier {r['stage_rank']}" for r in nm))
        write_json(st.dir / "counts.json", c)
        print(json.dumps(c, indent=1))


def read_rows(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def main() -> None:
    steps = ["verdicts", "scrutiny-inputs", "all-hops", "coverage-bundles", "coverage-final", "review", "external",
             "external-final", "repair-pool", "repaired", "candidates", "expansion-pool", "expansion", "near-misses",
             "counts"]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", help="run directory (default: $CMASS_RUN or the current directory)")
    ap.add_argument("--set", default="bcp")
    ap.add_argument("--partial", action="store_true", help="build from the outputs that exist (trial runs only)")
    ap.add_argument("step", choices=steps)
    a = ap.parse_args()
    b = Build(Run(a.run), a.set, a.partial)
    getattr(b, a.step.replace("-", "_"))()


if __name__ == "__main__":
    main()
