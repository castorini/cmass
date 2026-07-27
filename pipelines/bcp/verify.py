#!/usr/bin/env python3
"""Verify projected hops over known ClimbMix documents with a no-search panel."""
from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from codex_runner import codex_exec_json


PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parents[1]
DEFAULT_PROJECTIONS_DIR = REPO_ROOT / "work" / "bcp" / "projections"
DEFAULT_RUNS_DIR = REPO_ROOT / "work" / "bcp" / "verification"
DEFAULT_OUTPUT = REPO_ROOT / "work" / "bcp" / "verified_questions.jsonl"
DEFAULT_CM = PIPELINE_DIR / "cm.py"

JUDGED_HOP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["hop_id", "supported", "necessary", "accepted_doc_ids", "note"],
    "properties": {
        "hop_id": {"type": "integer"},
        "supported": {"type": "boolean"},
        "necessary": {"type": "boolean"},
        "accepted_doc_ids": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
    },
}
JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["record_id", "answer_uniquely_derivable", "hops", "keep", "rationale"],
    "properties": {
        "record_id": {"type": "string"},
        "answer_uniquely_derivable": {"type": "boolean"},
        "hops": {"type": "array", "items": JUDGED_HOP_SCHEMA},
        "keep": {"type": "boolean"},
        "rationale": {"type": "string"},
    },
}


def _prompt(projection: dict[str, Any], cm_path: Path, judge_index: int) -> str:
    compact_hops = []
    for hop_id, hop in enumerate(projection.get("hops", []), start=1):
        compact_hops.append(
            {
                "hop_id": hop_id,
                "clue": hop.get("clue", ""),
                "projector_necessary": bool(hop.get("necessary")),
                "candidate_evidence": hop.get("corpus_evidence", []),
            }
        )
    return "\n".join(
        [
            f"You are independent projection verifier {judge_index}.",
            "Judge only the handed-in ClimbMix document IDs. Do not search for new documents.",
            "You may fetch a known document with:",
            f'  python3 "{cm_path}" doc <shard_docid>',
            "",
            "A hop is supported only when an accepted document directly states or soundly establishes it.",
            "Reject topical mentions, outside-knowledge bridges, contradictions, and empty snippets.",
            "A hop is necessary when removing it materially weakens unique identification of the answer.",
            "KEEP only if every listed hop is supported and the answer is uniquely derivable from the chain.",
            "Return every hop exactly once and accept only candidate doc IDs supplied below.",
            "",
            f"record_id: {projection['record_id']}",
            f"question: {projection['question']}",
            f"answer: {projection['answer']}",
            "hops_json:",
            json.dumps(compact_hops, ensure_ascii=False, indent=2),
        ]
    )


def _candidate_ids(projection: dict[str, Any], hop_id: int) -> set[str]:
    hop = projection["hops"][hop_id - 1]
    return {str(item.get("doc_id")) for item in hop.get("corpus_evidence", []) if item.get("doc_id")}


def _validate_judgment(judgment: dict[str, Any], projection: dict[str, Any]) -> None:
    if str(judgment.get("record_id")) != str(projection["record_id"]):
        raise ValueError("Verifier returned the wrong record_id")
    expected = set(range(1, len(projection.get("hops", [])) + 1))
    returned = {int(hop.get("hop_id", -1)) for hop in judgment.get("hops", [])}
    if returned != expected:
        raise ValueError(f"Verifier returned hop IDs {sorted(returned)}; expected {sorted(expected)}")
    for hop in judgment["hops"]:
        hop_id = int(hop["hop_id"])
        extras = set(hop.get("accepted_doc_ids", [])) - _candidate_ids(projection, hop_id)
        if extras:
            raise ValueError(f"Verifier invented document IDs for hop {hop_id}: {sorted(extras)}")


