#!/usr/bin/env python3
"""Step 6b: near-duplicate redundancy inside each question's qrels.

For every question, count how many of its relevant documents have at least one
near-duplicate partner (Jaccard >= threshold) that is ALSO relevant for the same
question. Pairs are counted within a question, never across questions, so the
number answers "how much of this query's judged pool is redundant?".

Writes a per-question JSONL of counts, then a bucketed distribution figure.

Neighbours come either from the corpus near-duplicate parquet (--near-dup-dir)
or from a cached scan written by step 5 (--nb), which is much faster on a rerun.

Usage:
  python3 qrels_near_dup.py qrels.jsonl --near-dup-dir /path/doc_duplicates_t70 \
      --jsonl out/near_dup_per_question.jsonl --outdir out --png
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as stats
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MaxNLocator

import figstyle

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from step5_expand_duplicates import scan_near          # noqa: E402  (shared id normalisation)

BLUE = "#4573ae"

# HALF-OPEN [lo, hi): the values are continuous percentages, so inclusive integer
# ends silently drop everything between 9 and 10, 19 and 20, and so on.
BUCKETS = [(0, 10), (10, 20), (20, 30), (30, 40), (40, 50), (50, 60), (60, 70), (70, None)]


def bucket_label(lo, hi):
    return f"{lo}–{hi}" if hi is not None else f"{lo}+"


def neighbours_from_cache(paths, threshold):
    adj = defaultdict(dict)
    for path in paths:
        for doc, rec in json.loads(Path(path).read_text()).items():
            for e in rec.get("near", []):
                if e["jaccard"] < threshold:
                    continue
                for a, b in ((doc, e["doc_id"]), (e["doc_id"], doc)):
                    adj[a][b] = max(adj[a].get(b, 0.0), e["jaccard"])
    return adj


def neighbours_from_parquet(parquet_dir, docs, threshold):
    adj = defaultdict(dict)
    for doc, rec in scan_near(parquet_dir, docs, threshold).items():
        for e in rec["near"]:
            for a, b in ((doc, e["doc_id"]), (e["doc_id"], doc)):
                adj[a][b] = max(adj[a].get(b, 0.0), e["jaccard"])
    return adj


def clusters_within(docs, adj):
    """Connected components of the near-duplicate graph restricted to `docs`."""
    seen, out = set(), []
    for start in sorted(docs):
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            d = stack.pop()
            comp.append(d)
            for nxt in adj.get(d, ()):
                if nxt in docs and nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        out.append(sorted(comp))
    return out


def analyse(qrels_path, adj):
    rows = []
    for line in open(qrels_path):
        if not line.strip():
            continue
        r = json.loads(line)
        docs = set(r["qrels"])
        groups = [c for c in clusters_within(docs, adj) if len(c) > 1]
        dup = sorted({d for c in groups for d in c})
        rows.append({
            "qid": str(r["qid"]),
            "n_docs": len(docs),
            "n_near_dup": len(dup),
            "pct_near_dup": round(len(dup) / len(docs) * 100, 2),
            "n_groups": len(groups),
            # copies beyond one representative per group: what dedup would remove
            "n_redundant": len(dup) - len(groups),
            "largest_group": max((len(c) for c in groups), default=0),
        })
    return rows


def figure(rows, out, width, png):
    counts = [0] * len(BUCKETS)
    for r in rows:
        for i, (lo, hi) in enumerate(BUCKETS):
            if r["pct_near_dup"] >= lo and (hi is None or r["pct_near_dup"] < hi):
                counts[i] += 1
                break
    assert sum(counts) == len(rows), f"buckets hold {sum(counts)} of {len(rows)} questions"
    labels = [bucket_label(*b) for b in BUCKETS]
    x = np.arange(len(labels))

    fig, ax = plt.subplots(figsize=figstyle.size(width), layout="constrained")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="0.9", lw=0.6)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
    ax.bar(x, counts, width=0.62, color=BLUE)
    for xi, c in zip(x, counts):
        if c:
            ax.text(xi, c + max(counts) * 0.02, str(c), ha="center", va="bottom",
                    fontsize=figstyle.value_fontsize(plt.rcParams["font.size"]), color="0.15")
    ax.set_xlabel("Near-Duplicate Share\nof Qrels (%)")
    ax.set_ylabel("Questions")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", rotation_mode="anchor")
    ax.set_ylim(0, max(counts) * 1.16)
    figstyle.save(fig, out, png)
    return labels, counts


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("qrels")
    ap.add_argument("--near-dup-dir", default=os.environ.get("QRELS_NEAR_DUP_DIR", ""))
    ap.add_argument("--nb", nargs="*", default=[], help="cached scan JSONs instead of the parquet")
    ap.add_argument("--threshold", type=float, default=0.7)
    ap.add_argument("--jsonl", help="per-question counts (default <outdir>/near_dup_per_question.jsonl)")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--figwidth", type=float, default=figstyle.FIG_W)
    ap.add_argument("--fontsize", type=float, default=7.0)
    ap.add_argument("--png", action="store_true")
    args = ap.parse_args()

    plt.rcParams.update(figstyle.rc(args.fontsize))
    os.makedirs(args.outdir, exist_ok=True)
    jsonl = args.jsonl or os.path.join(args.outdir, "near_dup_per_question.jsonl")

    if args.nb:
        adj = neighbours_from_cache(args.nb, args.threshold)
    elif args.near_dup_dir:
        docs = {d for l in open(args.qrels) if l.strip() for d in json.loads(l)["qrels"]}
        adj = neighbours_from_parquet(args.near_dup_dir, docs, args.threshold)
    else:
        raise SystemExit("pass --near-dup-dir (or QRELS_NEAR_DUP_DIR) or --nb")

    rows = analyse(args.qrels, adj)
    with open(jsonl, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    labels, counts = figure(rows, f"{args.outdir}/fig_qrels_near_dup.pdf",
                            args.figwidth, args.png)
    docs = sum(r["n_docs"] for r in rows)
    dups = sum(r["n_near_dup"] for r in rows)
    pct = [r["pct_near_dup"] for r in rows]
    q = stats.quantiles(pct, n=4)
    print(f"{len(rows)} questions, {docs} judged rows, {dups} with a near-duplicate "
          f"sibling in the same query ({dups / docs * 100:.1f}%)")
    print(f"  collapsing each group to one representative would drop "
          f"{sum(r['n_redundant'] for r in rows)} rows")
    print(f"  per-query %: min {min(pct):.1f}  p25 {q[0]:.1f}  median {stats.median(pct):.1f}  "
          f"mean {stats.mean(pct):.1f}  p75 {q[2]:.1f}  max {max(pct):.1f}")
    for lab, c in zip(labels, counts):
        print(f"    {lab:>6}%  {c:2d}")
    print(f"wrote {jsonl} and {args.outdir}/fig_qrels_near_dup.pdf")


if __name__ == "__main__":
    main()
