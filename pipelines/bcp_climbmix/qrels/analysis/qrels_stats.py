#!/usr/bin/env python3
"""Step 6a: distributions over a finalized qrels file.

Two views, plus the figures for each:
  per question - how many relevant documents each question carries
  per hop      - how many documents support each individual hop

Hops marked "not needed" during judging carry zero supporting documents by
construction and are EXCLUDED from the per-hop view; keeping them would put a
spurious zero bucket in the histogram. They are derived as the zero-support
hops, and cross-checked against the `skipped` flag in the qrels file, which
fails loudly if the two ever disagree.

Usage:
  python3 qrels_stats.py qrels.jsonl --outdir out --png
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics as stats
from collections import Counter
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MaxNLocator

import figstyle

GREY, BLUE, RED = "#9aa0a6", "#4573ae", "#a6584e"

# Inclusive on both ends; the last bucket is open-ended.
Q_BUCKETS = [(1, 24), (25, 49), (50, 99), (100, 149), (150, 199), (200, 299), (300, None)]
H_BUCKETS = [(1, 2), (3, 5), (6, 10), (11, 20), (21, 40), (41, 70), (71, None)]


def bucket_label(lo, hi):
    return f"{lo}–{hi}" if hi is not None else f"{lo}+"


def bucketize(values, buckets):
    counts = [0] * len(buckets)
    for v in values:
        for i, (lo, hi) in enumerate(buckets):
            if v >= lo and (hi is None or v <= hi):
                counts[i] += 1
                break
    assert sum(counts) == len(values), "buckets dropped a value; check the edges"
    return counts


def load(path):
    per_question, per_hop = [], []
    for line in open(path):
        if not line.strip():
            continue
        r = json.loads(line)
        support = Counter()
        for hops in r["qrels"].values():
            support.update(hops)
        per_question.append({"qid": str(r["qid"]), "docs": len(r["qrels"]),
                             "pairs": sum(len(v) for v in r["qrels"].values())})
        for h in r["hops"]:
            per_hop.append({"qid": str(r["qid"]), "hop": h["index"],
                            "n": support.get(h["index"], 0),
                            "type": h.get("hop_type", "Required"),
                            "skipped": bool(h.get("skipped"))})
    return per_question, per_hop


def split_live(per_hop):
    zero = {(h["qid"], h["hop"]) for h in per_hop if h["n"] == 0}
    flagged = {(h["qid"], h["hop"]) for h in per_hop if h["skipped"]}
    if flagged and zero != flagged:
        raise SystemExit("zero-support hops do not match the skipped flags\n"
                         f"  zero only:    {sorted(zero - flagged)}\n"
                         f"  skipped only: {sorted(flagged - zero)}")
    return [h for h in per_hop if (h["qid"], h["hop"]) not in zero], sorted(zero)


def base_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="0.9", lw=0.6)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))


def legend_above(ax, ncol=1):
    ax.legend(frameon=False, ncol=ncol, loc="lower left", bbox_to_anchor=(0.0, 1.01),
              borderaxespad=0.0, handlelength=1.2, columnspacing=1.0, handletextpad=0.5)


def fig_per_question(per_question, out, width, png):
    counts = bucketize([p["docs"] for p in per_question], Q_BUCKETS)
    labels = [bucket_label(*b) for b in Q_BUCKETS]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=figstyle.size(width), layout="constrained")
    base_axes(ax)
    ax.bar(x, counts, width=0.62, color=BLUE)
    for xi, c in zip(x, counts):
        if c:
            ax.text(xi, c + max(counts) * 0.02, str(c), ha="center", va="bottom",
                    fontsize=figstyle.value_fontsize(plt.rcParams["font.size"]), color="0.15")
    ax.set_xlabel("Relevant Documents\nper Question")
    ax.set_ylabel("Questions")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", rotation_mode="anchor")
    ax.set_ylim(0, max(counts) * 1.16)
    figstyle.save(fig, out, png)
    return labels, counts


def fig_per_hop(live, out, width, png):
    req = bucketize([h["n"] for h in live if h["type"] == "Required"], H_BUCKETS)
    conf = bucketize([h["n"] for h in live if h["type"] != "Required"], H_BUCKETS)
    totals = [a + b for a, b in zip(req, conf)]
    labels = [bucket_label(*b) for b in H_BUCKETS]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=figstyle.size(width), layout="constrained")
    base_axes(ax)
    ax.bar(x, req, width=0.62, color=BLUE, label="required")
    ax.bar(x, conf, width=0.62, bottom=req, color=RED, label="confirmatory")
    for xi, t in zip(x, totals):
        if t:
            ax.text(xi, t + max(totals) * 0.02, str(t), ha="center", va="bottom",
                    fontsize=figstyle.value_fontsize(plt.rcParams["font.size"]), color="0.15")
    ax.set_xlabel("Supporting Documents\nper Hop")
    ax.set_ylabel("Question–Hop Pairs")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", rotation_mode="anchor")
    ax.set_ylim(0, max(totals) * 1.30)
    legend_above(ax)
    figstyle.save(fig, out, png)
    return labels, req, conf


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("qrels")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--figwidth", type=float, default=figstyle.FIG_W,
                    help="authored width in inches; set to the fraction of \\linewidth "
                         "the figure will occupy, so LaTeX never scales it")
    ap.add_argument("--fontsize", type=float, default=7.0)
    ap.add_argument("--png", action="store_true")
    ap.add_argument("--no-csv", action="store_true")
    args = ap.parse_args()

    plt.rcParams.update(figstyle.rc(args.fontsize))
    os.makedirs(args.outdir, exist_ok=True)
    per_question, per_hop = load(args.qrels)
    live, excluded = split_live(per_hop)

    docs = [p["docs"] for p in per_question]
    supp = [h["n"] for h in live]
    print(f"{len(per_question)} questions, {sum(docs)} relevant documents, "
          f"{sum(p['pairs'] for p in per_question)} document-hop pairs")
    print(f"excluded {len(excluded)} not-needed hop(s); {len(per_hop)} -> {len(live)} live")
    for name, v in (("documents per question", docs), ("documents per live hop", supp)):
        q = stats.quantiles(v, n=4)
        print(f"  {name:<24} min {min(v)}  p25 {q[0]:.0f}  median {stats.median(v):.0f}  "
              f"mean {stats.mean(v):.1f}  p75 {q[2]:.0f}  max {max(v)}")

    ql, qc = fig_per_question(per_question, f"{args.outdir}/fig_qrels_per_question.pdf",
                              args.figwidth, args.png)
    hl, hr, hc = fig_per_hop(live, f"{args.outdir}/fig_docs_per_hop.pdf",
                             args.figwidth, args.png)
    if not args.no_csv:
        with open(f"{args.outdir}/qrels_per_question.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["relevant_docs_per_question", "questions"])
            w.writerows(zip(ql, qc))
        with open(f"{args.outdir}/docs_per_hop.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["docs_per_hop", "required", "confirmatory", "all"])
            w.writerows((l, r, c, r + c) for l, r, c in zip(hl, hr, hc))
    print(f"wrote figures to {args.outdir}")


if __name__ == "__main__":
    main()
