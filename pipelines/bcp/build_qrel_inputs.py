#!/usr/bin/env python3
"""Build per-question Stage-3 qrel inputs from a Stage-2 projected-question list.

The Stage-2 JSONL is the source of truth. Stage 3 expands evidence for every
listed hop without changing the decomposition, including hops marked redundant.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parents[1]
DEFAULT_QUESTIONS_JSONL = REPO_ROOT / "data" / "bcp" / "questions.jsonl"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "work" / "bcp" / "qrel_inputs"
DEFAULT_MANIFEST = REPO_ROOT / "work" / "bcp" / "qrel_inputs.jsonl"


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def build_record(row: dict) -> dict:
    hops = []
    for i, hop in enumerate(row.get("hops", []), start=1):
        hops.append(
            {
                "hop_id": i,
                "clue": hop["clue"],
                "redundant": bool(hop.get("redundant", False)),
                "seed_doc_ids": dedupe(list(hop.get("supporting_doc_ids", []))),
            }
        )

    return {
        "record_id": str(row["record_id"]),
        "question": row["question"],
        "answer": row["answer"],
        "n_hops": len(hops),
        "n_necessary_hops": sum(not hop["redundant"] for hop in hops),
        "hops": hops,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_QUESTIONS_JSONL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--clean", action="store_true", help="Remove stale generated input JSON files.")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    with args.input.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                records.append(build_record(json.loads(line)))

    removed_stale = 0
    if args.clean:
        keep_ids = {record["record_id"] for record in records}
        for path in args.output_dir.glob("*.json"):
            if path.stem not in keep_ids:
                path.unlink()
                removed_stale += 1

    total_hops = 0
    total_necessary_hops = 0
    total_seed_docs = 0
    with args.manifest.open("w", encoding="utf-8") as manifest:
        for record in records:
            total_hops += record["n_hops"]
            total_necessary_hops += record["n_necessary_hops"]
            total_seed_docs += sum(len(hop["seed_doc_ids"]) for hop in record["hops"])
            out_path = args.output_dir / f"{record['record_id']}.json"
            out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            manifest.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(
        json.dumps(
            {
                "records": len(records),
                "hops": total_hops,
                "necessary_hops": total_necessary_hops,
                "redundant_hops": total_hops - total_necessary_hops,
                "seed_doc_ids_within_hops": total_seed_docs,
                "output_dir": str(args.output_dir),
                "manifest": str(args.manifest),
                "removed_stale_inputs": removed_stale,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
