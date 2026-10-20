#!/usr/bin/env python3
"""Review store and reviewed-JSONL export for the review site.

The store is one JSON object keyed "<dataset>~<record_id>", the same keys the page uses. Each value:

  {"decision": "accept" | "accept_with_fixes" | "reject" | "unsure" | null,
   "comment": str,
   "hops": {"<hop id>": {"agree": true | false | null, "comment": str}},
   "constraints": {"<constraint id>": {"agree": true | false | null, "comment": str}},
   "updated_at": ISO-8601}

The export writes reviewed/<dataset>_reviewed.jsonl: every record of data/<dataset>.json with a
"review" field (the stored review, or null).

  python3 reviews.py export [--site DIR] [--store reviews.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


class Site:
    """Paths of one review site: data/ (built by build_site_data.py), reviews/, reviewed/."""

    def __init__(self, root: Path = HERE, store: Path | None = None):
        self.root = Path(root)
        self.data = self.root / "data"
        self.store = Path(store) if store else self.root / "reviews" / "reviews.json"
        self.reviewed = self.root / "reviewed"

    def datasets(self) -> list[str]:
        manifest = self.data / "manifest.json"
        if not manifest.exists():
            return []
        return [d["id"] for d in json.loads(manifest.read_text()).get("datasets", [])]

    def reviewed_file(self, ds: str) -> Path:
        return self.reviewed / f"{ds}_reviewed.jsonl"

    def load_store(self) -> dict:
        try:
            return json.loads(self.store.read_text())
        except FileNotFoundError:
            return {}

    def save_store(self, store: dict) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store.with_suffix(".tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1, sort_keys=True))
        tmp.replace(self.store)

    def export(self, store: dict, datasets: list[str] | None = None) -> dict:
        counts = {}
        for ds in datasets or self.datasets():
            records = json.loads((self.data / f"{ds}.json").read_text())["records"]
            dst = self.reviewed_file(ds)
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(".jsonl.tmp")
            n = 0
            with open(tmp, "w") as g:
                for rec in records:
                    rv = store.get(f"{ds}~{rec.get('record_id', rec.get('id'))}")
                    rec["review"] = rv or None
                    n += bool(rv)
                    g.write(json.dumps(rec, ensure_ascii=False) + "\n")
            tmp.replace(dst)
            counts[ds] = n
        return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Export the review store as reviewed JSONL.")
    ap.add_argument("command", choices=["export"])
    ap.add_argument("--site", default=str(HERE), help="review site directory (default: this directory)")
    ap.add_argument("--store", help="review store to export (default: <site>/reviews/reviews.json)")
    a = ap.parse_args()
    site = Site(Path(a.site), a.store)
    print("reviewed records:", site.export(site.load_store()))
