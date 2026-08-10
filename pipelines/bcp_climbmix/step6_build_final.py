#!/usr/bin/env python3
"""Final benchmark (self-contained). Two gates, from the committed scrutiny + coverage-audit outputs:
  1. ALL HOPS GROUNDED - every hop of the original decomposition is grounded in ClimbMix (a verbatim
     document states its fact in EITHER scrutiny pass, runs/ or runs_v2/; union). No hop dropped to pass.
  2. COVERAGE - drop questions with a MISSING SUB-CLUE: a content phrase (especially a specific
     number/count/interval) that no hop represents (from step-4 coverage-audit output, audit_coverage_result.json).
We do NOT additionally require each hop's evidence to match the exact wording (a stricter literal-grounding
audit was found too aggressive).

Reads : runs/*.json, runs_v2/*.json (step 3 pass1/pass2), inputs/*.json, audit_coverage_result.json (step 4)
Writes: projected_questions_final.jsonl   the final set
        projected_questions_final.md        index
        coverage_rejected.md                the questions dropped for a missing sub-clue
"""
import json, glob, os

BASE = os.path.dirname(os.path.abspath(__file__))
load = lambda f: json.load(open(f))
v1 = {str(load(f)["qid"]): load(f) for f in glob.glob(f"{BASE}/runs/*.json")}
v2 = {str(load(f)["qid"]): load(f) for f in glob.glob(f"{BASE}/runs_v2/*.json")}
cov = {r["qid"]: r for r in load(f"{BASE}/audit_coverage_result.json")["rows"]}

def grounded_hops(q):
    """Return (all_grounded, hops) for a question using the union of the two scrutiny passes."""
    h1 = v1[q].get("hops") or []
    h2 = (v2.get(q) or {}).get("hops") or []
    hops, all_g = [], bool(h1)
    for i, h in enumerate(h1):
        hb = h2[i] if i < len(h2) else {}
        s1, s2 = h.get("supported"), hb.get("supported")
        g = bool(s1 or s2)
        docs = list(dict.fromkeys(((h.get("doc_ids") or []) if s1 else []) + ((hb.get("doc_ids") or []) if s2 else [])))
        redundant = (h.get("necessary") is False) and (hb.get("necessary") is False)
        hops.append({"clue": h.get("clue", ""), "redundant": redundant, "supporting_doc_ids": docs})
        if not g:
            all_g = False
    return all_g, hops

kept, dropped = [], []
for q in sorted(v1, key=int):
    all_g, hops = grounded_hops(q)
    if not all_g:
        continue                              # not all hops grounded: excluded upstream
    if not cov.get(q, {}).get("coverage_ok", True):
        dropped.append(q); continue           # all-grounded but a clue has no hop: missing sub-clue
    m = load(f"{BASE}/inputs/{q}.json")
    kept.append({"record_id": q, "question": m.get("question", ""), "answer": m.get("answer", ""),
                 "n_hops": len(hops), "hops": hops})

kept.sort(key=lambda r: int(r["record_id"]))
with open(f"{BASE}/projected_questions_final.jsonl", "w") as fo:
    for r in kept:
        fo.write(json.dumps(r, ensure_ascii=False) + "\n")

md = [f"# Final corpus-grounded benchmark ({len(kept)})", "",
      f"Every hop of the original decomposition is grounded in ClimbMix (union of two scrutiny passes), and",
      f"every content clue of the question is represented by a hop ({len(dropped)} dropped for a missing sub-clue).", "",
      "| qid | question | answer | #hops |", "|---|---|---|---:|"]
for r in kept:
    md.append(f"| {r['record_id']} | {r['question'].replace('|',' ').replace(chr(10),' ')[:88]} | {str(r['answer']).replace('|',' ')[:36]} | {r['n_hops']} |")
open(f"{BASE}/projected_questions_final.md", "w").write("\n".join(md) + "\n")

rj = [f"# Dropped for a missing sub-clue ({len(dropped)})", "",
      "All hops grounded, but a content phrase of the question (often a specific number/interval) is not",
      "represented by any hop, so the benchmark could not check it against the corpus.", ""]
for q in dropped:
    m = load(f"{BASE}/inputs/{q}.json"); c = cov[q]
    rj.append(f"## qid {q} — answer: {m.get('answer','')}")
    rj.append(f"Q: {m.get('question','')[:280]}")
    rj.append(f"Missing: {'; '.join(c.get('uncovered', [])) or c.get('reason','')}")
    rj.append("")
open(f"{BASE}/coverage_rejected.md", "w").write("\n".join(rj) + "\n")

