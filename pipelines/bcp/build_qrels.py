#!/usr/bin/env python3
"""Assemble qrels JSONL from verified qrel-expansion runs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parents[1]
DEFAULT_QUESTIONS_JSONL = REPO_ROOT / "data" / "bcp" / "questions.jsonl"
DEFAULT_RUNS_DIR = REPO_ROOT / "work" / "bcp" / "qrel_runs"
DEFAULT_OUTPUT = REPO_ROOT / "work" / "bcp" / "qrels.generated.jsonl"


def sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def load_expected(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            qid = str(row["record_id"])
            out[qid] = {
                "record_id": qid,
                "question": row["question"],
                "answer": row["answer"],
                "hops": row.get("hops", []),
            }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions-jsonl", type=Path, default=DEFAULT_QUESTIONS_JSONL)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--hop-output", type=Path, default=None, help="Write internal per-hop qrels JSONL.")
    parser.add_argument("--include-hard-negatives", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Fail unless every expected record and hop is verified.")
    args = parser.parse_args()

    expected = load_expected(args.questions_jsonl)
    rows: list[dict] = []
    missing: list[str] = []
    unverified: list[str] = []
    uncovered_hops: list[str] = []
    hop_rows: list[dict] = []

    for qid, base in sorted(expected.items(), key=lambda item: sort_key(item[0])):
        run_path = args.runs_dir / f"{qid}.json"
        if not run_path.exists():
            missing.append(qid)
            continue
        run = json.loads(run_path.read_text(encoding="utf-8"))
        if run.get("status") != "verified" or "verified_qrel" not in run:
            unverified.append(qid)
            continue
        by_hop: dict[int, set[str]] = {i: set() for i in range(1, len(base["hops"]) + 1)}
        for judgment in run.get("verification", []):
            if not judgment.get("keep"):
                continue
            hop_id = int(judgment.get("hop_id", -1))
            if hop_id in by_hop and judgment.get("docid"):
                by_hop[hop_id].add(str(judgment["docid"]))
        for hop_id, docids in by_hop.items():
            if not docids:
                uncovered_hops.append(f"{qid}:{hop_id}")

        qrel = sorted({docid for docids in by_hop.values() for docid in docids})
        row = {
            "record_id": base["record_id"],
            "question": base["question"],
            "answer": base["answer"],
            "qrel": qrel,
        }
        if args.include_hard_negatives:
            row["hard_negatives"] = sorted(set(run.get("hard_negatives", [])))
        rows.append(row)
        hop_rows.append(
            {
                "record_id": qid,
                "hops": [
                    {
                        "hop_id": hop_id,
                        "clue": hop["clue"],
                        "redundant": bool(hop.get("redundant", False)),
                        "qrel": sorted(by_hop[hop_id]),
                    }
                    for hop_id, hop in enumerate(base["hops"], start=1)
                ],
            }
        )

    if args.strict and (missing or unverified or uncovered_hops):
        raise SystemExit(
            "Cannot build strict qrels: "
            f"missing={len(missing)} {missing[:20]} "
            f"unverified={len(unverified)} {unverified[:20]} "
            f"uncovered_hops={len(uncovered_hops)} {uncovered_hops[:20]}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    if args.hop_output is not None:
        args.hop_output.parent.mkdir(parents=True, exist_ok=True)
        with args.hop_output.open("w", encoding="utf-8") as out:
            for row in hop_rows:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")

    sizes = [len(row["qrel"]) for row in rows]
    summary = {
        "written": len(rows),
        "expected": len(expected),
        "missing_runs": len(missing),
        "unverified_runs": len(unverified),
        "uncovered_hops": len(uncovered_hops),
        "output": str(args.output),
        "hop_output": str(args.hop_output) if args.hop_output is not None else None,
        "qrel_size_min": min(sizes) if sizes else 0,
        "qrel_size_max": max(sizes) if sizes else 0,
        "qrel_size_avg": round(sum(sizes) / len(sizes), 2) if sizes else 0,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
