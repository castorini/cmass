#!/usr/bin/env python3
"""Build the review site's data files from audited records and their documents.

  python3 build_site_data.py CONFIG.json [--out DIR]

CONFIG.json (paths are relative to the config file):

  {"corpus": "climbmix-400b",
   "docs": "docs.jsonl",                     # JSONL of {"doc_id", "text"}, or a directory of <doc_id>.txt
   "datasets": [
     {"id": "set_a", "name": "Set A", "records": "set_a_audited.jsonl"},
     {"id": "nm_set_a", "name": "Set A", "title": "Set A near misses", "group": "near_miss",
      "records": "set_a_near_misses.jsonl"},
     {"id": "rw_set_a", "name": "Set A", "title": "Set A: salvageable with rewrites", "group": "rewrite",
      "records": "set_a_rewrites.jsonl", "canary": "<canary string, optional>"}]}

Writes, under --out (default: data/ next to this script):

  manifest.json      {"corpus", "datasets": [{"id", "name", "title", "nm"?, "rw"?}]}
  <id>.json          {"dataset", "records": [...], "canary"?}
  docs_<id>.json     {doc_id: text} for every document a record cites, uses as evidence or rejects

The record format is described in README.md. Documents longer than MAX_CHARS keep their head plus a
window around every evidence quote, so a few very long pages do not bloat the site.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_CHARS = 40000
WIN = 2500
GROUPS = {"released": {}, "near_miss": {"nm": True}, "rewrite": {"rw": True}}
# U+FFFD, private-use and invisible format characters: written as JSON escapes (the text is unchanged)
ODD = re.compile("[�-​-‏‪-‮⁠-⁤﻿­]")


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def locate(text: str, quote: str):
    """(start, end) of a quote in the text, exact or whitespace-normalized; None if absent."""
    i = text.find(quote)
    if i >= 0:
        return i, i + len(quote)
    words = norm_ws(quote).split(" ")
    if not words or not words[0]:
        return None
    m = re.search(r"\s+".join(re.escape(w) for w in words), text)
    return (m.start(), m.end()) if m else None


def hop_doc_ids(h: dict) -> set[str]:
    ids = set(h.get("supporting_doc_ids") or [])
    if h.get("doc_id"):
        ids.add(h["doc_id"])
    return ids


def quotes_for(rec: dict) -> dict[str, list[str]]:
    q: dict[str, list[str]] = {}
    hops = [h.get("audit", {}) for h in rec.get("hops", [])] + rec.get("audit", {}).get("added_hops", [])
    for h in hops:
        for ev in h.get("evidence", []):
            q.setdefault(ev["doc_id"], []).extend(ev.get("quotes", []))
    return q


def doc_ids_for(rec: dict) -> set[str]:
    ids = set()
    for h in rec.get("hops", []):
        ids.update(hop_doc_ids(h))
        a = h.get("audit", {})
        ids.update(e["doc_id"] for e in a.get("evidence", []))
        ids.update(r["doc_id"] for r in a.get("rejected_docs", []) if r.get("doc_id"))
    for h in rec.get("audit", {}).get("added_hops", []):
        ids.update(e["doc_id"] for e in h.get("evidence", []))
    return ids


def trim(text: str, quotes: list[str]) -> str:
    if len(text) <= MAX_CHARS:
        return text
    spans = [(0, 3000)]
    for qt in quotes:
        hit = locate(text, qt)
        if hit:
            spans.append((max(0, hit[0] - WIN), min(len(text), hit[1] + WIN)))
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    out = "\n[… excerpt; full document is {:,} characters …]\n".format(len(text)).join(text[s:e] for s, e in merged)
    return out + ("\n[… document continues …]" if merged[-1][1] < len(text) else "")


def load_docs(src: Path):
    if src.is_dir():
        return lambda d: (src / f"{d}.txt").read_text() if (src / f"{d}.txt").exists() else None
    table = {}
    with open(src) as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                table[r["doc_id"]] = r["text"]
    return table.get


def main():
    ap = argparse.ArgumentParser(description="Build the review site's data files.")
    ap.add_argument("config")
    ap.add_argument("--out", default=str(HERE / "data"))
    a = ap.parse_args()
    cfg_path = Path(a.config).resolve()
    cfg, base, out = json.loads(cfg_path.read_text()), cfg_path.parent, Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    get_doc = load_docs(base / cfg["docs"])
    dump = lambda o: ODD.sub(lambda m: "\\u%04x" % ord(m.group()), json.dumps(o, ensure_ascii=False))
    manifest = {"corpus": cfg.get("corpus", ""), "datasets": []}
    for ds in cfg["datasets"]:
        group = ds.get("group", "released")
        if group not in GROUPS:
            sys.exit(f"{ds['id']}: group must be one of {sorted(GROUPS)}")
        recs = [json.loads(l) for l in open(base / ds["records"]) if l.strip()]
        docs, quotes, invalid, missing = {}, {}, set(), 0
        for r in recs:
            for d, qs in quotes_for(r).items():
                quotes.setdefault(d, []).extend(qs)
            docs.update({d: None for d in doc_ids_for(r)})
            invalid.update(r.get("audit", {}).get("invalid_doc_ids") or [])
        for d in docs:
            t = None if d in invalid else get_doc(d)
            missing += t is None and d not in invalid
            docs[d] = trim(t, quotes.get(d, [])) if t is not None else None
        payload = {"dataset": ds["id"], "records": recs}
        if ds.get("canary"):
            payload["canary"] = ds["canary"]
        (out / f"{ds['id']}.json").write_text(dump(payload))
        (out / f"docs_{ds['id']}.json").write_text(dump(docs))
        manifest["datasets"].append({"id": ds["id"], "name": ds.get("name", ds["id"]),
                                     "title": ds.get("title", ds.get("name", ds["id"])), **GROUPS[group]})
        print(f"{ds['id']}: {len(recs)} records, {len(docs)} docs ({missing} not found)")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
