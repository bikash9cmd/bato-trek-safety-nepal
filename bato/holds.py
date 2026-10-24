"""The storm-hold rule for high passes, and how it scores against what actually fell.

Rule: take the forecast's 6-hourly precipitation at the pass's grid cell, find the wettest
24-hour window in the next 48 hours, and compare it with two thresholds. At 4,600-5,500 m in
the trekking seasons nearly all of it falls as snow.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import config

CLEAR, WATCH, HOLD = "CLEAR", "WATCH", "HOLD"


def wettest_24h(series_6h: list[float], lookahead_h: int = config.HOLD_LOOKAHEAD_H) -> tuple[float, int]:
    """(mm, index of the first 6-hour block of that window)."""
    n = lookahead_h // 6
    vals = list(series_6h[:n])
    if len(vals) < 4:
        return (round(sum(vals), 2), 0)
    sums = [sum(vals[i : i + 4]) for i in range(len(vals) - 3)]
    i = max(range(len(sums)), key=sums.__getitem__)
    return (round(sums[i], 2), i)


def level_for(mm_24h: float, hold_mm: float = config.HOLD_MM_24H, watch_mm: float = config.WATCH_MM_24H) -> str:
    if mm_24h >= hold_mm:
        return HOLD
    if mm_24h >= watch_mm:
        return WATCH
    return CLEAR


def status(series_6h: list[float], cycle_utc: datetime) -> dict:
    mm, i = wettest_24h(series_6h)
    start = (cycle_utc + timedelta(hours=6 * i)).astimezone(config.NPT)
    end = start + timedelta(hours=24)
    return {
        "level": level_for(mm),
        "mm_24h": mm,
        "from_day": start.date().isoformat(),
        "until_day": end.date().isoformat(),
        "cycle": cycle_utc.strftime("%Y-%m-%dT%HZ"),
    }


# --- scoring -----------------------------------------------------------------------------
def contingency(pairs: list[tuple[bool, bool]]) -> dict:
    """pairs of (warned, event happened) -> hits, misses, false alarms and the usual scores."""
    hits = sum(w and e for w, e in pairs)
    misses = sum((not w) and e for w, e in pairs)
    fa = sum(w and (not e) for w, e in pairs)
    cn = sum((not w) and (not e) for w, e in pairs)
    return {
        "hits": hits, "misses": misses, "false_alarms": fa, "correct_negatives": cn,
        "pod": round(hits / (hits + misses), 3) if hits + misses else None,
        "far": round(fa / (hits + fa), 3) if hits + fa else None,
    }
