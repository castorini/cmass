#!/usr/bin/env python3
"""Stage 0 task construction for HotpotQA: one task per question of the HotpotQA dev set (distractor
configuration, 7,405 questions), joined with NanoKnow's partition of those questions into
ClimbMix-supported and unsupported, and NanoKnow's qrels for the supported ones.

  stage0_tasks.py [--run RUN] [--set hotpotqa] --nanoknow DIR [--hotpot FILE]
  stage0_tasks.py ... --download-nanoknow DIR        # fetch NanoKnow's HotpotQA files from Hugging Face first

Sources (read at run time, never vendored):
  HotpotQA   the Hugging Face dataset hotpotqa/hotpot_qa, config "distractor", split "validation"
             (--hotpot FILE reads a local copy instead: a .parquet export, or a .json/.jsonl list of rows
             in the same schema). Use the distractor configuration: its context holds the gold paragraphs
             by construction, while fullwiki's holds retrieved ones, from which many questions reconstruct
             none of their supporting facts.
  NanoKnow   castorini/NanoKnow, questions-and-qrels/hotpotqa/: the supported and unsupported topic files
             (qid TAB question; together they partition all 7,405 questions), the qrels of the supported
             ones (qid Q0 docid rel), and the answers (one JSON object per line, {"qid", "answer": [...]}).
             A NanoKnow qid is the row index of the question in the HotpotQA dev set.

Every NanoKnow qid is checked against the HotpotQA row it names (same question text, same answer); a
mismatch stops the script. NanoKnow's qrels carry some document ids whose row part is zero-padded
(shard_SSSSS_0RRRR where the corpus serves shard_SSSSS_RRRR); they are repaired here and counted.

Output: <run>/tasks/<set>.jsonl, one task per question, record_id = the NanoKnow qid (row index):
  {record_id, question, answer, answer_type: "single", gold_answers, hints, source, hotpot_type, level,
   supporting_facts: [{title, fact}], nanoknow: "supported" | "unsupported", nanoknow_doc_ids}
`hints` are the question's gold supporting paragraphs (leads only, never evidence); `supporting_facts`
are the official supporting-fact sentences, which the ranked path uses as retrieval queries.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hpqa_common import DEFAULT_SET, norm, run_of, write_jsonl  # noqa: E402

HF_DATASET, HF_CONFIG, HF_SPLIT = "hotpotqa/hotpot_qa", "distractor", "validation"
NANOKNOW_REPO = "castorini/NanoKnow"
NANOKNOW_FILES = {     # role -> path inside the NanoKnow dataset repository
    "supported_topics": "questions-and-qrels/hotpotqa/climbmix/topics.nanoknow-hotpotqa-climbmix.supported.tsv",
    "unsupported_topics": "questions-and-qrels/hotpotqa/climbmix/topics.nanoknow-hotpotqa-climbmix.unsupported.tsv",
    "qrels_file": "questions-and-qrels/hotpotqa/climbmix/qrels.nanoknow-hotpotqa-climbmix.supported.txt",
    "answer_file": "questions-and-qrels/hotpotqa/answers.nanoknow-hotpotqa.jsonl",
}
PADDED = re.compile(r"^(shard_\d+)_0+(\d+)$")


def repair_doc_id(d: str) -> str:
    """NanoKnow's zero-padding bug: the row part of the id is padded, the corpus serves it unpadded."""
    m = PADDED.match(d)
    return f"{m.group(1)}_{m.group(2)}" if m else d


# ---------------------------------------------------------------------------------------------- sources
def load_hotpot(path: str | None) -> list[dict]:
    if path:
        p = Path(path)
        if p.suffix == ".parquet":
            import pyarrow.parquet as pq
            return pq.read_table(p).to_pylist()
        if p.suffix == ".jsonl":
            return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        return json.loads(p.read_text())
    from datasets import load_dataset
    return [dict(r) for r in load_dataset(HF_DATASET, HF_CONFIG, split=HF_SPLIT)]


def download_nanoknow(dest: Path) -> None:
    """Fetch the four NanoKnow files (HF_TOKEN from the environment when the repository needs one)."""
    import requests
    dest.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {os.environ['HF_TOKEN']}"} if os.environ.get("HF_TOKEN") else {}
    for path in NANOKNOW_FILES.values():
        url = f"https://huggingface.co/datasets/{NANOKNOW_REPO}/resolve/main/{path}"
        r = requests.get(url, headers=headers, timeout=120)
        if r.status_code != 200:
            sys.exit(f"download failed ({r.status_code}): {url}")
        (dest / Path(path).name).write_bytes(r.content)
        print(f"fetched {Path(path).name}")


def load_nanoknow(d: Path) -> tuple[dict, dict, dict, dict]:
    def topics(name):
        out = {}
        for line in (d / Path(NANOKNOW_FILES[name]).name).read_text().splitlines():
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                out[parts[0]] = parts[1]
        return out
    qrels: dict[str, list[str]] = {}
    for line in (d / Path(NANOKNOW_FILES["qrels_file"]).name).read_text().splitlines():
        p = line.split()
        if len(p) == 4:
            qrels.setdefault(p[0], []).append(p[2])
    answers = {}
    for line in (d / Path(NANOKNOW_FILES["answer_file"]).name).read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            answers[str(r["qid"])] = r["answer"] if isinstance(r["answer"], list) else [r["answer"]]
    return topics("supported_topics"), topics("unsupported_topics"), qrels, answers


