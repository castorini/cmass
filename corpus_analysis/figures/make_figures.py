#!/usr/bin/env python3
"""Paper figures for the corpus analysis.

Emits both PDF (vector, TrueType-embedded, for the paper) and PNG (for the repository and
for GitHub preview). Figures come out individually rather than as multi-panel composites,
sized so a matching pair sits side by side at full column width:

    fig_length_a_p90       + fig_length_b_buckets       3.50 x 2.60 in
    fig_pie_a_affected     + fig_quartiles_duplicates   3.50 x 1.90 in
    fig_pie_a_affected     + fig_pie_b_removable        3.50 x 1.90 in

Consistency inside a pair is structural, not hand-matched: both pies run through one
routine with one set of layout constants and one shared table of label heights, so their
label rows line up when placed next to each other.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import math

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config
from figures.palette import (C_80, C_90, C_BAR, C_EXACT, C_MEDIAN, C_NONE, C_P75,
                             C_P90, C_70, GRID, INK, INK_2, INK_3, LEN_FIGSIZE,
                             LEN_MARGINS, PIE_FIGSIZE, PIE_FONT, PIE_LABEL_X, PIE_R,
                             PIE_XLIM, PIE_YLIM, RCPARAMS, TY_BANDS)

matplotlib.rcParams.update(RCPARAMS)

J70, J80, J90 = r"$J \geq 0.7$", r"$J \geq 0.8$", r"$J \geq 0.9$"


def save(fig, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    pdf = os.path.join(out_dir, name + ".pdf")
    png = os.path.join(out_dir, name + ".png")
    fig.savefig(pdf)
    fig.savefig(png, dpi=220)
    plt.close(fig)
    print(f"wrote {pdf} and {png}")


# ------------------------------------------------------------------ pies

def _in_arc(a, t1, t2):
    a, t1, t2 = a % 360, t1 % 360, t2 % 360
    return t1 <= a <= t2 if t1 <= t2 else (a >= t1 or a <= t2)


def _pie(out_dir, name, rows):
    """One pie with labels outside on leaders. `rows` is (label, value, colour, ty_key).

    A leader is drawn level whenever the label's height falls on its own wedge's arc; the
    anchor is solved for that height rather than fixed at the wedge's mid-angle. Where a
    wedge is too thin to span the height the leader slants instead, which is the honest
    fallback -- a level line would otherwise point at empty space beyond the pie.
    """
    total = sum(v for _, v, _, _ in rows)
    fig, ax = plt.subplots(figsize=PIE_FIGSIZE)
    wedges, _ = ax.pie([v for _, v, _, _ in rows], colors=[c for _, _, c, _ in rows],
                       startangle=90, counterclock=False, radius=1.0,
                       wedgeprops={"linewidth": 1.2, "edgecolor": "white"})
    for w, (label, val, _, key) in zip(wedges, rows):
        a = math.radians((w.theta1 + w.theta2) / 2)
        x, y = math.cos(a), math.sin(a)
        side = 1 if x >= 0 else -1
        h = TY_BANDS[key]
        ax_, ay_ = PIE_R * x, PIE_R * y
        v = h / PIE_R
        if -1.0 <= v <= 1.0:
            cand = (180.0 - math.degrees(math.asin(v))) if side < 0 \
                else math.degrees(math.asin(v))
            if _in_arc(cand, w.theta1, w.theta2):
                ax_, ay_ = PIE_R * math.cos(math.radians(cand)), h
        ax.annotate(f"{label}\n{val/total*100:.2f}%",
                    xy=(ax_, ay_), xytext=(PIE_LABEL_X * side, h),
                    ha="left" if side > 0 else "right", va="center",
                    fontsize=PIE_FONT, color=INK, linespacing=1.45,
                    arrowprops=dict(arrowstyle="-", color=INK_3, linewidth=0.7,
                                    shrinkA=0, shrinkB=4, connectionstyle="arc3,rad=0"))
    ax.set(aspect="equal")
    ax.set_xlim(-PIE_XLIM, PIE_XLIM)
    ax.set_ylim(-PIE_YLIM, PIE_YLIM)
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    save(fig, out_dir, name)
    for label, v, _, _ in rows:
        print(f"    {label:<20} {v:>13,}  {v/total*100:6.2f}%")


def fig_pie_affected(out_dir, fig_dir):
    """(a) Documents AFFECTED by duplication -- every member of a cluster counts."""
    c = {p["label"]: p["documents"]
         for p in json.load(open(f"{out_dir}/corpus_dup_chart_data.json"))["pie"]}
    _pie(fig_dir, "fig_pie_a_affected", [
        ("Unique", c["no duplicates"], C_NONE, "REST"),
        ("Exact duplicate", c["exact"], C_EXACT, "EXACT"),
        (J90, c["near_0.9"], C_90, "J90"),
        (J80, c["near_0.8"], C_80, "J80"),
        (J70, c["near_0.7"], C_70, "J70")])


def fig_pie_removable(out_dir, fig_dir):
    """(b) Documents REMOVABLE by dedup -- one representative per cluster is retained.

    Slices are incremental, so they partition the corpus: each threshold contributes only
    the copies removed beyond the stricter one above it.
    """
    r = json.load(open(f"{out_dir}/corpus_redundancy_complete.json"))
    N = r["corpus_documents"]
    ex = r["exact"]["redundant_copies"]
    r90, r80, r70 = (r[f"J>={t}"]["redundant_copies"] for t in (0.9, 0.8, 0.7))
    rows = [("Retained", N - r70, C_NONE, "REST"),
            ("Exact copy", ex, C_EXACT, "EXACT"),
            (J90, r90 - ex, C_90, "J90"),
            (J80, r80 - r90, C_80, "J80"),
            (J70, r70 - r80, C_70, "J70")]
    assert sum(v for _, v, _, _ in rows) == N, "slices must partition the corpus"
    _pie(fig_dir, "fig_pie_b_removable", rows)


# ------------------------------------------------------------------ duplicates per doc

def fig_quartiles(out_dir, fig_dir):
    """Duplicates per document: minimum through p90, a break, then on to the maximum.

    The mean is a bad summary here -- 14.82 for exact duplicates against a median of 1 --
    so the row carries percentiles. The axis breaks past p90 because the maxima are two to
    three orders of magnitude beyond it; drawn to scale every band would be a sliver.

    Percentiles are identified by rule colour and labelled with their value alone. Naming
    each one inline collided as soon as two marks shared an x, which happens in every band
    where median == p75.
    """
    m = json.load(open(f"{out_dir}/corpus_dup_moments.json"))
    # row order mirrors the composition pie's label column so the pair scans together
    rows = [(J70, "near_0.7"), (J80, "near_0.8"), (J90, "near_0.9"),
            ("Exact duplicate", "exact")]
    cols = [C_70, C_80, C_90, C_EXACT]
    ANN = dict(fontsize=5.4, color=INK_3)
    DY, RULE = 0.02, 0.20
    BREAK_X, TICK_X, XHI = 12.4, 14.2, 17.8
    MINIMUM = 1

    fig, ax = plt.subplots(figsize=PIE_FIGSIZE)
    ys = list(range(len(rows)))[::-1]
    H = 0.23
    for yi, (_, k), c in zip(ys, rows, cols):
        d = m[k]
        hi, med, p75 = d["p90"], d["median"], d["p75"]
        ax.add_patch(plt.Rectangle((MINIMUM, yi - H / 2), hi - MINIMUM, H,
                                   facecolor=c, edgecolor="none", zorder=3))
        for q, col in ((med, C_MEDIAN), (p75, C_P75), (hi, C_P90)):
            ax.plot([q, q], [yi - RULE, yi + RULE], color=col, linewidth=1.0,
                    linestyle=(0, (2.2, 1.6)), zorder=6, solid_capstyle="butt")
        ax.plot([hi + 0.25, TICK_X], [yi, yi], color="#b6b4ad", linewidth=0.7,
                linestyle=(0, (2.2, 2.0)), zorder=2)
        for dx in (-0.22, 0.12):
            ax.plot([BREAK_X + dx, BREAK_X + dx + 0.32], [yi - 0.12, yi + 0.12],
                    color=INK_3, linewidth=0.7, zorder=4)
        ax.plot([TICK_X, TICK_X], [yi - 0.13, yi + 0.13], color=INK_3, linewidth=0.9,
                zorder=4)
        ax.text(med, yi - RULE - DY, f"{med}", ha="center", va="top",
                fontsize=5.4, color=C_MEDIAN)
        ax.text(p75, yi + RULE + DY, f"{p75}", ha="center", va="bottom",
                fontsize=5.4, color=C_P75)
        ax.text(hi, yi + RULE + DY, f"{hi}", ha="center", va="bottom",
                fontsize=5.4, color=C_P90)
        ax.text(TICK_X + 0.35, yi, f"{d['max']:,}", ha="left", va="center", **ANN)

    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows], fontsize=8)
    ax.set_xlabel("Duplicates per document")
    ax.set_xlim(0, XHI)
    ax.set_xticks([0, 5, 10])
    ax.set_ylim(-0.62, len(rows) - 0.38)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.xaxis.grid(True, color=GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    handles = [plt.Line2D([0], [0], color=cc, linewidth=1.0, linestyle=(0, (2.2, 1.6)),
                          label=nm)
               for cc, nm in ((C_MEDIAN, "median"), (C_P75, "p75"), (C_P90, "p90"))]
    handles.append(plt.Line2D([0], [0], color=INK_3, linewidth=0.9, label="max"))
    ax.legend(handles=handles, loc="center left", bbox_to_anchor=(1.005, 0.5), ncol=1,
              frameon=False, fontsize=5.8, handlelength=1.4, labelspacing=0.75,
              handletextpad=0.4, borderaxespad=0.0)
    fig.subplots_adjust(left=0.305, right=0.795, top=0.955, bottom=0.235)
    save(fig, fig_dir, "fig_quartiles_duplicates")


# ------------------------------------------------------------------ length

def _len_axes(ax):
    ax.set_ylabel("Documents (millions)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.yaxis.grid(True, color=GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)


def fig_length_a(out_dir, fig_dir, tokenizer):
    """(a) Linear histogram of the clipped range, after Figure 4(a) of arXiv:2508.06600.

    Bars are a round number of tokens wide over a round window just past p90, so a caption
    can state both exactly.
    """
    d = json.load(open(config.length_stats_path(out_dir, tokenizer)))
    h = d["histogram_fixed_bins"]
    counts = np.array(h["counts"], dtype=float)
    edges = np.array(h["edges"], dtype=float)
    med = d["overall"]["median"]

    fig, ax = plt.subplots(figsize=LEN_FIGSIZE)
    ax.bar(edges[:-1], counts / 1e6, width=h["bin_width"], align="edge",
           color=C_BAR, linewidth=0, zorder=3)
    ax.axvline(med, color=INK_2, linewidth=0.9, linestyle=(0, (4, 2)), zorder=4)
    ax.annotate(f"median {med:.0f}", xy=(med, ax.get_ylim()[1] * 0.94),
                xytext=(5, 0), textcoords="offset points", fontsize=7.5, color=INK_2)
    ax.set_xlabel("Tokens")
    ax.set_xlim(0, h["hi"])
    step = 300 if h["hi"] > 1000 else 200
    ax.set_xticks(list(range(0, h["hi"] + 1, step)))
    _len_axes(ax)
    fig.subplots_adjust(**LEN_MARGINS)
    save(fig, fig_dir, "fig_length_a_p90")
    i = int(np.argmax(counts))
    print(f"    {h['bins']} bars x {h['bin_width']} tokens, range 0-{h['hi']}")
    print(f"    {h['documents_shown']:,} docs shown ({h['share_shown']*100:.2f}%)")
    print(f"    tallest bar {int(counts.max()):,} docs at "
          f"{i*h['bin_width']}-{i*h['bin_width']+h['bin_width']} tokens")


def fig_length_b(out_dir, fig_dir, tokenizer):
    """(b) Full range on log-spaced buckets, so the tail stays visible."""
    d = json.load(open(config.length_stats_path(out_dir, tokenizer)))
    b = [x for x in d["buckets"] if x["documents"]]
    # integer-dividing by 1000 collapses 1024 and 1536 to the same "1k"
    lab = [(f"{x['lo']/1024:g}k" if x["lo"] >= 1024 else str(x["lo"])) for x in b]
    lab[-1] += "+"
    vals = [x["documents"] / 1e6 for x in b]
    xs = np.arange(len(b))

    fig, ax = plt.subplots(figsize=LEN_FIGSIZE)
    ax.bar(xs, vals, width=0.82, color=C_BAR, linewidth=0, zorder=3)
    ax.set_xticks(xs)
    ax.set_xticklabels(lab, fontsize=7, rotation=45, ha="right")
    ax.set_xlabel("Tokens (bucket lower bound)")
    _len_axes(ax)
    top = max(vals)
    for x, v in zip(xs, vals):
        if v > top * 0.25:
            ax.text(x, v + top * 0.02, f"{v:.0f}", ha="center", va="bottom",
                    fontsize=6.8, color=INK_2)
    fig.subplots_adjust(**LEN_MARGINS)
    save(fig, fig_dir, "fig_length_b_buckets")
    for x, lo in zip(b, lab):
        print(f"    {lo:>6} {x['documents']:>13,}  {x['share']*100:6.2f}%")


def main():
    ap = config.base_parser(__doc__)
    ap.add_argument("--fig-dir", default=None,
                    help="output directory (default: <this file's dir>/out)")
    ap.add_argument("--tokenizer", default=config.DEFAULT_TOKENIZER,
                    choices=sorted(config.TOKENIZERS))
    ap.add_argument("--only", default="", help="comma-separated subset: "
                    "pie_affected,pie_removable,quartiles,length_a,length_b")
    a = ap.parse_args()
    fig_dir = a.fig_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

    all_figs = {
        "pie_affected": lambda: fig_pie_affected(a.out_dir, fig_dir),
        "pie_removable": lambda: fig_pie_removable(a.out_dir, fig_dir),
        "quartiles": lambda: fig_quartiles(a.out_dir, fig_dir),
        "length_a": lambda: fig_length_a(a.out_dir, fig_dir, a.tokenizer),
        "length_b": lambda: fig_length_b(a.out_dir, fig_dir, a.tokenizer),
    }
    want = [w.strip() for w in a.only.split(",") if w.strip()] or list(all_figs)
    if a.dry_run:
        print(f"out-dir  : {a.out_dir}\nfig-dir  : {fig_dir}\n"
              f"tokenizer: {a.tokenizer}\nfigures  : {want}")
        return
    for w in want:
        if w not in all_figs:
            raise SystemExit(f"unknown figure {w!r}; known: {', '.join(all_figs)}")
        all_figs[w]()
        print()


if __name__ == "__main__":
    main()
