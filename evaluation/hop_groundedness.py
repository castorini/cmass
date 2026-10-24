#!/usr/bin/env python3
"""Evaluate retrieval hop coverage, not whether a model used the evidence.

Private inputs are JSONL. Runs: dataset, model, query_id, surfaced_docids.
Hops: dataset, query_id, hop_id, role (inclusion/exclusion), doc_ids.
Only aggregate statistics are printed. No question or hop text is needed.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def identifier(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("IDs must be nonempty strings")
    return value


def document_set(value):
    if not isinstance(value, list):
        raise ValueError("Document IDs must be a list")
    return {identifier(item) for item in value}


def evaluate(runs: list[dict], hops: list[dict]) -> list[dict]:
    catalogs = defaultdict(dict)
    for hop in hops:
        key = (identifier(hop['dataset']), identifier(hop['query_id']))
        hid = identifier(hop['hop_id'])
        if hid in catalogs[key] or hop['role'] not in ('inclusion', 'exclusion'):
            raise ValueError("Duplicate hop or unknown role")
        catalogs[key][hid] = (hop['role'], document_set(hop['doc_ids']))
    if not catalogs:
        raise ValueError("A complete, nonempty retained-hop inventory is required")
    groups = defaultdict(dict)
    for run in runs:
        ds, model, qid = map(identifier, (run['dataset'], run['model'], run['query_id']))
        if qid in groups[ds, model] or (ds, qid) not in catalogs:
            raise ValueError("Duplicate or unexpected run question")
        groups[ds, model][qid] = document_set(run['surfaced_docids'])
    if not groups or {ds for ds, _ in groups} != {ds for ds, _ in catalogs}:
        raise ValueError("Run and hop datasets must match")
    output = []
    for (ds, model), records in sorted(groups.items()):
        expected = {qid for dataset, qid in catalogs if dataset == ds}
        if set(records) != expected:
            raise ValueError("Each model must cover every question in its hop inventory")
        fractions, role_fractions = [], {'inclusion': [], 'exclusion': []}
        total = hit_total = fully = zero = empty = 0
        for qid, pool in sorted(records.items()):
            hs = catalogs[ds, qid]
            hits = {hid: int(bool(pool & docs)) for hid, (_, docs) in hs.items()}
            # Equivalent inverse calculation catches implementation drift.
            inverse = {hid for doc in pool for hid, (_, docs) in hs.items() if doc in docs}
            if inverse != {hid for hid, hit in hits.items() if hit}:
                raise ValueError("Hop/document edge calculations disagree")
            count = sum(hits.values())
            fractions.append(count / len(hs))
            total += len(hs)
            hit_total += count
            fully += count == len(hs)
            zero += count == 0
            empty += sum(not docs for _, docs in hs.values())
            for role in role_fractions:
                eligible = [hid for hid, (label, _) in hs.items() if label == role]
                if eligible:
                    role_fractions[role].append(sum(hits[hid] for hid in eligible) / len(eligible))
        row = dict(dataset=ds, model=model, questions=len(records),
                   hop_coverage_percent=100 * sum(fractions) / len(fractions),
                   total_hops=total, hit_hops=hit_total, fully_covered_questions=fully,
                   zero_covered_questions=zero, hops_without_qrel_docs=empty)
        for role, values in role_fractions.items():
            row[role + '_coverage_percent'] = 100 * sum(values) / len(values) if values else None
            row[role + '_questions'] = len(values)
        output.append(row)
    return output


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=Path, required=True)
    parser.add_argument('--hops', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(read_jsonl(args.runs), read_jsonl(args.hops)), indent=2))
