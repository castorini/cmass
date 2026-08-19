#!/usr/bin/env python3
"""Paths and defaults for the qrels pipeline.

Nothing here is a constant edited in place: every path is an argument with an
environment-variable default, because the inputs (a reviewer workbook, a corpus
duplicate index) live outside the repository and differ per machine.

Environment variables, all optional:

  QRELS_WORK_DIR     where the pipeline writes            (default ./work)
  QRELS_WORKBOOK     reviewer workbook (.xlsm)            -- step 0
  QRELS_QUESTIONS    verified-questions JSONL             -- step 0
  QRELS_DOC_CMD      command that prints one document     -- step 1
  QRELS_NEAR_DUP_DIR parquet dir of near-duplicate edges  -- step 5
  QRELS_EXACT_DUPS   JSONL of exact-duplicate groups      -- step 5

The retrieval CLI is the same two-command contract the rest of this pipeline
uses (see ../README.md); only the `doc` half is needed here:

    python3 cm.py doc <docid>      -> {"docid": ..., "doc": "<full text>"}
"""

import argparse
import os
from pathlib import Path

WORK_DIR = Path(os.environ.get("QRELS_WORK_DIR", "work"))
WORKBOOK = os.environ.get("QRELS_WORKBOOK", "")
QUESTIONS = os.environ.get("QRELS_QUESTIONS", "")
DOC_CMD = os.environ.get("QRELS_DOC_CMD", "python3 cm.py doc")
NEAR_DUP_DIR = os.environ.get("QRELS_NEAR_DUP_DIR", "")
EXACT_DUPS = os.environ.get("QRELS_EXACT_DUPS", "")


def add_work_dir(parser: argparse.ArgumentParser) -> None:
    """--work-dir, plus the derived paths every step shares."""
    parser.add_argument("--work-dir", default=str(WORK_DIR),
                        help="pipeline working directory (env QRELS_WORK_DIR)")


def paths(args) -> dict:
    """Derived layout under --work-dir. Created lazily by whichever step writes."""
    work = Path(args.work_dir)
    return {
        "work": work,
        "candidates": work / "candidates.json",
        "doc_cache": work / "doccache",
        "rejections": work / "rejections",
        "verdicts": work / "verdicts",
        "expand": work / "expand",
        "qrels": work / "qrels.jsonl",
    }


def require(value: str, flag: str, env: str) -> str:
    """Fail with an actionable message instead of a stack trace three calls deep."""
    if not value:
        raise SystemExit(f"missing {flag}: pass it or set {env}")
    return value
