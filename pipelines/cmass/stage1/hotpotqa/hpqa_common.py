"""Shared helpers for the HotpotQA Stage 1 scripts: run paths, tasks and the per-question files.

Files under <run>/stage1/<set>/ (written by the scripts named; one JSON per question unless noted):

  pool.jsonl               select_questions.py pool        the ranked path's pool (NanoKnow-unsupported bridge questions)
  rank/<qid>.json          rank_screen.py screen           per-question ranking screen (weakest supporting fact)
  ranked.jsonl             rank_screen.py order            the pool in ranking order
  selected.jsonl           select_questions.py nanoknow, batch   every question taken into Stage 1, with its origin
  collect/<qid>.json       collect_docs.py                 retrieved candidate documents (ranked path)
  input/<qid>.json         select_questions.py nanoknow, collect_docs.py   contamination-screen input
  contamination/<qid>.json contamination.js agent          leakage and single-document answerability verdict
  gate_input/<qid>.json    select_questions.py stage-gates gate input: the screen input plus the screen's two counts
  extreme/<qid>.json, uniqueness/<qid>.json, coherence/<qid>.json, annotator/<qid>.json   gates.js agents
  <step>/<qid>.searchlog.jsonl  tools/cm.py --stage 1 --step <step>   every search that step's agent made for the question
  build_report.json        build_inputs.py                 hop outcomes and snippet verification

The source benchmark's own data never lives in this repository: Stage 0 reads it at run time.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPE = HERE.parent.parent                    # pipelines/cmass
TOOLS = PIPE / "tools"
sys.path.insert(0, str(PIPE))
from cmass.run import Run, read_jsonl, write_json, write_jsonl  # noqa: E402

__all__ = ["Run", "read_jsonl", "write_json", "write_jsonl", "DEFAULT_SET", "GATES", "STEPS", "norm", "natural_key",
           "load_json", "run_of", "SerialCorpus", "tasks_by_id", "file", "lst", "selected", "write_selected", "input_record",
           "guard_clean", "fragments", "verbatim_any"]

DEFAULT_SET = "hotpotqa"
GATES = ("extreme", "uniqueness", "coherence", "annotator")
STEPS = ("contamination",) + GATES

_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    """Whitespace collapsed, as every original script normalised document text and facts."""
    return _WS.sub(" ", s or "").strip()


def natural_key(s) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


def load_json(path: Path):
    """The parsed file, None when it does not exist, or {"_error": ...} when it is not valid JSON."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return {"_error": f"not valid JSON: {e}"}


class SerialCorpus:
    """The run's Corpus with its calls serialised within this process. The worker threads of
    rank_screen.py and collect_docs.py share one Corpus, whose document cache writes are not thread-safe
    (temp files are named by process id); the corpus service is paced to one request at a time anyway."""

    def __init__(self, corpus):
        self._c, self._lock = corpus, threading.Lock()

    def search(self, *a, **k):
        with self._lock:
            return self._c.search(*a, **k)

    def get_document(self, *a, **k):
        with self._lock:
            return self._c.get_document(*a, **k)


def run_of(path: str | None) -> Run:
    run = Run(path)
    run.log_stage = "stage1"
    return run


def tasks_by_id(run: Run, sid: str) -> dict[str, dict]:
    return {t["record_id"]: t for t in run.tasks(sid)}


def file(run: Run, sid: str, step: str, qid: str) -> Path:
    return run.stage1_file(sid, step, qid)


def lst(run: Run, sid: str, name: str) -> Path:
    return run.stage1_dir(sid) / f"{name}.jsonl"


def selected(run: Run, sid: str) -> dict[str, dict]:
    """record_id -> {record_id, origin, batch, rank, hotpot_type}, in selection order."""
    return {r["record_id"]: r for r in read_jsonl(lst(run, sid, "selected"))}


def write_selected(run: Run, sid: str, rows: dict[str, dict]) -> None:
    write_jsonl(lst(run, sid, "selected"), rows.values())


def input_record(qid: str, question: str, gold_answers: list, doc_ids: list) -> dict:
    """The contamination-screen input, the same four fields the original agents read."""
    return {"qid": qid, "question": question, "gold_answers": list(gold_answers), "doc_ids": list(doc_ids)}


# ---------------------------------------------------------------------------------------------- the snippet guard
# The original build_final.py's verbatim test, unchanged: typographic quotes, dashes and non-breaking spaces
# are folded and whitespace collapsed on both sides; a snippet is split at elisions ("..", "…") and at
# inline document-id markers; every fragment of 8 or more characters must occur in one of the documents.
_ELISION = re.compile(r"\s*(?:\.{2,}|…|\[shard_\d+_\d+\])\s*")


def guard_clean(s: str) -> str:
    s = (s or "").replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("\xa0", " ")
    return _WS.sub(" ", s).strip()


def fragments(snippet: str) -> list[str]:
    return [f for f in (x.strip() for x in _ELISION.split(guard_clean(snippet))) if len(f) >= 8]


def verbatim_any(snippet: str, texts: list[str]) -> bool:
    """True when every fragment of the snippet occurs in one of the (raw) document texts."""
    fs = fragments(snippet)
    texts = [guard_clean(t) for t in texts if t]
    if not fs or not texts:
        return False
    return all(any(f in t for t in texts) for f in fs)
