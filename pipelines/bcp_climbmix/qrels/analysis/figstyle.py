#!/usr/bin/env python3
"""Shared figure style for the paper's small multiples.

Every figure here is placed at 0.32\\linewidth, three to a row. LaTeX scales the
PDF down to that width, and any text in it shrinks by the same factor - which is
why a 9pt label authored on a 3.6in canvas arrives on the page at about 5.5pt.

The fix is to author each figure at the width it will actually occupy, so
\\includegraphics scales it by 1.0 and a 9pt label really is 9pt on the page.
FIG_W below is that width in inches; pass --figwidth to match your own
\\linewidth (0.32 x 6.9in text width by default).
"""

# 0.32 x 6.9in line width. Override per paper with --figwidth.
FIG_W = 2.2
FIG_RATIO = 0.82        # height / width


def rc(base=7.0):
    """rcParams for text that stays legible after LaTeX places the figure.

    Axis titles are bold and one step up; everything else sits just below them.
    """
    return {
        "font.family": "serif",
        "font.size": base,
        "axes.labelsize": base + 0.5,
        "axes.labelweight": "bold",
        "axes.titlesize": base + 1,
        "xtick.labelsize": base,
        "ytick.labelsize": base,
        "legend.fontsize": base - 0.5,
        "pdf.fonttype": 42,       # embed TrueType, no substitution downstream
    }


def size(width=FIG_W, ratio=FIG_RATIO):
    return (width, width * ratio)


def value_fontsize(base=7.0):
    """Bar-cap value labels: a step below the tick labels, never below 7pt."""
    return max(5.5, base - 0.5)


def save(fig, out, png=False):
    """Write at the canvas size, NOT a tight bbox.

    bbox_inches="tight" grows the canvas to whatever the labels need, so the PDF
    would no longer be the authored width and \includegraphics would scale it -
    shrinking the text again, which is the problem this module exists to solve.
    The layout engine fits the labels inside the fixed canvas instead.
    """
    fig.savefig(out)
    if png:
        fig.savefig(out[:-4] + ".png", dpi=300)
    import matplotlib.pyplot as plt
    plt.close(fig)
