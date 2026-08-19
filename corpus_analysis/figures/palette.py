#!/usr/bin/env python3
"""Colours and geometry shared by every figure.

The duplicate tiers are ORDERED (exact > 0.9 > 0.8 > 0.7), so they use a validated
single-hue ordinal ramp -- darker means a stronger duplicate relationship -- rather than
arbitrary categorical hues. The "everything else" mass gets a desaturated slate so it
reads as ground rather than as a competing category. Colour follows the category, never
its position in a chart, so a tier keeps its hue across every figure.

The percentile marker hues are a separate encoding (percentiles, not categories) and were
picked against a colour-vision validator: orange/aqua/violet clears every all-pairs gate
(worst CVD dE 9.2, normal-vision 27.6). Green/orange was rejected at dE 3.2 under
protanopia -- the classic red-green confusion.

Canvases are FIXED rather than cropped to ink. Two figures meant to sit side by side must
emit identically sized files or LaTeX scales them differently; cropping gave the two pies
different widths purely because one legend label was longer.
"""

C_NONE = "#a8b2bd"      # no duplicates / retained
C_EXACT = "#104281"     # exact duplicates    (darkest)
C_90 = "#1c5cab"        # J >= 0.9
C_80 = "#3987e5"        # J >= 0.8
C_70 = "#86b6ef"        # J >= 0.7            (lightest)
C_BAR = "#2a78d6"       # single-series bars/histograms

C_MEDIAN = "#eb6834"    # percentile markers
C_P75 = "#1baf7a"
C_P90 = "#4a3aa7"

INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

RCPARAMS = {
    # fonttype 42 embeds TrueType rather than Type 3, which most venues reject
    "pdf.fonttype": 42, "ps.fonttype": 42,
    "font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"],
    "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.edgecolor": AXIS, "axes.linewidth": 0.6,
    "xtick.color": INK_2, "ytick.color": INK,
    "text.color": INK, "axes.labelcolor": INK,
    "figure.dpi": 150,
}

# pie pair: XLIM/YLIM held equal to the figure aspect so the data box fills the canvas
PIE_FIGSIZE = (3.5, 1.9)
PIE_XLIM, PIE_YLIM = 2.67, 1.4489       # 2.67/1.4489 == 3.5/1.9
PIE_LABEL_X = 1.20
PIE_FONT = 9.0
PIE_R = 0.97

# One table of label heights for BOTH pies, so their label rows line up side by side.
# Taken from the redundancy pie, whose duplicate slices sit in a narrower arc and so
# constrain the layout more tightly.
TY_BANDS = {"J70": 0.99, "J80": 0.39, "J90": -0.15, "EXACT": -0.69, "REST": 0.0}

# length pair: identical margins, sized for the rotated tick labels of the bucket panel,
# so both plot areas line up. The right margin also leaves room for the final tick label
# to centre on the axis end rather than being clipped by the canvas edge.
LEN_FIGSIZE = (3.5, 2.6)
LEN_MARGINS = dict(left=0.165, right=0.955, top=0.965, bottom=0.235)

QUART_FIGSIZE = PIE_FIGSIZE             # pairs with the composition pie