# ---------------------------------------------------------------------------------------------- tasks
def supporting_facts(row: dict) -> list[dict]:
    """The official supporting-fact sentences, reconstructed from the gold paragraphs exactly as the
    original screen did (jev/screen.py facts_for): one {title, fact} per distinct (title, sentence)."""
    t2s = {t: list(s) for t, s in zip(row["context"]["title"], row["context"]["sentences"])}
    out, seen = [], set()
    for t, i in zip(row["supporting_facts"]["title"], row["supporting_facts"]["sent_id"]):
        s = t2s.get(t, [])
        if int(i) < len(s):
            f = norm(s[int(i)])
            if f and (t, f) not in seen:
                seen.add((t, f))
                out.append({"title": t, "fact": f})
    return out


def hints(row: dict) -> list[str]:
    """The gold supporting paragraphs, one string per supporting title."""
    t2s = {t: list(s) for t, s in zip(row["context"]["title"], row["context"]["sentences"])}
    out = []
    for t in dict.fromkeys(row["supporting_facts"]["title"]):
        if t in t2s:
            out.append(f"{t}: {norm(' '.join(t2s[t]))}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="run directory (default: $CMASS_RUN or the current directory)")
    ap.add_argument("--set", default=DEFAULT_SET)
    ap.add_argument("--nanoknow", required=True, help="directory holding NanoKnow's four HotpotQA files")
    ap.add_argument("--download-nanoknow", action="store_true", help="fetch them from Hugging Face into --nanoknow first")
    ap.add_argument("--hotpot", help="local copy of the HotpotQA dev set (distractor): .parquet, .json or .jsonl")
    a = ap.parse_args()

    run = run_of(a.run)
    nk_dir = Path(a.nanoknow)
    if a.download_nanoknow:
        download_nanoknow(nk_dir)
    supported, unsupported, qrels, answers = load_nanoknow(nk_dir)
    rows = load_hotpot(a.hotpot)

    # NanoKnow qid = row index; check every one against its row
    problems = []
    if set(supported) & set(unsupported):
        problems.append(f"{len(set(supported) & set(unsupported))} qids are both supported and unsupported")
    allq = {**supported, **unsupported}
    if sorted(allq, key=int) != [str(i) for i in range(len(rows))]:
        problems.append(f"the topic files hold {len(allq)} qids; the HotpotQA split has {len(rows)} rows")
    for q, text in allq.items():
        i = int(q)
        if i >= len(rows):
            continue
        if rows[i]["question"] != text:
            problems.append(f"qid {q}: question text differs from HotpotQA row {i}")
        if q in answers and rows[i]["answer"] not in answers[q]:
            problems.append(f"qid {q}: NanoKnow answer differs from HotpotQA row {i}")
    if set(qrels) - set(supported):
        problems.append(f"{len(set(qrels) - set(supported))} qrel qids are not in the supported topics")
    if problems:
        sys.exit("NanoKnow and HotpotQA do not line up:\n  " + "\n  ".join(problems[:20]))

    tasks, repaired, no_facts = [], 0, 0
    for i, row in enumerate(rows):
        q = str(i)
        doc_ids = []
        for d in qrels.get(q, []):
            r = repair_doc_id(d)
            repaired += r != d
            doc_ids.append(r)
        facts = supporting_facts(row)
        no_facts += not facts
        tasks.append({
            "record_id": q,
            "question": row["question"],
            "answer": row["answer"],
            "answer_type": "single",
            "gold_answers": answers.get(q, [row["answer"]]),
            "hints": hints(row),
            "source": {"benchmark": "HotpotQA", "dataset": HF_DATASET, "config": HF_CONFIG, "split": HF_SPLIT,
                       "id": row["id"], "row": i},
            "hotpot_type": row["type"],
            "level": row.get("level"),
            "supporting_facts": facts,
            "nanoknow": "supported" if q in supported else "unsupported",
            "nanoknow_doc_ids": doc_ids,
        })
    write_jsonl(run.task_file(a.set), tasks)
    c = Counter((t["nanoknow"], t["hotpot_type"]) for t in tasks)
    print(f"tasks: {len(tasks)} -> {run.task_file(a.set)}")
    print(f"  NanoKnow supported {len(supported)} (bridge {c[('supported', 'bridge')]}, comparison {c[('supported', 'comparison')]})"
          f" | unsupported {len(unsupported)} (bridge {c[('unsupported', 'bridge')]}, comparison {c[('unsupported', 'comparison')]})")
    print(f"  qrel pairs {sum(len(v) for v in qrels.values())} | zero-padded doc ids repaired {repaired}"
          f" | questions without supporting facts {no_facts}")


if __name__ == "__main__":
    main()
