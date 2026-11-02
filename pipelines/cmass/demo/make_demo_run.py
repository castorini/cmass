#!/usr/bin/env python3
"""Create a demo run directory (default: demo/run, gitignored) with invented Stage 1 candidates over the demo
corpus, configured to use demo/demo_cm.py as its corpus CLI, plus one valid sample audit.

  python3 pipelines/cmass/demo/make_demo_run.py [RUN_DIR]
  cd pipelines/cmass/demo/run && python3 ../../tools/validate.py      # 1 audit file, 0 errors

SYNTHETIC CONTENT ONLY: every place, person and document is invented (the island nation of Oskeria).
Listed as [synthetic] in .leak-scan-allow. Never put a real benchmark item here.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

CANDIDATES = [
    {"record_id": "demo-1", "answer_type": "single",
     "question": "Which river runs through the capital of the island nation of Oskeria?",
     "answer": "Ardune",
     "hops": [{"clue": "Velmaran is the capital of Oskeria.", "doc_id": "demo_doc_001",
               "supporting_doc_ids": ["demo_doc_001"], "snippet": "Velmaran, the capital of Oskeria"},
              {"clue": "The Ardune runs through Velmaran.", "doc_id": "demo_doc_002",
               "supporting_doc_ids": ["demo_doc_002", "demo_doc_011"],
               "snippet": "runs through Velmaran before reaching the sea"}]},
    {"record_id": "demo-2", "answer_type": "set",
     "question": "Which rivers of Oskeria longer than twenty kilometres rise in the Morrowfen hills?",
     "answer": ["Ardune", "Varrow"],
     "hops": [{"clue": "The Ardune rises in the Morrowfen hills.", "doc_id": "demo_doc_002",
               "supporting_doc_ids": ["demo_doc_002"]},
              {"clue": "The Varrow rises in the Morrowfen hills.", "doc_id": "demo_doc_004",
               "supporting_doc_ids": ["demo_doc_004"]}]},
    {"record_id": "demo-3", "answer_type": "single",
     "question": "In what year did the composer of the opera Lanterns of Morrowfen found the Velmaran Conservatory?",
     "answer": "1874",
     "hops": [{"clue": "Lanterns of Morrowfen was composed by Ilsa Renvaar.", "doc_id": "demo_doc_006",
               "supporting_doc_ids": ["demo_doc_006"]},
              {"clue": "Ilsa Renvaar founded the Velmaran Conservatory in 1874.", "doc_id": "demo_doc_008",
               "supporting_doc_ids": ["demo_doc_008", "demo_doc_007"]}]},
]
NEAR_MISSES = [
    {"record_id": "demo-nm-1", "answer_type": "single",
     "question": "Which lighthouse keeper of Cape Vardequist compiled the first atlas of the Oskerian coast?",
     "answer": "Tomas Hale",
     "hops": [{"clue": "Tomas Hale kept the lighthouse at Cape Vardequist.", "doc_id": "demo_doc_009",
               "supporting_doc_ids": ["demo_doc_009"]},
              {"clue": "Tomas Hale compiled the first atlas of the Oskerian coast.", "doc_id": "demo_doc_010",
               "supporting_doc_ids": ["demo_doc_010"]}]},
]

SAMPLE_AUDIT = {
    "record_id": "demo-1", "set": "demo", "revision": 0,
    "question": CANDIDATES[0]["question"], "answer": "Ardune",
    "constraints": [
        {"id": "C1", "text": "the capital of the island nation of Oskeria", "covered_by": [1], "status": "covered",
         "note": "Identifies the city: Velmaran."},
        {"id": "C2", "text": "Which river runs through", "covered_by": [2, "A1"], "status": "covered",
         "note": "The asked attribute; the Selm, the other river named near Velmaran, is excluded by A1."}],
    "hops": [
        {"index": 1, "type": "required", "role": "inclusion", "constraints": ["C1"], "status": "grounded",
         "evidence": [{"doc_id": "demo_doc_001", "source": "original", "supports": "full",
                       "quotes": ["Velmaran, the capital of Oskeria, sits on the northern coast"]}],
         "rejected_docs": [], "clue_issue": "", "note": ""},
        {"index": 2, "type": "required", "role": "inclusion", "constraints": ["C2"], "status": "grounded",
         "evidence": [{"doc_id": "demo_doc_002", "source": "original", "supports": "full",
                       "quotes": ["The Ardune rises in the Morrowfen hills and runs through Velmaran before reaching the sea at Port Ilse."]}],
         "rejected_docs": [{"doc_id": "demo_doc_011", "reason": "travel notes: mentions the quays, not the river's course"}],
         "clue_issue": "", "note": ""}],
    "added_hops": [
        {"id": "A1", "clue": "The Selm does not pass through Velmaran.", "type": "required", "role": "exclusion",
         "candidate": "Selm", "constraints": ["C2"], "status": "grounded",
         "evidence": [{"doc_id": "demo_doc_003", "source": "search", "supports": "full",
                       "quotes": ["It does not pass through Velmaran."]}],
         "rejected_docs": [], "note": "Rules out the other river the list document names near the capital."}],
    "candidates": [
        {"name": "Ardune", "role": "answer", "status": "qualifies", "hops": [1, 2], "note": ""},
        {"name": "Selm", "role": "rival", "status": "excluded", "hops": ["A1"], "note": "Joins the Ardune below Port Ilse."},
        {"name": "Varrow", "role": "rival", "status": "excluded", "hops": ["A2"],
         "note": "placeholder replaced below"}],
    "completeness": {"basis": "list_document", "doc_ids": ["demo_doc_005"],
                     "note": "The rivers list names three rivers; the Selm and the Varrow are each excluded by a hop."},
    "contamination": {"leak": False, "single_document": False, "doc_ids": [], "note": ""},
    "answer_check": {"status": "derives_gold", "note": "Oskeria -> capital Velmaran -> the Ardune runs through it."},
    "verdict": "pass_with_fixes", "fail_reasons": [],
    "summary": "Both hops grounded by their cited documents; the audit added A1 and A2 to exclude the other two rivers.",
}
SAMPLE_AUDIT["added_hops"].append(
    {"id": "A2", "clue": "The Varrow flows through the Brannoch marshes, not Velmaran.", "type": "required",
     "role": "exclusion", "candidate": "Varrow", "constraints": ["C2"], "status": "grounded",
     "evidence": [{"doc_id": "demo_doc_004", "source": "search", "supports": "full",
                   "quotes": ["flows south through the Brannoch marshes to the Gulf of Amberly"]}],
     "rejected_docs": [], "note": "Rules out the third river of the list document."})
SAMPLE_AUDIT["candidates"][2]["note"] = "Flows through the Brannoch marshes."
SAMPLE_AUDIT["constraints"][1]["covered_by"] = [2, "A1", "A2"]


def main():
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "run"
    (run / "inputs").mkdir(parents=True, exist_ok=True)
    cm = [sys.executable, str(HERE / "demo_cm.py")]
    cfg = {"corpus": {"name": "demo-corpus", "backend": "cli", "cli": {"search": cm + ["search"], "doc": cm + ["doc"]},
                      "min_gap_s": 0.0, "slots": 4},
           "sets": {"demo": {"input": "inputs/demo.jsonl", "family": "generic", "group": "released"},
                    "nm_demo": {"input": "inputs/demo_near_misses.jsonl", "family": "generic", "group": "near_miss"}}}
    (run / "cmass.json").write_text(json.dumps(cfg, indent=1))
    for name, rows in (("demo.jsonl", CANDIDATES), ("demo_near_misses.jsonl", NEAR_MISSES)):
        (run / "inputs" / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
    (run / "work" / "demo").mkdir(parents=True, exist_ok=True)
    (run / "work" / "demo" / "demo-1.audit.json").write_text(json.dumps(SAMPLE_AUDIT, indent=1))
    print(f"demo run: {run}\n  cd {run} && python3 {HERE.parent / 'tools' / 'validate.py'}")


if __name__ == "__main__":
    main()
