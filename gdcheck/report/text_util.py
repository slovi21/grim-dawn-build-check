"""Number and text helpers of the Player View rows (B7: no decimals, a gap never reads as met).
Moved out of build.py (.70, K1: build.py is at the module size gate); build.py and area_text.py import them."""
from __future__ import annotations

import math
import re


def num(v, unit=""):
    if v is None:
        return "-"
    if abs(v) >= 1000:
        return f"{v:,.0f}{unit}"
    if abs(v - round(v)) < 0.05:
        return f"{v:.0f}{unit}"
    return f"{v:.1f}{unit}"


_DEC_RE = re.compile(r"(?<![\d.,])(-?\d{1,3}(?:,\d{3})+|-?\d+)\.(\d+)(?![\d.])")


def int_text(text: str) -> str:
    """B7: every decimal number of a Player View text as a whole number, rounded DOWN - a value short of
    its target never reads as reaching it (99.6% -> 99%, never '100% - target 100%'). Version numbers
    (1.3.0.8) stay as they are."""
    def floor(m):
        v = math.floor(float(f"{m.group(1).replace(',', '')}.{m.group(2)}"))
        return f"{v:,}" if "," in m.group(1) else str(v)
    return _DEC_RE.sub(floor, str(text or ""))


def toward_gap(now: float, target: float | None) -> int:
    """A value short of its target as a whole number: rounded normally (the Expert View's figure), but down
    when that would reach or pass the target - or up where lower is better (now above a 'max' target) - so a
    gap never reads as met (99.6% of 100% -> 99%; Health 14,960.6 of 16,000 -> 14,961)."""
    v = int(round(now))                                     # as the Expert View's '{:.0f}' shows it
    if target is None:
        return v
    if now > target:                                        # lower is better: never at/below the max
        return math.ceil(now) if v <= target else v
    return math.floor(now) if v >= target else v


def player_now(r, a) -> str:
    """now_text of a target for the Player View (B7): the value of a target that is not met rounded normally,
    towards 'missing' only where normal rounding would reach the target (toward_gap); resistance lists
    rebuilt from the values."""
    t = r.now_text
    if r.status not in ("gap", "tight"):
        return t
    if r.id in ("res_ultimate", "res_own_cap") and " - below: " in t and a is not None:
        from ..analysis import stage as STG
        vals = STG.ultimate_resists(a.ctx, a.snap) if r.id == "res_ultimate" else a.snap.sheet.resists
        head, lst = t.split(" - below: ", 1)
        items = []
        for x in lst.split(", "):                         # 'Fire 54%' / 'Fire 79/80%'
            name, _, num = x.rpartition(" ")
            v = (vals.get(name) or {}).get("value")
            m = re.match(r"-?[\d.]+", num)
            cap = re.match(r"/([\d.]+)", num[m.end():]) if m else None      # 'Fire 79/80%': own cap 80
            tgt = float(cap.group(1)) if cap else (vals.get(name) or {}).get("target",
                                                                             (vals.get(name) or {}).get("cap"))
            items.append(f"{name} {toward_gap(v, tgt)}{num[m.end():]}" if v is not None and m else x)
        return f"{head} - below: {', '.join(items)}"
    now, target = getattr(r, "now", None), getattr(r, "target", None)
    if not isinstance(now, (int, float)) or not isinstance(target, (int, float)):
        return t
    m = re.match(r"-?[\d,]+(?:\.\d+)?", t)
    if not m or t[m.end():m.end() + 1] == "/":                   # counts ('7/9') stay
        return t
    v = toward_gap(now, target)
    return (f"{v:,}" if "," in m.group(0) or abs(v) >= 1000 else str(v)) + t[m.end():]


def worst_first(now_text: str, n: int = 9) -> str:
    """'4/9 - below: Fire 54%, ...' -> lowest first, at most n and '+k more' (.62, product review: the
    Resistances row was cut at 70 characters and hid resistances; the top issue lists all of them)."""
    if " - below: " not in now_text:
        return now_text
    head, lst = now_text.split(" - below: ", 1)
    items = lst.split(", ")

    def val(x: str) -> float:
        m = re.search(r"(-?\d+(?:\.\d+)?)", x.rsplit(" ", 1)[-1])
        return float(m.group(1)) if m else 0.0
    worst = sorted(items, key=val)
    more = f" +{len(items) - n} more" if len(items) > n else ""
    return f"{head} - below: {', '.join(worst[:n])}{more}"
