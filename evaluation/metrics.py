#!/usr/bin/env python3
"""Aggregate private normalized records; never emit question text or per-ID data.

Each record: query_id, outcome ('completed' or 'model_unanswered'), correct
(scalar) or strict_correct/relaxed_correct (DSQA), surfaced_docids,
search_calls, read_document_calls. Select and validate attempts before calling.
Decoded qrels are JSONL query_id/doc_id/relevance records from the HF release.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def aggregate(records: list[dict], qrels: list[dict], dsqa: bool = False) -> dict:
    gold = {}
    for row in qrels:
        if row["relevance"] > 0:
            gold.setdefault(str(row["query_id"]), set()).add(row["doc_id"])
    ids = [str(row["query_id"]) for row in records]
    if not ids or len(ids) != len(set(ids)) or set(ids) != set(gold):
        raise ValueError("Expected exactly one selected outcome per qrel question")
    sums = {"strict_correct": 0, "relaxed_correct": 0} if dsqa else {"correct": 0}
    recall, searches, reads, unanswered = 0.0, 0, 0, 0
    for row in records:
        if row["outcome"] not in ("completed", "model_unanswered"):
            raise ValueError("Resolve infrastructure errors before reporting a final table")
        no_answer = row["outcome"] == "model_unanswered"
        unanswered += no_answer
        for key in sums:
            value = row[key]
            if type(value) is not bool or (no_answer and value):
                raise ValueError("Invalid verdict or successful unanswered outcome")
            sums[key] += value
        if dsqa and row["strict_correct"] and not row["relaxed_correct"]:
            raise ValueError("Strict success must imply relaxed success")
        for key in ("search_calls", "read_document_calls"):
            if type(row[key]) is not int or row[key] < 0:
                raise ValueError("Invalid tool-call count")
        pool = row["surfaced_docids"]
        if not isinstance(pool, list) or not all(isinstance(i, str) for i in pool):
            raise ValueError("surfaced_docids must contain document IDs")
        relevant = gold[str(row["query_id"])]
        recall += len(set(pool) & relevant) / len(relevant)
        searches += row["search_calls"]
        reads += row["read_document_calls"]
    n = len(records)
    return dict(questions=n, completed_answers=n-unanswered, model_unanswered=unanswered,
                **sums, **{k.replace("correct", "accuracy_percent"): 100*v/n for k, v in sums.items()},
                recall_at_all_percent=100*recall/n,
                mean_tool_calls=(searches+reads)/n,
                search_calls=searches, read_document_calls=reads)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--qrels", type=Path, required=True)
    parser.add_argument("--dsqa", action="store_true")
    args = parser.parse_args()
    load = lambda p: [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    print(json.dumps(aggregate(load(args.records), load(args.qrels), args.dsqa), indent=2))
