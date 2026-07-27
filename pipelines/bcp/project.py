#!/usr/bin/env python3
"""Project BrowseComp-Plus questions onto ClimbMix with OAuth-backed Codex."""
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
DEFAULT_INPUT_DIR = REPO_ROOT / "work" / "bcp" / "inputs"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "work" / "bcp" / "projections"
DEFAULT_CM = PIPELINE_DIR / "cm.py"

EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["doc_id", "snippet"],
    "properties": {"doc_id": {"type": "string"}, "snippet": {"type": "string"}},
}
HOP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clue", "supported", "necessary", "corpus_evidence", "note"],
    "properties": {
        "clue": {"type": "string"},
        "supported": {"type": "boolean"},
        "necessary": {"type": "boolean"},
        "corpus_evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
        "note": {"type": "string"},
    },
}
PROJECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "record_id",
        "question",
        "answer",
        "verdict",
        "answer_uniquely_inferable",
        "hops",
        "reasoning_path",
        "rationale",
    ],
    "properties": {
        "record_id": {"type": "string"},
        "question": {"type": "string"},
        "answer": {"type": "string"},
        "verdict": {"type": "string", "enum": ["PROJECTABLE", "PARTIAL", "NOT"]},
        "answer_uniquely_inferable": {"type": "boolean"},
        "hops": {"type": "array", "minItems": 1, "items": HOP_SCHEMA},
        "reasoning_path": {"type": "string"},
        "rationale": {"type": "string"},
    },
}


def _prompt(task: dict[str, Any], input_path: Path, cm_path: Path) -> str:
    return "\n".join(
        [
            "Project one BrowseComp-Plus question onto the fixed ClimbMix corpus.",
            "Use the provided documents only as hints for decomposing the reasoning chain.",
            "Corpus support must come from ClimbMix documents retrieved with the supplied helper.",
            "Do not browse the web and do not treat model knowledge as evidence.",
            "",
            "For each atomic hop:",
            f'  python3 "{cm_path}" search "<query>" 30 700',
            f'  python3 "{cm_path}" doc <shard_docid>',
            "Issue several entity-anchored query variants and deepen retrieval for difficult hops.",
            "Copy a short verbatim snippet from every cited ClimbMix document.",
            "Mark a hop necessary when removing it weakens unique identification of the answer.",
            "",
            "Verdicts:",
            "PROJECTABLE: every hop is corpus-supported and the answer is uniquely derivable.",
            "PARTIAL: some evidence exists, but at least one hop or uniqueness is weak.",
            "NOT: decisive evidence is absent or contradicts the proposed answer.",
            "",
            "Read the input file first; its provided documents are decomposition hints, not ClimbMix evidence.",
            f'input_file: "{input_path}"',
            "Return the complete structured result. Preserve the input question and answer verbatim.",
            "",
            f"record_id: {task['record_id']}",
            f"question: {task['question']}",
            f"answer: {task['answer']}",
        ]
    )


def _parse_qids(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _paths(input_dir: Path, qids: list[str], limit: int | None) -> list[Path]:
    paths = [input_dir / f"{qid}.json" for qid in qids] if qids else list(input_dir.glob("*.json"))
    paths.sort(key=lambda path: (0, int(path.stem)) if path.stem.isdigit() else (1, path.stem))
    return paths[:limit] if limit is not None else paths


def _validate(result: dict[str, Any], task: dict[str, Any]) -> None:
    if str(result.get("record_id")) != str(task["record_id"]):
        raise ValueError("Codex returned the wrong record_id")
    if result.get("question") != task.get("question") or result.get("answer") != task.get("answer"):
        raise ValueError("Codex changed the canonical question or answer")
    for hop in result.get("hops", []):
        for evidence in hop.get("corpus_evidence", []):
            if not str(evidence.get("doc_id", "")).startswith("shard_"):
                raise ValueError(f"Invalid ClimbMix doc ID: {evidence.get('doc_id')}")
            if not str(evidence.get("snippet", "")).strip():
                raise ValueError("Empty evidence snippet")
    if result.get("verdict") == "PROJECTABLE":
        if not result.get("answer_uniquely_inferable"):
            raise ValueError("PROJECTABLE result is not uniquely inferable")
        if any(not hop.get("supported") or not hop.get("corpus_evidence") for hop in result.get("hops", [])):
            raise ValueError("PROJECTABLE result contains an unsupported hop")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cm", type=Path, default=DEFAULT_CM)
    parser.add_argument("--qids", default="")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=4)
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

    selected = _paths(args.input_dir, _parse_qids(args.qids), args.limit)
    if args.dry_run:
        print(json.dumps({"selected": [path.stem for path in selected], "count": len(selected)}, indent=2))
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)

    def run_one(input_path: Path) -> dict[str, str]:
        output_path = args.output_dir / input_path.name
        if output_path.exists() and not args.force:
            return {"record_id": input_path.stem, "status": "skipped"}
        task = json.loads(input_path.read_text(encoding="utf-8"))
        result = codex_exec_json(
            _prompt(task, input_path.resolve(), args.cm.resolve()),
            PROJECTION_SCHEMA,
            cwd=REPO_ROOT,
            codex_bin=args.codex_bin,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            sandbox=args.sandbox,
            timeout=args.timeout,
            retries=args.retries,
            retry_delay=args.retry_delay,
        )
        _validate(result, task)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"record_id": str(task["record_id"]), "status": str(result["verdict"])}

    results: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
        futures = {executor.submit(run_one, path): path for path in selected}
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            print(json.dumps(row), flush=True)
    print(json.dumps({"processed": len(results), "output_dir": str(args.output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
