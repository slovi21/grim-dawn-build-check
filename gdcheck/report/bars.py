"""Cells and percentages of the progress bars (.70, plan A.2 / A.4) - the one place the renderers share.

The readiness bar shows the score band by band (EARLY 0-33, MID 34-66, ENDGAME 67-100), full up to the
score. An area bar shows the area's value 0-100 in the colour of its status (OK green, RISK yellow, GAP
red); INFO has no bar. Chat Markdown draws coloured squares, the text view ASCII, HTML a width in percent.
Every rounding is DOWN (B7): a full bar always means 'everything met'. No game rules here (F-008)."""
from __future__ import annotations

import math

MD_CELL = {"OK": "🟩", "RISK": "🟨", "GAP": "🟥"}
MD_EMPTY = "⬜"
MD_CELLS = 5            # per band and per area in the chat view
TEXT_BAND_CELLS = 10    # per band in the text view
TEXT_AREA_CELLS = 5     # per area in the text view; '[#####]' is 7 characters
_EPS = 1e-9


def fill(score: float, low: float, high: float) -> float:
    """How much of the band [low, high] the score covers, 0..1 (an empty band is all or nothing)."""
    if high <= low:
        return 1.0 if score >= high else 0.0
    return min(max((score - low) / (high - low), 0.0), 1.0)


def cells(fraction: float, n: int) -> int:
    """Whole cells of n for a fraction 0..1, rounded down."""
    return min(max(int(math.floor(fraction * n + _EPS)), 0), n)


def pct(fraction: float) -> int:
    """Whole percent of a fraction 0..1, rounded down (the width of an HTML bar)."""
    return min(max(int(math.floor(fraction * 100 + _EPS)), 0), 100)


def _has_bar(value, status: str) -> bool:
    return value is not None and status in MD_CELL


def md_readiness(score: int, bands) -> str:
    """'EARLY 🟩🟩🟩🟩🟩 MID 🟩🟩🟩🟩🟩 ENDGAME 🟩🟩🟩⬜⬜' - green up to the score."""
    out = []
    for name, low, high in bands:
        n = cells(fill(score, low, high), MD_CELLS)
        out.append(f"{name} {MD_CELL['OK'] * n}{MD_EMPTY * (MD_CELLS - n)}")
    return " ".join(out)


def text_readiness(score: int, bands) -> str:
    """'EARLY [##########] MID [##########] ENDGAME [#######---]' (ASCII)."""
    out = []
    for name, low, high in bands:
        n = cells(fill(score, low, high), TEXT_BAND_CELLS)
        out.append(f"{name} [{'#' * n}{'-' * (TEXT_BAND_CELLS - n)}]")
    return " ".join(out)


def md_area(value: int | None, status: str) -> str:
    """Five squares in the colour of the status, '-' without a bar (no value or INFO)."""
    if not _has_bar(value, status):
        return "-"
    n = cells(value / 100, MD_CELLS)
    return MD_CELL[status] * n + MD_EMPTY * (MD_CELLS - n)


def text_area(value: int | None, status: str) -> str:
    """'[##---]' (ASCII), 7 spaces without a bar so the text column stays aligned."""
    if not _has_bar(value, status):
        return " " * (TEXT_AREA_CELLS + 2)
    n = cells(value / 100, TEXT_AREA_CELLS)
    return f"[{'#' * n}{'-' * (TEXT_AREA_CELLS - n)}]"


def html_value(value: int | None, status: str) -> int | None:
    """The value of an HTML meter, None where the area has no bar."""
    return value if _has_bar(value, status) else None
