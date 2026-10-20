#!/usr/bin/env python3
"""Build the review site with an invented demo dataset, so the site runs without any benchmark data.

  python3 review_site/demo/make_demo.py      # writes demo/build/ and data/, then:
  python3 review_site/serve.py               # open http://127.0.0.1:8765/

SYNTHETIC CONTENT ONLY. Every place, person, work and document below is invented (the island nation
of Oskeria and everything in it); the document ids are demo ids, not ClimbMix ids. Listed as
[synthetic] in .leak-scan-allow. Never put a real benchmark item in this file.
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"

DOCS = {
    "demo_doc_001": "Oskeria is an island nation in the Gulf of Amberly. Velmaran, the capital of Oskeria, sits on "
                    "the northern coast and has been the seat of the Oskerian assembly since 1802.",
    "demo_doc_002": "The Ardune rises in the Morrowfen hills and runs through Velmaran before reaching the sea at "
                    "Port Ilse. Its quays were rebuilt after the flood of 1911.",
    "demo_doc_003": "Lanterns of Morrowfen, an opera in three acts, was composed by Ilsa Renvaar and first staged "
                    "in Velmaran in 1868.",
    "demo_doc_004": "Ilsa Renvaar founded the Velmaran Conservatory in 1874 and directed it until her death.",
    "demo_doc_005": "A guide to the conservatories of Oskeria lists the Velmaran Conservatory among the oldest, "
                    "without naming its founder.",
    "demo_doc_006": "Tomas Hale kept the lighthouse at Cape Vardequist from 1890 to 1921 and logged every ship "
                    "that rounded the cape.",
    "demo_doc_007": "The first atlas of the Oskerian coast, published in 1904, was compiled by a committee of the "
                    "Velmaran Geographical Society.",
}


def hop(index, clue, doc_id, status, quotes, constraints, note="", supports="full", rejected=None):
    return {"index": index, "clue": clue, "doc_id": doc_id, "supporting_doc_ids": [doc_id],
            "audit": {"index": index, "type": "required", "constraints": constraints, "status": status,
                      "evidence": [{"doc_id": doc_id, "source": "original", "supports": supports, "quotes": quotes}]
                      if quotes else [],
                      "rejected_docs": rejected or [], "note": note}}


def constraint(cid, text, covered_by, status="covered", note=""):
    return {"id": cid, "text": text, "covered_by": covered_by, "status": status, "note": note}


def audit(verdict, summary, constraints, hops, fail_reasons=(), verification="confirmed", salvage=None):
    grounded = sum(h["audit"]["status"] == "grounded" for h in hops)
    a = {"auditor": "demo", "audited_at": "2026-01-01T00:00:00Z", "verdict": verdict,
         "fail_reasons": list(fail_reasons), "summary": summary, "constraints": constraints, "added_hops": [],
         "answer_check": {"status": "derives_gold" if verdict != "fail" else "partial",
                          "note": "The answer follows from the grounded hops." if verdict != "fail"
                          else "The chain breaks at an ungrounded hop."},
         "verification": {"result": verification, "changes": [], "note": ""},
         "invalid_doc_ids": [], "searches": [],
         "stats": {"n_constraints": len(constraints),
                   "n_constraints_covered": sum(c["status"] == "covered" for c in constraints),
                   "n_hops": len(hops), "n_hops_grounded": grounded,
                   "n_hops_grounded_only_via_new_docs": 0, "n_added_hops": 0}}
    if salvage:
        a["salvage"] = salvage
    return a


def records():
    h1 = [hop(1, "Velmaran is the capital of Oskeria.", "demo_doc_001", "grounded",
              ["Velmaran, the capital of Oskeria, sits on the northern coast"], ["C1"]),
          hop(2, "The Ardune runs through Velmaran.", "demo_doc_002", "grounded",
              ["runs through Velmaran before reaching the sea at Port Ilse"], ["C2"])]
    c1 = [constraint("C1", "the capital of the island nation of Oskeria", [1]),
          constraint("C2", "Which river runs through", [2])]
    h2 = [hop(1, "Lanterns of Morrowfen was composed by Ilsa Renvaar.", "demo_doc_003", "grounded",
              ["Lanterns of Morrowfen, an opera in three acts, was composed by Ilsa Renvaar"], ["C1"]),
          hop(2, "Ilsa Renvaar founded the Velmaran Conservatory in 1874.", "demo_doc_004", "grounded",
              ["Ilsa Renvaar founded the Velmaran Conservatory in 1874"], ["C2", "C3"],
              rejected=[{"doc_id": "demo_doc_005", "reason": "names the conservatory but not its founder"}])]
    c2 = [constraint("C1", "the composer of the opera Lanterns of Morrowfen", [1]),
          constraint("C2", "found the Velmaran Conservatory", [2]),
          constraint("C3", "In what year", [2])]
    h3 = [hop(1, "Tomas Hale kept the lighthouse at Cape Vardequist.", "demo_doc_006", "grounded",
              ["Tomas Hale kept the lighthouse at Cape Vardequist from 1890 to 1921"], ["C1"]),
          hop(2, "Tomas Hale compiled the first atlas of the Oskerian coast.", "demo_doc_007", "ungrounded", [],
              ["C2"], note="The atlas document credits a committee of the Velmaran Geographical Society, not Hale.")]
    c3 = [constraint("C1", "lighthouse keeper of Cape Vardequist", [1]),
          constraint("C2", "wrote the first atlas of the Oskerian coast", [2], "not_covered",
                     "No document attributes the atlas to Hale.")]
    released = [
        {"record_id": "demo-1", "question": "Which river runs through the capital of the island nation of Oskeria?",
         "answer": "Ardune", "n_hops": 2, "hops": h1,
         "audit": audit("pass", "Both hops are grounded by their cited documents.", c1, h1)},
        {"record_id": "demo-2", "question": "In what year did the composer of the opera Lanterns of Morrowfen found "
                                            "the Velmaran Conservatory?",
         "answer": "1874", "n_hops": 2, "hops": h2,
         "audit": audit("pass", "Grounded; one candidate document was rejected because it does not name the founder.",
                        c2, h2)},
    ]
    near_miss = [
        {"record_id": "demo-nm-1", "question": "Which lighthouse keeper of Cape Vardequist wrote the first atlas of "
                                               "the Oskerian coast?",
         "answer": "Tomas Hale", "n_hops": 2, "hops": h3,
         "audit": audit("fail", "Hop 2 is not grounded: the atlas is credited to a society committee.", c3, h3,
                        fail_reasons=["hop_not_grounded"],
                        salvage={"outcome": "not_salvageable",
                                 "blocker": "No document supports the atlas clue, and dropping it leaves the "
                                            "question without its identifying constraint.",
                                 "note": "Kept as a near miss for review."})},
    ]
    return released, near_miss


def main():
    BUILD.mkdir(parents=True, exist_ok=True)
    released, near_miss = records()
    for name, recs in (("demo_audited.jsonl", released), ("demo_near_misses.jsonl", near_miss)):
        (BUILD / name).write_text("".join(json.dumps(r) + "\n" for r in recs))
    (BUILD / "docs.jsonl").write_text("".join(json.dumps({"doc_id": d, "text": t}) + "\n" for d, t in DOCS.items()))
    config = {"corpus": "demo corpus (invented)", "docs": "docs.jsonl",
              "datasets": [{"id": "demo", "name": "Demo", "title": "Demo set (invented)", "records": "demo_audited.jsonl"},
                           {"id": "nm_demo", "name": "Demo", "title": "Demo near misses (invented)",
                            "group": "near_miss", "records": "demo_near_misses.jsonl"}]}
    (BUILD / "config.json").write_text(json.dumps(config, indent=1))
    subprocess.run([sys.executable, str(HERE.parent / "build_site_data.py"), str(BUILD / "config.json")], check=True)
    print("demo built: python3 review_site/serve.py, then open http://127.0.0.1:8765/")


if __name__ == "__main__":
    main()
