#!/usr/bin/env python3
"""Step 0: join the reviewer workbook with the verified-questions JSONL.

Emits one candidate pool per qid: every (hop, document) row the workbook lists,
annotated with the human judgments that drive the finalization rules.

Rules encoded here:
  * scope         - the qids present in the questions JSONL; workbook-only tabs
                    are dropped.
  * candidates    - ALL workbook rows, not only the rows the JSONL already lists
                    as supporting. The workbook is the wider pool.
  * skipped hops  - a hop is dropped ONLY when the reviewer judged the
                    decomposition "No" AND the note says the hop is not needed.
                    A bare "No" is kept and judged normally; if it ends with no
                    supporting document, step 4 flags it rather than silently
                    discarding it. Both conditions are required, because a note
                    about surplus detail *inside* a clue can otherwise be
                    misread as dropping the hop.
  * force-include - rows voted "Supports" / "Partial support" are guaranteed to
                    reach the final qrels.
  * force-exclude - rows voted "Does not support" are dropped unless a later
                    step records an explicit, conservative override.

Usage:
  python3 step0_build_candidates.py --workbook review.xlsm --questions verified.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import openpyxl

import config

FORCE_INCLUDE = {"Supports", "Partial support"}
FORCE_EXCLUDE = {"Does not support"}
SKIP_DECOMPOSITION = {"No"}
SKIP_NOTE = "not needed"

# The workbook appends a machine-generated review aid to each hop clue cell.
_AID_SEPARATOR = "\n\nAssistant:"


def _cell(row: tuple, index: int):
    return row[index] if row and len(row) > index else None


def _clean_clue(value: object) -> str:
    return str(value).split(_AID_SEPARATOR)[0].strip()


def parse_tab(worksheet) -> tuple[list[dict], list[dict]]:
    """Split a QID tab into its decomposition rows and its qrel rows."""
    rows = list(worksheet.iter_rows(values_only=True))
    decomp_header = qrel_header = None
    for i, row in enumerate(rows):
        if _cell(row, 0) == "Hop" and _cell(row, 2) == "Hop clue and assistant review aid":
            decomp_header = i
        if _cell(row, 0) == "Hop" and _cell(row, 1) == "Document ID":
            qrel_header = i
            break
    if decomp_header is None or qrel_header is None:
        raise ValueError(f"{worksheet.title}: could not locate both section headers")

    hops = []
    for row in rows[decomp_header + 1 : qrel_header]:
        marker = _cell(row, 0)
        # The section-2 banner sits inside this range; it is not a hop.
        if marker is None or str(marker).startswith("2 "):
            continue
        hops.append({
            "index": len(hops),
            "hop_number": int(float(marker)),
            "clue": _clean_clue(_cell(row, 2)),
            "hop_type": _cell(row, 1),
            "decomposition_judgment": _cell(row, 5),
            "decomposition_note": _cell(row, 6),
        })

    qrels = []
    for row in rows[qrel_header + 1 :]:
        if _cell(row, 0) is None:
            break
        qrels.append({
            "hop_index": int(float(_cell(row, 0))) - 1,
            "doc_id": _cell(row, 1),
            "assistant_flag": _cell(row, 2),
            "human_judgment": _cell(row, 5),
            "human_note": _cell(row, 6),
        })
    return hops, qrels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workbook", default=config.WORKBOOK,
                        help="reviewer workbook .xlsm (env QRELS_WORKBOOK)")
    parser.add_argument("--questions", default=config.QUESTIONS,
                        help="verified-questions JSONL (env QRELS_QUESTIONS)")
    config.add_work_dir(parser)
    parser.add_argument("--out", help="default <work-dir>/candidates.json")
    args = parser.parse_args()

    workbook_path = config.require(args.workbook, "--workbook", "QRELS_WORKBOOK")
    questions_path = config.require(args.questions, "--questions", "QRELS_QUESTIONS")
    out = Path(args.out) if args.out else config.paths(args)["candidates"]

    records = {r["record_id"]: r
               for r in (json.loads(line) for line in Path(questions_path).open() if line.strip())}
    workbook = openpyxl.load_workbook(workbook_path, data_only=True, read_only=True)

    out_map: dict[str, dict] = {}
    for qid, record in records.items():
        hops, qrels = parse_tab(workbook[f"QID {qid}"])
        if len(hops) != len(record["hops"]):
            raise ValueError(f"QID {qid}: {len(hops)} workbook hops vs {len(record['hops'])} JSONL hops")
        for hop, json_hop in zip(hops, record["hops"]):
            if hop["clue"] != json_hop["clue"].strip():
                raise ValueError(f"QID {qid} hop {hop['hop_number']}: clue text diverged between files")
            note = str(hop["decomposition_note"] or "").lower()
            judged_no = hop["decomposition_judgment"] in SKIP_DECOMPOSITION
            hop["skip"] = judged_no and SKIP_NOTE in note
            hop["needs_review_if_empty"] = judged_no and not hop["skip"]
            hop["in_jsonl_supporting"] = sorted(json_hop["supporting_doc_ids"])
            hop["in_jsonl_human_confirmed"] = sorted(json_hop.get("human_confirmed_doc_ids", []))

        live = {h["index"] for h in hops if not h["skip"]}
        for row in qrels:
            row["hop_skipped"] = row["hop_index"] not in live
            row["force_include"] = row["human_judgment"] in FORCE_INCLUDE and not row["hop_skipped"]
            row["force_exclude"] = row["human_judgment"] in FORCE_EXCLUDE

        out_map[qid] = {
            "qid": qid,
            "question": record["question"],
            "answer": record["answer"],
            "source": record.get("source"),
            "hops": hops,
            "candidates": qrels,
        }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(out_map, indent=1))

    n_rows = sum(len(v["candidates"]) for v in out_map.values())
    n_docs = len({r["doc_id"] for v in out_map.values() for r in v["candidates"]})
    n_live = sum(1 for v in out_map.values() for r in v["candidates"] if not r["hop_skipped"])
    print(f"qids              : {len(out_map)}")
    print(f"hops              : {sum(len(v['hops']) for v in out_map.values())} "
          f"({sum(1 for v in out_map.values() for h in v['hops'] if h['skip'])} skipped)")
    print(f"candidate rows    : {n_rows} ({n_live} on live hops)")
    print(f"distinct documents: {n_docs}")
    print(f"force-include rows: {sum(1 for v in out_map.values() for r in v['candidates'] if r['force_include'])}")
    print(f"force-exclude rows: {sum(1 for v in out_map.values() for r in v['candidates'] if r['force_exclude'])}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
