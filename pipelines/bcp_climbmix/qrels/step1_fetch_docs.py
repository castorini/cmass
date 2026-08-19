#!/usr/bin/env python3
"""Step 1: cache the full text of every candidate document.

Judgments are made against full documents, never the excerpt column of the
workbook, so the text is pulled once and cached. Re-runs skip what is already
cached, which makes the whole pipeline resumable.

Documents come from the same retrieval CLI the rest of this pipeline uses:

    python3 cm.py doc <docid>   ->  {"docid": ..., "doc": "<full text>"}

Any command with that contract works; point --doc-cmd at it.

Usage:
  python3 step1_fetch_docs.py --all
  python3 step1_fetch_docs.py --qids 25,74,78
  python3 step1_fetch_docs.py --ids-file more_docs.json     # a JSON list of ids
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import config


def doc_path(cache: Path, doc_id: str) -> Path:
    return cache / f"{doc_id}.json"


def fetch_one(doc_cmd: str, cache: Path, doc_id: str) -> tuple[str, str]:
    target = doc_path(cache, doc_id)
    if target.exists():
        return doc_id, "cached"
    try:
        proc = subprocess.run(shlex.split(doc_cmd) + [doc_id],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            target.with_suffix(".error").write_text(proc.stderr[:2000])
            return doc_id, f"error: exit {proc.returncode}"
        payload = json.loads(proc.stdout)
    except Exception as exc:                      # recorded per doc, never aborts the sweep
        target.with_suffix(".error").write_text(str(exc))
        return doc_id, f"error: {exc}"
    text = payload.get("doc")
    target.write_text(json.dumps({"docid": doc_id, "found": text is not None, "doc": text}))
    return doc_id, "fetched" if text is not None else "not-found"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    config.add_work_dir(parser)
    parser.add_argument("--doc-cmd", default=config.DOC_CMD,
                        help="command printing one document as JSON (env QRELS_DOC_CMD)")
    parser.add_argument("--qids", help="comma-separated")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--ids-file", help="JSON list of extra document ids to cache")
    parser.add_argument("--workers", type=int, default=8,
                        help="one request in flight each; keep modest for a shared endpoint")
    args = parser.parse_args()

    p = config.paths(args)
    wanted: list[str] = []
    seen: set[str] = set()

    if args.all or args.qids:
        candidates = json.loads(p["candidates"].read_text())
        qids = list(candidates) if args.all else [q.strip() for q in args.qids.split(",")]
        for qid in qids:
            for row in candidates[qid]["candidates"]:
                if row["doc_id"] not in seen:
                    seen.add(row["doc_id"])
                    wanted.append(row["doc_id"])
    if args.ids_file:
        for d in json.loads(Path(args.ids_file).read_text()):
            if d not in seen:
                seen.add(d)
                wanted.append(d)
    if not wanted:
        parser.error("pass --qids, --all, or --ids-file")

    cache = p["doc_cache"]
    cache.mkdir(parents=True, exist_ok=True)
    todo = [d for d in wanted if not doc_path(cache, d).exists()]
    print(f"distinct docs={len(wanted)} cached={len(wanted) - len(todo)} to fetch={len(todo)}")

    tally: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, args.doc_cmd, cache, d): d for d in todo}
        for n, future in enumerate(as_completed(futures), 1):
            _, status = future.result()
            key = status.split(":")[0]
            tally[key] = tally.get(key, 0) + 1
            if n % 200 == 0 or n == len(todo):
                print(f"  {n}/{len(todo)}  {tally}")
    print("done:", tally or "nothing to do")


if __name__ == "__main__":
    main()
