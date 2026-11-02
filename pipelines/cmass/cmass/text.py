"""Text matching used everywhere evidence is checked: quotes must occur in their cited document
verbatim, ignoring only differences in whitespace (spaces, tabs, line breaks)."""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def norm_ws(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def locate(text: str, frag: str) -> tuple[int, int, str] | None:
    """Find frag in text: (start, end, mode) with mode exact | ws (whitespace-insensitive) | ci
    (also case-insensitive), or None."""
    i = text.find(frag)
    if i >= 0:
        return i, i + len(frag), "exact"
    pat = r"\s+".join(re.escape(t) for t in frag.split())
    if not pat:
        return None
    m = re.search(pat, text)
    if m:
        return m.start(), m.end(), "ws"
    m = re.search(pat, text, re.IGNORECASE)
    if m:
        return m.start(), m.end(), "ci"
    return None


def is_verbatim(text: str, quote: str) -> str | None:
    """'exact' or 'ws' when the quote occurs in the text (case-sensitive), else None."""
    hit = locate(text, quote)
    return hit[2] if hit and hit[2] in ("exact", "ws") else None


def split_spans(text: str) -> list[str]:
    """A constraint may name non-contiguous parts of the question, joined by '...' or '…'."""
    return [p.strip() for p in re.split(r"\s*(?:\.\.\.|…)\s*", text or "") if p.strip()]


def span_between(text: str, start: str, end: str) -> str | None:
    """The exact document span from the first occurrence of `start` to the end of the next `end`."""
    a = locate(text, start)
    if not a:
        return None
    b = locate(text[a[0]:], end)
    if not b:
        return None
    return norm_ws(text[a[0]:a[0] + b[1]])
