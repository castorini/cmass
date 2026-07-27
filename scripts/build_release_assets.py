#!/usr/bin/env python3
"""Build standard qrels and GitHub Pages data from the canonical BCP release."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "bcp"
DOCS_DATA_DIR = REPO_ROOT / "docs" / "data"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def parse_evidence_review(path: Path) -> dict[str, dict[int, list[dict[str, str]]]]:
    text = path.read_text(encoding="utf-8")
    question_matches = list(re.finditer(r'<a id="qid-(\d+)"></a>\n## QID \1\n', text))
    evidence: dict[str, dict[int, list[dict[str, str]]]] = {}
    for question_index, question_match in enumerate(question_matches):
        qid = question_match.group(1)
        question_end = question_matches[question_index + 1].start() if question_index + 1 < len(question_matches) else len(text)
        question_body = text[question_match.end():question_end]
        hop_matches = list(
            re.finditer(r'^### Hop (\d+) \((required|redundant / confirmatory)\)\n', question_body, re.M)
        )
        evidence[qid] = {}
        for hop_index, hop_match in enumerate(hop_matches):
            hop_id = int(hop_match.group(1))
            hop_end = hop_matches[hop_index + 1].start() if hop_index + 1 < len(hop_matches) else len(question_body)
            visible = question_body[hop_match.end():hop_end].split("<details>", 1)[0]
            lines = visible.splitlines()
            rows: list[dict[str, str]] = []
            cursor = 0
            while cursor < len(lines):
                doc_match = re.match(r'^\d+\. \*\*`([^`]+)`\*\*$', lines[cursor])
                if not doc_match:
                    cursor += 1
                    continue
                docid = doc_match.group(1)
                source = support = snippet = note = ""
                cursor += 1
                while cursor < len(lines) and not re.match(r'^\d+\. \*\*`', lines[cursor]):
                    label_match = re.match(r'^\s+_(.+?) excerpt; support: ([^_]+)_$', lines[cursor])
                    quote_match = re.match(r'^\s+> (.*)$', lines[cursor])
                    note_match = re.match(r'^\s+_Verifier note:_\s*(.*)$', lines[cursor])
                    if label_match:
                        source, support = label_match.groups()
                    elif quote_match:
                        snippet = quote_match.group(1)
                    elif note_match:
                        note = note_match.group(1)
                    cursor += 1
                rows.append(
                    {
                        "docid": docid,
                        "source": source,
                        "support": support,
                        "snippet": snippet,
                        "note": note,
                    }
                )
            evidence[qid][hop_id] = rows
    return evidence


def build_site_payload(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    questions = read_jsonl(data_dir / "questions.jsonl")
    qrels = {str(row["record_id"]): row for row in read_jsonl(data_dir / "qrels.jsonl")}
    hop_qrels = {str(row["record_id"]): row for row in read_jsonl(data_dir / "qrels_hops.jsonl")}
    review = parse_evidence_review(data_dir / "evidence_review.md")

    payload_questions = []
    displayed_docs: set[str] = set()
    all_qrel_docs: set[str] = set()
    qrel_pairs = hop_pairs = excerpt_count = required = redundant = 0
    for question in questions:
        qid = str(question["record_id"])
        question_qrel = list(qrels[qid]["qrel"])
        all_qrel_docs.update(question_qrel)
        qrel_pairs += len(question_qrel)
        source_hops = question.get("hops", [])
        public_hops = hop_qrels[qid]["hops"]
        hops = []
        for hop_index, (source_hop, public_hop) in enumerate(zip(source_hops, public_hops), start=1):
            excerpts = review[qid][hop_index]
            excerpt_count += len(excerpts)
            displayed_docs.update(excerpt["docid"] for excerpt in excerpts)
            hop_pairs += len(public_hop["qrel"])
            if source_hop.get("redundant"):
                redundant += 1
            else:
                required += 1
            hops.append(
                {
                    "hop_id": hop_index,
                    "clue": source_hop["clue"],
                    "redundant": bool(source_hop.get("redundant", False)),
                    "seed_doc_ids": list(source_hop.get("supporting_doc_ids", [])),
                    "qrel": list(public_hop["qrel"]),
                    "excerpts": excerpts,
                }
            )
        payload_questions.append(
            {
                "record_id": qid,
                "question": question["question"],
                "answer": question["answer"],
                "qrel": question_qrel,
                "hops": hops,
            }
        )

    return {
        "release": "bcp-climbmix-v1",
        "corpus": "climbmix-400b",
        "stats": {
            "questions": len(payload_questions),
            "required_hops": required,
            "redundant_hops": redundant,
            "hops": required + redundant,
            "question_qrel_pairs": qrel_pairs,
            "hop_qrel_pairs": hop_pairs,
            "distinct_qrel_docs": len(all_qrel_docs),
            "evidence_excerpts": excerpt_count,
            "distinct_displayed_docs": len(displayed_docs),
        },
        "questions": payload_questions,
    }


def write_outputs(payload: dict[str, Any], data_dir: Path, docs_data_dir: Path) -> None:
    docs_data_dir.mkdir(parents=True, exist_ok=True)
    (docs_data_dir / "bcp.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    trec_lines = []
    for question in payload["questions"]:
        for docid in question["qrel"]:
            trec_lines.append(f"{question['record_id']} 0 {docid} 1")
    (data_dir / "qrels.trec").write_text("\n".join(trec_lines) + "\n", encoding="utf-8")

    manifest = {
        "release": payload["release"],
        "corpus": payload["corpus"],
        "stats": payload["stats"],
        "files": {
            "questions": "questions.jsonl",
            "question_qrels": "qrels.jsonl",
            "hop_qrels": "qrels_hops.jsonl",
            "trec_qrels": "qrels.trec",
            "evidence_review": "evidence_review.md",
        },
    }
    (data_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--docs-data-dir", type=Path, default=DOCS_DATA_DIR)
    args = parser.parse_args()
    payload = build_site_payload(args.data_dir)
    write_outputs(payload, args.data_dir, args.docs_data_dir)
    print(json.dumps(payload["stats"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
