#!/usr/bin/env python3
"""Export only retrieval IDs from frozen, hash-pinned Piika artifacts.

Input manifest: {sources: [{dataset, model, query_id, path, sha256, outcome}]}.
query_id is the released ID, not an experiment's local sequential ID.
outcome is completed or model_unanswered, selected before running this tool.
Decoded qrels: JSONL dataset/query_id/doc_id/relevance.
No question, answer, reasoning, search query, snippet or document text is copied.
Output must be outside the public repository; publishing is a separate action.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from hop_groundedness import document_set, identifier, read_jsonl

MODELS = {'gpt': 'gpt-6.1-sol', 'gemma': 'gemma-4-31b-it', 'qwen': 'qwen3.5-9b'}
COUNTS = {'bcp': 80, 'trqa': 68, 'hotpotqa': 204, 'dsqa': 71}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def export(manifest: dict, qrels: list[dict], output: Path) -> dict:
    repo = Path(__file__).resolve().parents[1]
    output = output.resolve()
    if output == repo or repo in output.parents or output.exists():
        raise ValueError("Use a new private output directory outside this repository")
    gold = defaultdict(set)
    for row in qrels:
        ds, qid = identifier(row['dataset']), identifier(row['query_id'])
        if ds not in COUNTS or row['relevance'] <= 0:
            raise ValueError("Expected positive phase-2 qrels for the four datasets")
        gold[ds, qid].add(identifier(row['doc_id']))
    if any(sum(ds == dataset for ds, _ in gold) != n for dataset, n in COUNTS.items()):
        raise ValueError("Qrels do not cover the released question sets")
    groups, pools = defaultdict(list), defaultdict(lambda: defaultdict(dict))
    runs = []
    for source in manifest['sources']:
        ds, model, qid = map(identifier, (source['dataset'], source['model'], source['query_id']))
        if ds not in COUNTS or model not in MODELS or (ds, qid) not in gold:
            raise ValueError("Unknown dataset, model or released question ID")
        data = Path(source['path']).read_bytes()
        if sha(data) != source['sha256']:
            raise ValueError("Selected artifact SHA-256 mismatch")
        artifact = json.loads(data)
        meta, stats = artifact['metadata'], artifact['stats']
        if (not str(meta['model']).lower().endswith('/' + MODELS[model])
                or meta.get('supplied_docids') or meta.get('tool_interface') != 'pyserini-rest-2tool'
                or stats.get('pi_search_failures', 0)):
            raise ValueError("Unexpected model, supplied evidence, tool interface or failed retrieval")
        if source['outcome'] not in ('completed', 'model_unanswered'):
            raise ValueError("Do not export infrastructure-invalid intermediate attempts")
        if source['outcome'] == 'completed' and (artifact.get('status') != 'completed'
                or artifact.get('completion_source') != 'assistant_text' or stats.get('timed_out')
                or artifact.get('error')):
            raise ValueError("Completed source is not a completed assistant answer")
        counts = artifact['tool_call_counts']
        if set(counts) - {'search', 'read_document'}:
            raise ValueError("Unexpected tool in retrieval run")
        searches, reads = stats['search_calls'], stats['read_document_calls']
        if any(type(v) is not int or v < 0 for v in (searches, reads)):
            raise ValueError("Invalid call counts")
        if (counts.get('search', 0), counts.get('read_document', 0)) != (searches, reads):
            raise ValueError("Tool count fields disagree")
        if stats['tool_calls_total'] != searches + reads:
            raise ValueError("Tool count total disagrees")
        surfaced, opened = document_set(artifact['surfaced_docids']), document_set(artifact['opened_docids'])
        row = dict(dataset=ds, model=model, query_id=qid, outcome=source['outcome'],
                   source_artifact_sha256=source['sha256'],
                   surfaced_docids=sorted(surfaced), opened_docids=sorted(opened),
                   search_calls=searches, read_document_calls=reads)
        groups[ds, model].append(row)
        runs.append(row)
        for doc in surfaced | opened:
            pools[ds, qid][doc][model] = dict(surfaced=doc in surfaced, opened=doc in opened)
    if set(groups) != {(ds, model) for ds in COUNTS for model in MODELS}:
        raise ValueError("Expected all twelve retrieval runs")
    for (ds, model), rows in groups.items():
        ids = [r['query_id'] for r in rows]
        if len(ids) != COUNTS[ds] or len(set(ids)) != len(ids) or set(ids) != {q for d, q in gold if d == ds}:
            raise ValueError("Missing or duplicate selected questions")
    output.mkdir(parents=True)
    files = {}
    def save(name, rows):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = ''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows).encode()
        path.write_bytes(payload)
        files[name] = dict(sha256=sha(payload), bytes=len(payload), rows=len(rows))
    summaries = []
    for (ds, model), rows in sorted(groups.items()):
        rows.sort(key=lambda r: r['query_id'])
        save(f'runs/{ds}/{model}.jsonl', rows)
        relevant = sum(len(set(r['surfaced_docids']) & gold[ds, r['query_id']]) for r in rows)
        summaries.append(dict(dataset=ds, model=model, questions=len(rows),
                             surfaced_query_doc_pairs=sum(len(r['surfaced_docids']) for r in rows),
                             opened_query_doc_pairs=sum(len(r['opened_docids']) for r in rows),
                             relevant_surfaced_pairs=relevant,
                             zero_search_result_questions=sum(not r['surfaced_docids'] for r in rows)))
    pool_summary = []
    for ds in COUNTS:
        rows = []
        for dataset, qid in sorted(pools):
            if dataset != ds:
                continue
            for doc, contributors in sorted(pools[dataset, qid].items()):
                rows.append(dict(dataset=ds, query_id=qid, doc_id=doc, systems=contributors,
                                 assessment_status='existing_positive' if doc in gold[ds, qid] else 'unjudged'))
        save(f'pools/{ds}.jsonl', rows)
        pool_summary.append(dict(dataset=ds, questions=COUNTS[ds], candidate_pairs=len(rows),
                                 existing_positive_pairs=sum(r['assessment_status']=='existing_positive' for r in rows),
                                 unjudged_pairs=sum(r['assessment_status']=='unjudged' for r in rows)))
    receipt = dict(schema_version=1, input_manifest_sha256=sha(json.dumps(manifest, sort_keys=True).encode()),
                   source_selection='Frozen final revised retrieval attempts; not selected by correctness',
                   run_summaries=summaries, pool_summaries=pool_summary, files=files)
    (output/'manifest.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--qrels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = export(json.loads(args.manifest.read_text()), read_jsonl(args.qrels), args.output)
    print(json.dumps({k: v for k, v in result.items() if k != 'files'}, indent=2))
