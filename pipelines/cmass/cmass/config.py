"""Run configuration: the corpus backend and the candidate sets with their dataset rules.

The config is `cmass.json` in the run directory (or the file named by $CMASS_CONFIG). Example:

  {
    "corpus": {
      "name": "climbmix-400b",
      "backend": "cli",                          # "cli" or "rest"
      "cli": {"search": ["python3", "/path/to/cm.py", "search"],
              "doc":    ["python3", "/path/to/cm.py", "doc"]},
      "rest": {"base_url": "https://example.org", "index": "climbmix-400b", "token_env": "PYSERINI_API_TOKEN"},
      "min_gap_s": 2.0, "slots": 1, "retries": 20
    },
    "sets": {
      "set_a":    {"input": "inputs/set_a.jsonl", "family": "generic", "group": "released"},
      "nm_set_a": {"input": "inputs/set_a_near_misses.jsonl", "family": "generic", "group": "near_miss"}
    }
  }

Families carry the paper's dataset-specific rules (Table "Dataset-specific limits on question and answer
revisions"): which revisions are permitted, whether the answer may change, and whether the final check
runs. A set may override any family field. `answer_type` (single, set or aggregate) is per record when
the record says so, else the set's or the family's default.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

FAMILIES = {
    "hotpotqa": {
        "answer_type": "single",
        "revisions": ["correct_wording", "add_qualifier"],
        "answer_may_change": False,
        "final_check": False,
        "notes": "Correct malformed wording or add one grounded qualifier; preserve the answer. "
                 "Source supporting paragraphs are hints only, never grounding evidence.",
    },
    "bcp": {
        "answer_type": "single",
        "revisions": ["evidence_only"],
        "near_miss_revisions": ["correct_clue", "remove_clue", "adjust_range", "add_qualifier"],
        "answer_may_change": "typo_only",
        "final_check": True,
        "notes": "Evidence-only repairs for questions Stage 1 retained; near misses may receive a clue "
                 "correction or removal, a range adjustment, or one grounded qualifier. The answer is "
                 "preserved except for typo corrections.",
    },
    "trqa": {
        "answer_type": "aggregate",
        "revisions": ["add_qualifier", "change_attribute_if_trivial", "recompute_answer"],
        "answer_may_change": True,
        "final_check": True,
        "notes": "Add a grounded qualifier, or change the requested attribute when the wording makes the "
                 "answer trivial; recompute the answer only under the completeness standard.",
    },
    "dsqa": {
        "answer_type": "set",
        "revisions": ["remove_source_clause", "add_qualifier", "adjust_year_or_threshold", "recompute_answer"],
        "answer_may_change": True,
        "final_check": True,
        "notes": "Remove a source clause, add a grounded qualifier, or adjust a year or threshold; "
                 "recompute the answer only under the completeness standard.",
    },
    "generic": {
        "answer_type": "single",
        "revisions": ["evidence_only", "add_qualifier", "correct_wording"],
        "answer_may_change": False,
        "final_check": True,
        "notes": "Evidence-only repairs, one grounded qualifier, or a wording correction; preserve the answer.",
    },
}
GROUPS = ("released", "near_miss", "rewrite")

DEFAULT_CORPUS = {"name": "corpus", "backend": "cli", "min_gap_s": 2.0, "slots": 1, "retries": 20,
                  "lock_dir": None}


class Config:
    def __init__(self, data: dict, path: Path | None = None):
        self.data = data
        self.path = path
        self.corpus = {**DEFAULT_CORPUS, **(data.get("corpus") or {})}
        self.sets: dict[str, dict] = {}
        for sid, s in (data.get("sets") or {}).items():
            fam = s.get("family", "generic")
            if fam not in FAMILIES:
                raise SystemExit(f"set {sid}: unknown family {fam!r}; one of {sorted(FAMILIES)}")
            if s.get("group", "released") not in GROUPS:
                raise SystemExit(f"set {sid}: group must be one of {GROUPS}")
            merged = copy.deepcopy(FAMILIES[fam])
            merged.update({"family": fam, "group": "released"})
            merged.update(s)
            self.sets[sid] = merged

    def set(self, sid: str) -> dict:
        if sid not in self.sets:
            raise SystemExit(f"unknown set {sid!r}; configured: {sorted(self.sets)}")
        return self.sets[sid]

    def permitted_revisions(self, sid: str) -> list[str]:
        s = self.set(sid)
        if s["group"] == "near_miss" and s.get("near_miss_revisions"):
            return list(dict.fromkeys(s["revisions"] + s["near_miss_revisions"]))
        return list(s["revisions"])


def load(run_dir: Path, path: str | os.PathLike | None = None) -> Config:
    p = Path(path or os.environ.get("CMASS_CONFIG") or Path(run_dir) / "cmass.json")
    if not p.exists():
        raise SystemExit(f"config not found: {p} (create cmass.json in the run directory; see config.py)")
    return Config(json.loads(p.read_text()), p)