def _aggregate(panel: list[dict[str, Any]], projection: dict[str, Any]) -> dict[str, Any]:
    majority = len(panel) // 2 + 1
    unique_votes = sum(bool(judge.get("answer_uniquely_derivable")) for judge in panel)
    aggregate_hops = []
    for hop_id, source_hop in enumerate(projection.get("hops", []), start=1):
        votes = [next(hop for hop in judge["hops"] if int(hop["hop_id"]) == hop_id) for judge in panel]
        accepted_counts: dict[str, int] = {}
        for vote in votes:
            for docid in vote.get("accepted_doc_ids", []):
                accepted_counts[docid] = accepted_counts.get(docid, 0) + 1
        accepted = sorted(docid for docid, count in accepted_counts.items() if count >= majority)
        supported_votes = sum(bool(vote.get("supported")) for vote in votes)
        necessary_votes = sum(bool(vote.get("necessary")) for vote in votes)
        aggregate_hops.append(
            {
                "hop_id": hop_id,
                "clue": source_hop.get("clue", ""),
                "supported": supported_votes >= majority and bool(accepted),
                "necessary": necessary_votes >= majority,
                "support_votes": supported_votes,
                "necessity_votes": necessary_votes,
                "supporting_doc_ids": accepted,
            }
        )
    keep = unique_votes >= majority and all(hop["supported"] for hop in aggregate_hops)
    return {
        "record_id": str(projection["record_id"]),
        "question": projection["question"],
        "answer": projection["answer"],
        "judges": len(panel),
        "majority": majority,
        "unique_votes": unique_votes,
        "hops": aggregate_hops,
        "keep": keep,
    }


def _parse_qids(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",") if part.strip()}


def _write_verified_list(runs_dir: Path, output: Path) -> int:
    rows = []
    for path in runs_dir.glob("*.json"):
        run = json.loads(path.read_text(encoding="utf-8"))
        aggregate = run.get("aggregate", {})
        if not aggregate.get("keep"):
            continue
        hops = [
            {
                "clue": hop["clue"],
                "redundant": not bool(hop["necessary"]),
                "supporting_doc_ids": hop["supporting_doc_ids"],
            }
            for hop in aggregate["hops"]
        ]
        rows.append(
            {
                "record_id": str(aggregate["record_id"]),
                "question": aggregate["question"],
                "answer": aggregate["answer"],
                "n_hops": len(hops),
                "hops": hops,
                "source": "cmass_projection_pipeline",
            }
        )
    rows.sort(key=lambda row: (0, int(row["record_id"])) if row["record_id"].isdigit() else (1, row["record_id"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projections-dir", type=Path, default=DEFAULT_PROJECTIONS_DIR)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cm", type=Path, default=DEFAULT_CM)
    parser.add_argument("--qids", default="")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--judges", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--model", default=os.environ.get("CMASS_CODEX_MODEL", "gpt-5.5"))
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high", "xhigh"],
        default=os.environ.get("CMASS_CODEX_REASONING_EFFORT", "medium"),
    )
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    parser.add_argument("--sandbox", default="danger-full-access")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--retry-delay", type=float, default=60.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.judges < 1:
        raise SystemExit("--judges must be at least 1")

    wanted = _parse_qids(args.qids)
    paths = [path for path in args.projections_dir.glob("*.json") if not wanted or path.stem in wanted]
    paths.sort(key=lambda path: (0, int(path.stem)) if path.stem.isdigit() else (1, path.stem))
    if args.limit is not None:
        paths = paths[: args.limit]
    if args.dry_run:
        print(json.dumps({"selected": [path.stem for path in paths], "count": len(paths)}, indent=2))
        return 0
    args.runs_dir.mkdir(parents=True, exist_ok=True)

    def run_one(path: Path) -> dict[str, str]:
        output_path = args.runs_dir / path.name
        if output_path.exists() and not args.force:
            return {"record_id": path.stem, "status": "skipped"}
        projection = json.loads(path.read_text(encoding="utf-8"))
        if projection.get("verdict") != "PROJECTABLE":
            return {"record_id": path.stem, "status": "not_projectable"}
        panel = []
        for judge_index in range(1, args.judges + 1):
            judgment = codex_exec_json(
                _prompt(projection, args.cm.resolve(), judge_index),
                JUDGE_SCHEMA,
                cwd=REPO_ROOT,
                codex_bin=args.codex_bin,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                sandbox=args.sandbox,
                timeout=args.timeout,
                retries=args.retries,
                retry_delay=args.retry_delay,
            )
            _validate_judgment(judgment, projection)
            panel.append(judgment)
        run = {"projection": projection, "panel": panel, "aggregate": _aggregate(panel, projection)}
        output_path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"record_id": path.stem, "status": "keep" if run["aggregate"]["keep"] else "reject"}

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
        futures = {executor.submit(run_one, path): path for path in paths}
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            print(json.dumps(row), flush=True)
    kept = _write_verified_list(args.runs_dir, args.output)
    print(json.dumps({"processed": len(results), "kept": kept, "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
