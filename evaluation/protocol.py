#!/usr/bin/env python3
"""Render the archived CMASS prompts and validate the DSQA dual verdict.

Inputs and rendered requests contain benchmark content: store them outside this
public repository. This module makes no network or model calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

PROMPTS = Path(__file__).with_name("prompts")
DATASETS = ("bcp", "trqa", "hotpotqa", "dsqa")
JUDGE_SYSTEM = "You are an evaluation judge. Follow the supplied gold-answer grading prompt."


def prompt(name: str) -> str:
    # Repository text files have one final LF; the runtime strings do not.
    return (PROMPTS / name).read_text(encoding="utf-8").removesuffix("\n")


def render(template: str, **fields: str) -> str:
    # One pass: a placeholder-looking string inside input is never re-expanded.
    return re.sub(r"\{\{(\w+)\}\}", lambda m: fields[m[1]], template)


def generation_messages(dataset: str, condition: str, question: str) -> list[dict]:
    if dataset not in DATASETS or condition not in ("closedbook", "piika"):
        raise ValueError("Unknown dataset or generation condition")
    if condition == "closedbook":
        name = "closed_book_dsqa.txt" if dataset == "dsqa" else "closed_book.txt"
        return [{"role": "system", "content": prompt(name)},
                {"role": "user", "content": question}]
    name = "piika_dsqa.txt" if dataset == "dsqa" else "piika.txt"
    return [{"role": "user", "content": render(prompt(name), question=question)}]


def judge_request(dataset: str, question: str, response: str, gold) -> dict:
    if dataset not in DATASETS:
        raise ValueError("Unknown dataset")
    if dataset == "dsqa":
        if not isinstance(gold, list) or not gold:
            raise ValueError("DSQA requires canonical answer_set members with aliases")
        system = prompt("dsqa_judge.txt")
        data = json.dumps(dict(question=question, response=response, gold_members=gold), ensure_ascii=False)
    else:
        if not isinstance(gold, str):
            raise ValueError("Pass the exact serialized correct-answer field")
        system = JUDGE_SYSTEM
        data = render(prompt("gold_answer_judge.txt"), question=question,
                      response=response, correct_answer=gold)
    return {"model": "gpt-6.1-sol", "reasoning": {"effort": "high"},
            "instructions": system, "input": [{"role": "user", "content": data}],
            "tools": [], "tool_choice": "none", "parallel_tool_calls": False,
            "stream": True, "store": False}


def verify_dsqa_verdict(verdict: dict, gold_members: list) -> tuple[bool, bool]:
    """Check arithmetic; semantic matching remains the GPT judge's decision."""
    if not gold_members or set(verdict) != {"answers", "strict_correct", "relaxed_correct", "reasoning"}:
        raise ValueError("Invalid DSQA verdict schema or empty gold set")
    if not isinstance(verdict["answers"], list) or not isinstance(verdict["reasoning"], str):
        raise ValueError("Invalid answers or rationale")
    if any(type(verdict[k]) is not bool for k in ("strict_correct", "relaxed_correct")):
        raise ValueError("Verdicts must be Boolean")
    covered, wrong = set(), False
    for member in verdict["answers"]:
        if set(member) != {"text", "gold_indices"} or not isinstance(member["text"], str) or not member["text"].strip():
            raise ValueError("Invalid predicted member")
        indices = member["gold_indices"]
        if not isinstance(indices, list) or any(type(i) is not int or not 0 <= i < len(gold_members) for i in indices):
            raise ValueError("Invalid gold index")
        if len(indices) != len(set(indices)):
            raise ValueError("Duplicate gold indices")
        covered.update(indices)
        wrong |= not indices
    relaxed = bool(verdict["answers"]) and not wrong
    strict = relaxed and covered == set(range(len(gold_members)))
    if (verdict["strict_correct"], verdict["relaxed_correct"]) != (strict, relaxed):
        raise ValueError("Verdicts disagree with the supplied member mappings")
    return strict, relaxed


def configure_piika(directory: Path, dataset: str) -> None:
    """Apply the archived prompt variant to a dedicated Piika checkout only."""
    path = directory / "src/pi-search/agent_prompt.ts"
    source = path.read_text(encoding="utf-8")
    known = {
        "af25f8964d7beb159bb88c79601aedcd75118de9365835be7efc4298f99c5496",
        "5389001e74dff70e81b6b37c2d3f718b352db7cb5d00d5292d98dd0bdb81cfbd",
        "9889a1b4c9f737799223ac91c0d61e3efbe55f4a86364b6805da236f07c7e3af",
    }
    if hashlib.sha256(source.encode()).hexdigest() not in known:
        raise ValueError("Unrecognized Piika prompt source; refusing to change it")
    source = re.sub(r"\nSTRICT EVIDENCE POLICY:.*?`;", "`;", source, count=1)
    extra = "\n- " + prompt("dsqa_complete_set.txt")
    source = source.replace(extra, "")
    if dataset == "dsqa":
        line = "- Keep Exact Answer directly responsive to the question."
        source = source.replace(line, line + extra)
    expected = ("9889a1b4c9f737799223ac91c0d61e3efbe55f4a86364b6805da236f07c7e3af"
                if dataset == "dsqa" else "5389001e74dff70e81b6b37c2d3f718b352db7cb5d00d5292d98dd0bdb81cfbd")
    if hashlib.sha256(source.encode()).hexdigest() != expected:
        raise ValueError("Prompt source hash mismatch")
    if path.read_text(encoding="utf-8") != source:
        path.write_text(source, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=DATASETS, required=True)
    sub = parser.add_subparsers(dest="operation", required=True)
    generation = sub.add_parser("generation")
    generation.add_argument("--condition", choices=("closedbook", "piika"), required=True)
    generation.add_argument("--input", type=Path, required=True, help="Private JSON object with question")
    judge = sub.add_parser("judge")
    judge.add_argument("--input", type=Path, required=True, help="Private JSON with question, response, gold")
    configure = sub.add_parser("configure-piika")
    configure.add_argument("--checkout", type=Path, required=True)
    args = parser.parse_args()
    if args.operation == "configure-piika":
        configure_piika(args.checkout, args.dataset)
        return
    item = json.loads(args.input.read_text(encoding="utf-8"))
    result = (generation_messages(args.dataset, args.condition, item["question"])
              if args.operation == "generation" else
              judge_request(args.dataset, item["question"], item["response"], item["gold"]))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
