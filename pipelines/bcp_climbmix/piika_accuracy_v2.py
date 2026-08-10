#!/usr/bin/env python3
"""piika (GPT-5.5, own-retrieval / no docs from us) accuracy across the funnel, ending at the canonical
final set. Deterministic join of piika per-query correctness (piika_correct.json: qid->bool, frozen from
origin/codex-browsecomp-plus-climbmix-artifacts).

Canonical final = projected_questions_final.jsonl (every content clue is a hop; four documented temporal
near misses use the release's relaxed temporal-qualifier policy).
The all-hops-grounded (pre-coverage) count is computed here from the two scrutiny passes (union), so no
separate 63-question file is needed. The 154 (all necessary hops grounded) is an earlier superseded variant.
"""
import json, glob, os

BASE = os.path.dirname(os.path.abspath(__file__))
cmap = json.load(open(f"{BASE}/piika_correct.json"))
def ids_of(p): return [str(json.loads(l)["record_id"]) for l in open(p)] if os.path.exists(p) else []
def acc(ids):
    ids = [str(q) for q in ids if str(q) in cmap]; c = sum(1 for q in ids if cmap[q])
    return c, len(ids), (100 * c / len(ids) if ids else 0.0)

# all-hops-grounded set (union of the two scrutiny passes), computed from runs so no 63-file is required
v1 = {str(json.load(open(f))["qid"]): json.load(open(f)) for f in glob.glob(f"{BASE}/runs/*.json")}
v2 = {str(json.load(open(f))["qid"]): json.load(open(f)) for f in glob.glob(f"{BASE}/runs_v2/*.json")}
def all_grounded(q):
    h1 = v1[q].get("hops") or []; h2 = (v2.get(q) or {}).get("hops") or []
    return bool(h1) and all((h.get("supported") or (h2[i].get("supported") if i < len(h2) else False)) for i, h in enumerate(h1))
grounded_ids = [q for q in v1 if all_grounded(q)]

rows = [
    ("all BCP", list(cmap.keys())),
    ("answerable (Stage 1-2)", [os.path.basename(x)[:-5] for x in glob.glob(f"{BASE}/inputs/*.json")]),
    ("all hops grounded (pre-coverage-audit)", grounded_ids),
    ("final release (54 ours + 11 Sahel-verified)", ids_of(f"{BASE}/projected_questions_final.jsonl")),
]
L = ["# piika accuracy across the funnel (own retrieval, no documents from us)", "",
     "piika = independent GPT-5.5 deep-research agent over the same ClimbMix BM25; answers from its own",
     "retrieval only (gold-answer judge). Deterministic join against each set.", "",
     "| set | n | piika correct | accuracy |", "|---|---:|---:|---:|"]
for name, ids in rows:
    c, n, a = acc(ids); L.append(f"| {name} | {n} | {c} | {a:.1f}% |")
L += ["",
      "Requiring every content clue to be represented by a grounded hop selects a set piika answers from the",
      "corpus far more often (29% -> 47% -> 60% -> 66%). Four restored near misses use the documented",
      "relaxed temporal-qualifier policy; all other final records retain strict all-hop grounding. The",
      "final release adds 11 questions from an independent projection (Sahel), each all-hop audited with",
      "retrieval-verified grounding.", "",
      "## Earlier intermediate (superseded)"]
c, n, a = acc(ids_of(f"{BASE}/projected_questions_all_hops.jsonl"))
L.append(f"- all necessary hops grounded (dropped redundant): {n} questions, piika {a:.1f}%")
L += ["", "That trimmed unsupported/redundant hops to let a question pass, so a clue in the written question",
      "could lack corpus evidence (e.g. Q122's birthplace statistic). The current rule keeps a question only",
      "if every clue is represented by a hop; only the four documented temporal restorations relax exact",
      "temporal grounding."]
open(f"{BASE}/piika_accuracy_v2.md", "w").write("\n".join(L) + "\n")
print("\n".join(L))
