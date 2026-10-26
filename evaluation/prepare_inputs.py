#!/usr/bin/env python3
"""Prepare private Piika inputs from the pinned, obfuscated four-dataset release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.deobfuscate import decode_row

REVISION = "09b13bd392f499ab9af21618bcde3284a69cbcbb"
COUNTS = {"bcp": 80, "trqa": 68, "hotpotqa": 204, "dsqa": 71}
QREL_COUNTS = {"bcp": 10968, "trqa": 7512, "hotpotqa": 10033, "dsqa": 8698}


def fetch(dataset: str, name: str) -> tuple[list[dict], str]:
    url = f"https://huggingface.co/datasets/castorini/cmass/resolve/{REVISION}/{dataset}/{name}"
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    return [decode_row(json.loads(line)) for line in data.splitlines() if line.strip()], hashlib.sha256(data).hexdigest()


def prepare(dataset: str, output: Path) -> None:
    output = output.resolve()
    if output == REPO or REPO in output.parents:
        raise ValueError("Decoded benchmark data must be written outside this public repository")
    if output.exists():
        raise FileExistsError("Use a new private output directory; no inputs are overwritten")
    queries, query_sha = fetch(dataset, "queries.jsonl")
    qrels, qrel_sha = fetch(dataset, "qrels.jsonl")
    ids = {str(q["id"]) for q in queries}
    if len(queries) != COUNTS[dataset] or len(ids) != len(queries):
        raise ValueError("Unexpected question count or duplicate IDs")
    pairs = {(str(q["query_id"]), q["doc_id"]) for q in qrels}
    if len(pairs) != len(qrels) or len(pairs) != QREL_COUNTS[dataset]:
        raise ValueError("Unexpected qrel count or duplicate pairs")
    if {str(q["query_id"]) for q in qrels} != ids or {q["relevance"] for q in qrels} != {1}:
        raise ValueError("Qrel membership or relevance mismatch")
    sets, set_sha = {}, None
    if dataset == "dsqa":
        rows, set_sha = fetch(dataset, "answer_sets.jsonl")
        sets = {str(row["id"]): row["answer_set"] for row in rows}
        if len(rows) != len(ids) or set(sets) != ids or any(not v for v in sets.values()):
            raise ValueError("DSQA answer-set membership mismatch")
    # Separate directories prevent accidentally passing gold fields to generation.
    os.umask(0o077)
    (output / "generation").mkdir(parents=True)
    (output / "evaluation").mkdir()
    normalized = [(str(q["id"]), re.sub(r"[\t\r\n]+", " ", q["question"]).strip(), q["answer"]) for q in queries]
    if any(not i or re.search(r"[\s/]", i) or not question for i, question, _ in normalized):
        raise ValueError("Invalid question ID or empty question")
    def save(relative: str, text: str) -> None:
        (output / relative).write_text(text, encoding="utf-8")
    save("generation/questions.tsv", "".join(f"{i}\t{question}\n" for i, question, _ in normalized))
    gold = []
    for i, question, answer in normalized:
        row = dict(query_id=i, query=question, question=question, answer=answer)
        if dataset == "dsqa":
            row.update(answer_set=sets[i], ordered=False)
        gold.append(row)
    save("evaluation/ground_truth.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in gold))
    save("evaluation/qrels.txt", "".join(f"{i} 0 {doc} 1\n" for i, doc in sorted(pairs)))
    bid = f"cmass-{dataset}-20261020"
    definition = dict(id=bid, aliases=[], displayName=f"CMASS {dataset}", datasetId=bid,
                      piSearchPromptVariant="plain_minimal", defaultQuerySetId="final",
                      defaultQueryPath=str(output / "generation/questions.tsv"),
                      querySets={"final": str(output / "generation/questions.tsv")},
                      defaultQrelsPath=str(output / "evaluation/qrels.txt"),
                      defaultGroundTruthPath=str(output / "evaluation/ground_truth.jsonl"),
                      defaultIndexPath="climbmix-400b", managedPresets={}, setup={"steps": {}},
                      retrievalEvaluation=dict(runFileBackend="internal", runDirBackend="internal"),
                      judgeEvaluation=dict(supportedModes=["gold-answer"], defaultMode="gold-answer"))
    save("benchmark.json", json.dumps(definition, indent=2) + "\n")
    receipt = dict(dataset=dataset, revision=REVISION, questions=len(queries), qrels=len(qrels),
                   source_sha256=dict(queries=query_sha, qrels=qrel_sha, answer_sets=set_sha),
                   question_normalization="replace TAB/CR/LF runs with one space; trim ends",
                   files={str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in output.rglob("*") if p.is_file()})
    save("input_manifest.json", json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(dict(dataset=dataset, questions=len(queries), qrels=len(qrels), output=str(output))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=COUNTS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.dataset, args.output)
