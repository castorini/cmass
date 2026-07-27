#!/usr/bin/env python3
"""Qrel expansion and verification for BCP -> ClimbMix.

This runner intentionally uses the local Codex CLI for the two LLM steps:
query-family generation and document-hop verification. With a ChatGPT login,
that means OAuth/subscription auth rather than an OpenAI API key.

The model and reasoning effort are configurable. The defaults match the
published BCP qrel run unless overridden by ``CMASS_CODEX_MODEL`` and
``CMASS_CODEX_REASONING_EFFORT``.

Everything else is deterministic and resumable through one JSON file per
question under ``work/bcp/qrel_runs``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parents[1]
DEFAULT_INPUT_DIR = REPO_ROOT / "work" / "bcp" / "qrel_inputs"
DEFAULT_RUNS_DIR = REPO_ROOT / "work" / "bcp" / "qrel_runs"
DEFAULT_CM = PIPELINE_DIR / "cm.py"

QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["queries", "notes"],
    "properties": {
        "queries": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {"type": "string"},
        },
        "notes": {"type": "string"},
    },
}

QUERY_FAMILIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["hops"],
    "properties": {
        "hops": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["hop_id", "queries", "notes"],
                "properties": {
                    "hop_id": {"type": "integer"},
                    "queries": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 12,
                        "items": {"type": "string"},
                    },
                    "notes": {"type": "string"},
                },
            },
        }
    },
}

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["keep", "support_level", "evidence_snippet", "rationale"],
    "properties": {
        "keep": {"type": "boolean"},
        "support_level": {
            "type": "string",
            "enum": ["direct", "inferential", "partial", "none", "contradicts"],
        },
        "evidence_snippet": {"type": "string"},
        "rationale": {"type": "string"},
    },
}

JUDGE_BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["judgments"],
    "properties": {
        "judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["docid", "keep", "support_level", "evidence_snippet", "rationale"],
                "properties": {
                    "docid": {"type": "string"},
                    "keep": {"type": "boolean"},
                    "support_level": {
                        "type": "string",
                        "enum": ["direct", "inferential", "partial", "none", "contradicts"],
                    },
                    "evidence_snippet": {"type": "string"},
                    "rationale": {"type": "string"},
                },
            },
        }
    },
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def select_input_paths(input_dir: Path, qids: list[str], limit: int | None) -> list[Path]:
    if qids:
        paths = [input_dir / f"{qid}.json" for qid in qids]
    else:
        paths = sorted(input_dir.glob("*.json"), key=lambda p: sort_key(p.stem))
    if limit is not None:
        paths = paths[:limit]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing Stage-3 input(s): " + ", ".join(missing))
    return paths


def parse_json_object(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def is_retryable_codex_error(message: str) -> bool:
    lower = message.lower()
    return any(
        marker in lower
        for marker in (
            "429",
            "too many requests",
            "rate limit",
            "exceeded retry limit",
            "reconnecting",
            "502",
            "503",
            "504",
            "timed out",
            "timeout",
            "temporarily",
            "connection reset",
            "connection refused",
            "service unavailable",
            "overloaded",
        )
    )


def codex_exec_json(
    prompt: str,
    schema: dict[str, Any],
    *,
    codex_bin: str,
    model: str,
    reasoning_effort: str,
    timeout: int,
    retries: int,
    retry_delay: float,
) -> dict:
    last_error = "codex exec was not attempted"
    attempt = 0
    while retries < 0 or attempt <= retries:
        with tempfile.TemporaryDirectory(prefix="stage3-codex-") as tmp:
            tmp_path = Path(tmp)
            schema_path = tmp_path / "schema.json"
            output_path = tmp_path / "last_message.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")

            cmd = [
                codex_bin,
                "--ask-for-approval",
                "never",
                "exec",
                "--ephemeral",
                "-C",
                str(REPO_ROOT),
                "--model",
                model,
                "-c",
                f'model_reasoning_effort="{reasoning_effort}"',
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "--output-schema",
                str(schema_path),
                "-o",
                str(output_path),
                "-",
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    input=prompt,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                retryable = True
                last_error = f"codex exec timed out after {timeout}s"
            else:
                combined = f"{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}"
                retryable = is_retryable_codex_error(combined)
                if proc.returncode != 0:
                    last_error = (
                        "codex exec failed\n"
                        f"command: {' '.join(cmd)}\n"
                        f"stdout:\n{proc.stdout[-4000:]}\n"
                        f"stderr:\n{proc.stderr[-4000:]}"
                    )
                elif not output_path.exists():
                    last_error = f"codex exec did not write {output_path}; stdout:\n{proc.stdout[-4000:]}"
                else:
                    return parse_json_object(output_path.read_text(encoding="utf-8").strip())

            if retries < 0 or attempt < retries:
                if retryable:
                    sleep_for = min(600.0, retry_delay * (2**attempt) + random.uniform(0.0, retry_delay))
                    print(f"Codex exec retry {attempt + 1} after retryable failure: {last_error}", file=sys.stderr, flush=True)
                    time.sleep(sleep_for)
                    attempt += 1
                    continue
                break

            attempt += 1

    attempt_text = "infinite" if retries < 0 else str(retries + 1)
    raise RuntimeError(
        f"codex exec failed after {attempt_text} attempt(s)\n"
        f"{last_error}"
            )


def cm_json_call(command: list[str], timeout: int, retries: int, delay: float) -> dict:
    last_error = None
    attempt = 0
    while retries < 0 or attempt <= retries:
        if delay > 0:
            time.sleep(delay + random.uniform(0, min(0.5, delay)))
        try:
            proc = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            proc = None
            last_error = {"error": f"subprocess timed out after {timeout}s"}
        if proc is None:
            pass
        elif proc.returncode != 0:
            last_error = {"error": f"process exited {proc.returncode}", "stderr": proc.stderr}
        else:
            data = json.loads(proc.stdout)
            if "error" not in data:
                return data
            last_error = data
            message = str(data.get("error", "")) + " " + str(data.get("body", ""))
            retryable = any(
                marker in message.lower()
                for marker in (
                    "429",
                    "502",
                    "503",
                    "504",
                    "overloaded",
                    "timed out",
                    "timeout",
                    "temporarily",
                    "connection reset",
                    "connection refused",
                    "urlopen error",
                    "nodename",
                    "servname",
                    "name or service",
                    "temporary failure",
                )
            )
            if not retryable:
                break

        if retries < 0 or attempt < retries:
            print(
                f"Pyserini REST retry {attempt + 1}"
                + (" after 429/overload" if last_error else ""),
                file=sys.stderr,
                flush=True,
            )
            time.sleep(min(60.0, (2 ** attempt) + random.uniform(0.0, 1.0)))
        attempt += 1

    attempt_text = "infinite" if retries < 0 else str(retries + 1)
    raise RuntimeError(f"cm.py call failed after {attempt_text} attempt(s): {last_error}")


def cm_search(
    cm_path: Path,
    query: str,
    hits: int,
    preview_chars: int,
    timeout: int,
    retries: int,
    delay: float,
) -> dict:
    try:
        return cm_json_call(
            [sys.executable, str(cm_path), "search", query, str(hits), str(preview_chars)],
            timeout,
            retries,
            delay,
        )
    except RuntimeError as exc:
        raise RuntimeError(f"cm.py search error for query={query!r}: {exc}") from exc


def cm_doc(cm_path: Path, docid: str, timeout: int, retries: int, delay: float) -> str:
    data = cm_json_call(
        [sys.executable, str(cm_path), "doc", docid],
        timeout,
        retries,
        delay,
    )
    return str(data.get("doc", ""))


def query_prompt(record: dict, hop: dict, max_queries: int) -> str:
    return "\n".join(
        [
            "Generate a high-recall BM25 query family for qrel expansion.",
            "",
            "Goal: retrieve every ClimbMix document that supports the given projected hop.",
            "Use aliases, paraphrases, entity names, dates, titles, and the key fact phrased in different ways.",
            "Queries should be concise natural-language / keyword BM25 queries, not instructions.",
            "Do not invent facts beyond the question, answer, and hop clue.",
            "",
            f"Return at most {max_queries} queries. The first query should be the hop clue verbatim or a very close form.",
            "",
            f"record_id: {record['record_id']}",
            f"question: {record['question']}",
            f"answer: {record['answer']}",
            f"hop_id: {hop['hop_id']}",
            f"hop_clue: {hop['clue']}",
            f"hop_redundant: {hop.get('redundant', False)}",
            f"stage2_seed_doc_ids: {', '.join(hop.get('seed_doc_ids', []))}",
        ]
    )


def query_families_prompt(record: dict, max_queries: int) -> str:
    hops = [
        {
            "hop_id": hop["hop_id"],
            "clue": hop["clue"],
            "redundant": hop.get("redundant", False),
            "stage2_seed_doc_ids": hop.get("seed_doc_ids", []),
        }
        for hop in record["hops"]
    ]
    return "\n".join(
        [
            "Generate high-recall BM25 query families for qrel expansion.",
            "",
            "Goal: retrieve every ClimbMix document that supports each projected hop.",
            "Use aliases, paraphrases, entity names, dates, titles, and the key fact phrased in different ways.",
            "Queries should be concise natural-language / keyword BM25 queries, not instructions.",
            "Do not invent facts beyond the question, answer, and hop clues.",
            "",
            f"Return at most {max_queries} queries for each hop.",
            "For each hop, the first query should be the hop clue verbatim or a very close form.",
            "",
            f"record_id: {record['record_id']}",
            f"question: {record['question']}",
            f"answer: {record['answer']}",
            "",
            "necessary_hops_json:",
            json.dumps(hops, ensure_ascii=False, indent=2),
        ]
    )


def dry_query_family(record: dict, hop: dict, max_queries: int) -> dict:
    return {
        "queries": dedupe(
            [
                hop["clue"],
                f"{record['answer']} {hop['clue']}",
                record["question"],
            ]
        )[:max_queries],
        "notes": "dry-run query family",
    }


def get_query_families(record: dict, args: argparse.Namespace) -> dict[int, dict]:
    if args.dry_run:
        return {int(hop["hop_id"]): dry_query_family(record, hop, args.max_queries) for hop in record["hops"]}

    query_obj = codex_exec_json(
        query_families_prompt(record, args.max_queries),
        QUERY_FAMILIES_SCHEMA,
        codex_bin=args.codex_bin,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        timeout=args.codex_timeout,
        retries=args.codex_retries,
        retry_delay=args.codex_retry_delay,
    )

    by_hop: dict[int, dict] = {}
    for item in query_obj.get("hops", []):
        try:
            hop_id = int(item["hop_id"])
        except (KeyError, TypeError, ValueError):
            continue
        by_hop[hop_id] = {
            "queries": [str(query) for query in item.get("queries", [])],
            "notes": item.get("notes", ""),
        }

    # Fill any missing hop with a focused single-hop call rather than silently
    # running under-recalled retrieval.
    for hop in record["hops"]:
        hop_id = int(hop["hop_id"])
        if hop_id not in by_hop:
            by_hop[hop_id] = codex_exec_json(
                query_prompt(record, hop, args.max_queries),
                QUERY_SCHEMA,
                codex_bin=args.codex_bin,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                timeout=args.codex_timeout,
                retries=args.codex_retries,
                retry_delay=args.codex_retry_delay,
            )
    return by_hop


def fuse_searches(hop: dict, searches: list[dict], rrf_k: float) -> list[dict]:
    by_doc: dict[str, dict] = {}
    seed_doc_ids = set(hop.get("seed_doc_ids", []))

    for query_index, search in enumerate(searches):
        query = search["query"]
        for result in search.get("results", []):
            docid = result.get("docid")
            if not docid:
                continue
            rank = int(result.get("rank") or 10**9)
            row = by_doc.setdefault(
                docid,
                {
                    "docid": docid,
                    "rrf": 0.0,
                    "is_seed": docid in seed_doc_ids,
                    "sources": [],
                },
            )
            row["rrf"] += 1.0 / (rrf_k + rank)
            row["sources"].append(
                {
                    "query_index": query_index,
                    "query": query,
                    "rank": rank,
                    "bm25_score": result.get("score"),
                    "preview": result.get("preview", ""),
                }
            )

    for docid in seed_doc_ids:
        by_doc.setdefault(
            docid,
            {
                "docid": docid,
                "rrf": 0.0,
                "is_seed": True,
                "sources": [],
            },
        )

    fused = list(by_doc.values())
    fused.sort(key=lambda row: (-row["rrf"], not row["is_seed"], row["docid"]))
    for rank, row in enumerate(fused, start=1):
        row["fused_rank"] = rank
        row["rrf"] = round(row["rrf"], 8)
    return fused


def selected_candidates(fused: list[dict], top_per_hop: int, min_rrf: float) -> list[dict]:
    selected = []
    non_seed_seen = 0
    for row in fused:
        if row["is_seed"]:
            selected.append(row)
            continue
        non_seed_seen += 1
        if non_seed_seen <= top_per_hop or (min_rrf > 0 and row["rrf"] >= min_rrf):
            selected.append(row)
    return selected


def negative_candidates(fused: list[dict], selected_docids: set[str], limit: int) -> list[dict]:
    out = []
    for row in fused:
        if row["docid"] not in selected_docids:
            out.append(row)
        if len(out) >= limit:
            break
    return out


def expand_record(record: dict, args: argparse.Namespace, existing: dict | None) -> dict:
    if existing and existing.get("expanded_at") and not args.force:
        return existing

    run = {
        "record_id": record["record_id"],
        "question": record["question"],
        "answer": record["answer"],
        "n_hops": len(record["hops"]),
        "n_necessary_hops": record["n_necessary_hops"],
        "model_config": {
            "llm_transport": "codex-cli-chatgpt-oauth",
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
        },
        "retrieval_config": {
            "cm_path": str(args.cm),
            "depth": args.depth,
            "preview_chars": args.preview_chars,
            "rrf_k": args.rrf_k,
            "top_per_hop": args.top_per_hop,
            "min_rrf": args.min_rrf,
            "hard_negative_top": args.hard_negative_top,
        },
        "hops": [],
    }

    candidate_order: list[str] = []
    candidate_hop_links: dict[str, set[int]] = defaultdict(set)
    seed_doc_ids: set[str] = set()
    query_families = get_query_families(record, args)

    for hop in record["hops"]:
        query_obj = query_families[int(hop["hop_id"])]
        queries = dedupe([hop["clue"], *query_obj.get("queries", [])])[: args.max_queries]
        searches = [
            cm_search(
                args.cm,
                query,
                args.depth,
                args.preview_chars,
                args.cm_timeout,
                args.cm_retries,
                args.cm_delay,
            )
            for query in queries
        ]
        fused = fuse_searches(hop, searches, args.rrf_k)
        selected = selected_candidates(fused, args.top_per_hop, args.min_rrf)
        selected_docids = {row["docid"] for row in selected}
        hard_negs = negative_candidates(fused, selected_docids, args.hard_negative_top)

        for docid in hop.get("seed_doc_ids", []):
            seed_doc_ids.add(docid)
        for row in selected:
            docid = row["docid"]
            if docid not in candidate_order:
                candidate_order.append(docid)
            candidate_hop_links[docid].add(int(hop["hop_id"]))

        run["hops"].append(
            {
                "hop_id": hop["hop_id"],
                "clue": hop["clue"],
                "redundant": hop.get("redundant", False),
                "seed_doc_ids": hop.get("seed_doc_ids", []),
                "query_family": queries,
                "query_generation_notes": query_obj.get("notes", ""),
                "searches": searches,
                "fused_candidates": fused,
                "selected_candidates": selected,
                "hard_negative_candidates": hard_negs,
            }
        )

    run["candidate_qrel"] = candidate_order
    run["candidate_hop_links"] = {docid: sorted(hops) for docid, hops in sorted(candidate_hop_links.items())}
    run["stage2_seed_doc_ids"] = sorted(seed_doc_ids)
    run["expanded_at"] = utc_now()
    run["status"] = "expanded"
    return run


def preview_text_from_sources(sources: list[dict]) -> str:
    previews = dedupe([str(source.get("preview", "")) for source in sources if source.get("preview")])
    return "\n\n".join(previews[:6])


def doc_excerpt(doc_text: str, sources: list[dict], max_doc_chars: int) -> str:
    if len(doc_text) <= max_doc_chars:
        return doc_text

    preview_block = preview_text_from_sources(sources)
    if preview_block:
        preview_block = "Retrieved match windows:\n" + preview_block + "\n\n"

    remaining = max(1000, max_doc_chars - len(preview_block) - 200)
    head_len = remaining // 2
    tail_len = remaining - head_len
    return (
        preview_block
        + "Document head:\n"
        + doc_text[:head_len]
        + "\n\n[...document truncated...]\n\nDocument tail:\n"
        + doc_text[-tail_len:]
    )[:max_doc_chars]


def find_sources_for_doc(run: dict, docid: str) -> list[dict]:
    sources: list[dict] = []
    for hop in run.get("hops", []):
        for row in hop.get("fused_candidates", []):
            if row.get("docid") == docid:
                sources.extend(row.get("sources", []))
    return sources


def judge_prompt(record: dict, hop: dict, docid: str, excerpt: str) -> str:
    return "\n".join(
        [
            "You are verifying qrels for a retrieval benchmark.",
            "",
            "Question-level context is provided only to interpret entities and references.",
            "Judge the document against this ONE projected hop.",
            "",
            "KEEP if the document directly states or soundly establishes the hop fact.",
            "KEEP also if the hop is established by a short, unambiguous inference from the document text.",
            "DROP if the document is merely topical, only mentions one entity, supports a different hop,",
            "requires outside knowledge, contradicts the hop, or is only partially useful.",
            "",
            "Return JSON only. evidence_snippet should be a short excerpt or empty string.",
            "",
            f"record_id: {record['record_id']}",
            f"question: {record['question']}",
            f"answer: {record['answer']}",
            f"hop_id: {hop['hop_id']}",
            f"hop_clue: {hop['clue']}",
            f"hop_redundant: {hop.get('redundant', False)}",
            f"docid: {docid}",
            "",
            "DOCUMENT:",
            excerpt,
        ]
    )


def judge_batch_prompt(record: dict, hop: dict, docs: list[dict]) -> str:
    return "\n".join(
        [
            "You are verifying qrels for a retrieval benchmark.",
            "",
            "Question-level context is provided only to interpret entities and references.",
            "Judge every candidate document against this ONE projected hop.",
            "",
            "KEEP a document if it directly states or soundly establishes the hop fact.",
            "KEEP also if the hop is established by a short, unambiguous inference from the document text.",
            "DROP if the document is merely topical, only mentions one entity, supports a different hop,",
            "requires outside knowledge, contradicts the hop, or is only partially useful.",
            "",
            "Return one judgment for every candidate docid exactly once.",
            "evidence_snippet should be a short excerpt or empty string.",
            "",
            f"record_id: {record['record_id']}",
            f"question: {record['question']}",
            f"answer: {record['answer']}",
            f"hop_id: {hop['hop_id']}",
            f"hop_clue: {hop['clue']}",
            f"hop_redundant: {hop.get('redundant', False)}",
            "",
            "candidate_documents_json:",
            json.dumps(docs, ensure_ascii=False, indent=2),
        ]
    )


def chunks(items: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        raise ValueError("chunk size must be positive")
    return [items[i:i + size] for i in range(0, len(items), size)]


def hop_by_id(record: dict) -> dict[int, dict]:
    return {int(hop["hop_id"]): hop for hop in record["hops"]}


def existing_judgment_map(run: dict) -> dict[tuple[str, int], dict]:
    out: dict[tuple[str, int], dict] = {}
    for judgment in run.get("verification", []):
        docid = judgment.get("docid")
        hop_id = judgment.get("hop_id")
        if docid and hop_id is not None:
            out[(str(docid), int(hop_id))] = judgment
    return out


def verify_record(record: dict, run: dict, args: argparse.Namespace) -> dict:
    if run.get("verified_at") and not args.force:
        return run

    hops = hop_by_id(record)
    existing = {} if args.force else existing_judgment_map(run)
    verification: list[dict] = [] if args.force else list(run.get("verification", []))
    verified_docids: set[str] = set() if args.force else set(run.get("verified_qrel", []))
    doc_cache: dict[str, str] = {}

    def add_judgment(hop_id: int, docid: str, judged: dict, judge: str | dict) -> None:
        key = (docid, hop_id)
        if key in existing:
            return
        judgment = {
            "docid": docid,
            "hop_id": hop_id,
            "keep": bool(judged.get("keep")),
            "support_level": judged.get("support_level", "none"),
            "evidence_snippet": judged.get("evidence_snippet", ""),
            "rationale": judged.get("rationale", ""),
            "judge": judge,
        }
        verification.append(judgment)
        existing[key] = judgment
        if judgment["keep"]:
            verified_docids.add(docid)

    run_hops_by_id = {int(hop["hop_id"]): hop for hop in run.get("hops", [])}
    all_candidate_docids = list(run.get("candidate_qrel", []))

    for hop_id, hop in sorted(hops.items()):
        hop_seed_docids = set(hop.get("seed_doc_ids", []))
        if args.verify_against == "all":
            hop_docids = all_candidate_docids
        else:
            run_hop = run_hops_by_id.get(hop_id, {})
            hop_docids = [row["docid"] for row in run_hop.get("selected_candidates", []) if row.get("docid")]
        hop_docids = dedupe(hop_docids)
        pending_docids: list[str] = []

        for docid in hop_docids:
            key = (docid, hop_id)
            if key in existing and not args.force:
                if existing[key].get("keep"):
                    verified_docids.add(docid)
                continue

            if docid in hop_seed_docids and not args.verify_seeds:
                add_judgment(
                    hop_id,
                    docid,
                    {
                        "keep": True,
                        "support_level": "direct",
                        "evidence_snippet": "",
                        "rationale": "Trusted Stage-2 seed supporting doc.",
                    },
                    "stage2_seed",
                )
                continue

            pending_docids.append(docid)

        for batch_docids in chunks(pending_docids, args.verify_batch_size):
            docs = []
            for docid in batch_docids:
                sources = find_sources_for_doc(run, docid)
                if docid not in doc_cache:
                    doc_cache[docid] = cm_doc(args.cm, docid, args.cm_timeout, args.cm_retries, args.cm_delay)
                docs.append(
                    {
                        "docid": docid,
                        "excerpt": doc_excerpt(doc_cache[docid], sources, args.max_doc_chars),
                    }
                )

            if args.dry_run:
                judged_items = [
                    {
                        "docid": docid,
                        "keep": False,
                        "support_level": "none",
                        "evidence_snippet": "",
                        "rationale": "dry-run verification",
                    }
                    for docid in batch_docids
                ]
            else:
                batch_obj = codex_exec_json(
                    judge_batch_prompt(record, hop, docs),
                    JUDGE_BATCH_SCHEMA,
                    codex_bin=args.codex_bin,
                    model=args.model,
                    reasoning_effort=args.reasoning_effort,
                    timeout=args.codex_timeout,
                    retries=args.codex_retries,
                    retry_delay=args.codex_retry_delay,
                )
                judged_items = batch_obj.get("judgments", [])

            judged_by_docid = {str(item.get("docid")): item for item in judged_items if item.get("docid")}
            missing = [docid for docid in batch_docids if docid not in judged_by_docid]

            for docid in batch_docids:
                judged = judged_by_docid.get(docid)
                if judged is None:
                    continue
                add_judgment(
                    hop_id,
                    docid,
                    judged,
                    {
                        "transport": "codex-cli-chatgpt-oauth",
                        "model": args.model,
                        "reasoning_effort": args.reasoning_effort,
                        "batch_size": len(batch_docids),
                    },
                )

            for docid in missing:
                sources = find_sources_for_doc(run, docid)
                excerpt = doc_excerpt(doc_cache[docid], sources, args.max_doc_chars)
                if args.dry_run:
                    judged = {
                        "keep": False,
                        "support_level": "none",
                        "evidence_snippet": "",
                        "rationale": "dry-run verification fallback",
                    }
                else:
                    judged = codex_exec_json(
                        judge_prompt(record, hop, docid, excerpt),
                        JUDGE_SCHEMA,
                        codex_bin=args.codex_bin,
                        model=args.model,
                        reasoning_effort=args.reasoning_effort,
                        timeout=args.codex_timeout,
                        retries=args.codex_retries,
                        retry_delay=args.codex_retry_delay,
                    )
                add_judgment(
                    hop_id,
                    docid,
                    judged,
                    {
                        "transport": "codex-cli-chatgpt-oauth",
                        "model": args.model,
                        "reasoning_effort": args.reasoning_effort,
                        "fallback": "single_doc",
                    },
                )

    hop_qrels: dict[str, list[str]] = {}
    for hop_id in sorted(hops):
        hop_qrels[str(hop_id)] = sorted(
            {
                str(judgment["docid"])
                for judgment in verification
                if judgment.get("keep") and int(judgment.get("hop_id", -1)) == hop_id
            }
        )
    uncovered_hop_ids = [int(hop_id) for hop_id, docids in hop_qrels.items() if not docids]

    run["verification"] = verification
    run["verified_hop_qrels"] = hop_qrels
    run["uncovered_hop_ids"] = uncovered_hop_ids
    run["verified_qrel"] = sorted({docid for docids in hop_qrels.values() for docid in docids})
    run["verified_at"] = utc_now()
    run["status"] = "verified" if not uncovered_hop_ids else "incomplete"
    return run


def parse_qids(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["expand", "verify", "all"], default="all")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--cm", type=Path, default=DEFAULT_CM)
    parser.add_argument("--qids", default="", help="Comma-separated record_ids, e.g. 23,60,83")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--depth", type=int, default=120)
    parser.add_argument("--preview-chars", type=int, default=800)
    parser.add_argument("--max-queries", type=int, default=8)
    parser.add_argument("--rrf-k", type=float, default=60.0)
    parser.add_argument("--top-per-hop", type=int, default=80)
    parser.add_argument("--min-rrf", type=float, default=0.0)
    parser.add_argument("--hard-negative-top", type=int, default=40)
    parser.add_argument("--max-doc-chars", type=int, default=6000)
    parser.add_argument("--verify-against", choices=["selected", "all"], default="selected")
    parser.add_argument("--verify-batch-size", type=int, default=12)
    parser.add_argument("--verify-seeds", action="store_true")
    parser.add_argument("--model", default=os.environ.get("CMASS_CODEX_MODEL", "gpt-5.5"))
    parser.add_argument(
        "--reasoning-effort",
        default=os.environ.get("CMASS_CODEX_REASONING_EFFORT", "medium"),
        choices=["low", "medium", "high", "xhigh"],
    )
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    parser.add_argument("--codex-timeout", type=int, default=1800)
    parser.add_argument("--codex-retries", type=int, default=6, help="Use -1 to retry Codex 429/timeouts forever.")
    parser.add_argument("--codex-retry-delay", type=float, default=60.0, help="Base delay with jitter before retrying Codex calls.")
    parser.add_argument("--cm-timeout", type=int, default=120)
    parser.add_argument("--cm-retries", type=int, default=30, help="Use -1 to retry 429/overload forever.")
    parser.add_argument("--cm-delay", type=float, default=1.05, help="Delay with jitter before each Pyserini REST request.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="No Codex calls; useful for plumbing tests.")
    args = parser.parse_args()

    qids = parse_qids(args.qids)
    paths = select_input_paths(args.input_dir, qids, args.limit)
    args.runs_dir.mkdir(parents=True, exist_ok=True)

    summary = {"processed": 0, "expanded": 0, "verified": 0, "skipped": 0, "runs_dir": str(args.runs_dir)}

    for input_path in paths:
        record = load_json(input_path)
        run_path = args.runs_dir / f"{record['record_id']}.json"
        existing = load_json(run_path) if run_path.exists() else None

        if existing and existing.get("status") == "verified" and args.mode in {"verify", "all"} and not args.force:
            summary["skipped"] += 1
            continue

        run = existing if existing else None
        if args.mode in {"expand", "all"}:
            run = expand_record(record, args, existing)
            write_json(run_path, run)
            summary["expanded"] += 1
        elif run is None:
            raise FileNotFoundError(f"No expansion run exists for {record['record_id']}: {run_path}")

        if args.mode in {"verify", "all"}:
            run = verify_record(record, run, args)
            write_json(run_path, run)
            summary["verified"] += 1

        summary["processed"] += 1
        print(json.dumps({"record_id": record["record_id"], "status": run.get("status"), "run": str(run_path)}))

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
