#!/usr/bin/env python3
"""Validate the BCP release, qrels, review evidence, and website payload."""
from __future__ import annotations

import json
from pathlib import Path

from build_release_assets import DATA_DIR, DOCS_DATA_DIR, build_site_payload, parse_evidence_review, read_jsonl


EXPECTED = {
    "questions": 65,
    "required_hops": 320,
    "redundant_hops": 80,
    "hops": 400,
    "question_qrel_pairs": 7270,
    "hop_qrel_pairs": 9639,
    "distinct_qrel_docs": 7242,
    "evidence_excerpts": 721,
    "distinct_displayed_docs": 662,
}


def main() -> int:
    questions = read_jsonl(DATA_DIR / "questions.jsonl")
    qrels = {str(row["record_id"]): row for row in read_jsonl(DATA_DIR / "qrels.jsonl")}
    hop_qrels = {str(row["record_id"]): row for row in read_jsonl(DATA_DIR / "qrels_hops.jsonl")}
    review = parse_evidence_review(DATA_DIR / "evidence_review.md")
    errors: list[str] = []

    qids = [str(row["record_id"]) for row in questions]
    if len(qids) != len(set(qids)):
        errors.append("questions.jsonl contains duplicate record IDs")
    if set(qids) != set(qrels) or set(qids) != set(hop_qrels) or set(qids) != set(review):
        errors.append("record ID sets differ across release files")

    for question in questions:
        qid = str(question["record_id"])
        qrel_row = qrels.get(qid, {})
        hop_row = hop_qrels.get(qid, {})
        if question.get("question") != qrel_row.get("question") or question.get("answer") != qrel_row.get("answer"):
            errors.append(f"{qid}: question/answer mismatch between questions and qrels")
        source_hops = question.get("hops", [])
        public_hops = hop_row.get("hops", [])
        if question.get("n_hops") != len(source_hops) or len(source_hops) != len(public_hops):
            errors.append(f"{qid}: hop count mismatch")
            continue
        union: set[str] = set()
        for hop_id, (source_hop, public_hop) in enumerate(zip(source_hops, public_hops), start=1):
            if int(public_hop.get("hop_id", -1)) != hop_id:
                errors.append(f"{qid}:{hop_id}: hop ID mismatch")
            if source_hop.get("clue") != public_hop.get("clue"):
                errors.append(f"{qid}:{hop_id}: clue mismatch")
            if bool(source_hop.get("redundant")) != bool(public_hop.get("redundant")):
                errors.append(f"{qid}:{hop_id}: redundant marker mismatch")
            hop_docs = list(public_hop.get("qrel", []))
            if not hop_docs or len(hop_docs) != len(set(hop_docs)):
                errors.append(f"{qid}:{hop_id}: empty or duplicate hop qrel")
            union.update(hop_docs)
            seed_docs = set(source_hop.get("supporting_doc_ids", []))
            if not seed_docs.issubset(set(hop_docs)):
                errors.append(f"{qid}:{hop_id}: Stage-2 seed missing from hop qrel")
            excerpts = review.get(qid, {}).get(hop_id, [])
            if not excerpts:
                errors.append(f"{qid}:{hop_id}: no displayed evidence")
            for excerpt in excerpts:
                if excerpt["docid"] not in hop_docs:
                    errors.append(f"{qid}:{hop_id}: displayed document is outside the hop qrel")
                if not excerpt["snippet"] or not excerpt["source"] or not excerpt["support"]:
                    errors.append(f"{qid}:{hop_id}: incomplete displayed evidence metadata")
        if union != set(qrel_row.get("qrel", [])):
            errors.append(f"{qid}: question qrel is not the union of hop qrels")

    payload = build_site_payload(DATA_DIR)
    if payload["stats"] != EXPECTED:
        errors.append(f"release counts differ: {payload['stats']}")
    site_payload_path = DOCS_DATA_DIR / "bcp.json"
    if not site_payload_path.exists() or json.loads(site_payload_path.read_text(encoding="utf-8")) != payload:
        errors.append("docs/data/bcp.json is stale; run scripts/build_release_assets.py")
    manifest_path = DATA_DIR / "manifest.json"
    if not manifest_path.exists() or json.loads(manifest_path.read_text(encoding="utf-8")).get("stats") != EXPECTED:
        errors.append("manifest.json is missing or stale")

    trec_expected = {
        f"{question['record_id']} 0 {docid} 1"
        for question in payload["questions"]
        for docid in question["qrel"]
    }
    trec_path = DATA_DIR / "qrels.trec"
    trec_actual = set(trec_path.read_text(encoding="utf-8").splitlines()) if trec_path.exists() else set()
    if trec_actual != trec_expected:
        errors.append("qrels.trec is missing or inconsistent with qrels.jsonl")

    print(json.dumps({"stats": payload["stats"], "errors": errors}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
