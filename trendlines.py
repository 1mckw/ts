"""Auto trend lines: ≥3 touch points, body validation, sharp pierce grace.

Touch points = strict pivots on the line plus local extrema (wing 2) whose
wick reaches or nears the line (2%). Sharp up/down bars that break the line are
not touch points. Line construction ignores wick exceed (body-only pierce
rules). Up to one line per side; the line must stay valid to the latest bar
(valid_to_current). Drawing extends to the latest bar; already-broken lines
do not emit 1–10 bar exceed alerts. When multiple anchor pivots fall within
K+6 bars, keep the line with the most touches. Sharp pierce grace unchanged.
"""

from __future__ import annotations

from typing import Any

import ardr

PIVOT_HIGH = 4
PIVOT_LOW = 4
MAX_LOOKBACK = 2000
BEST_TOUCH_LOOKBACK = 200
MAX_RESISTANCE = 1
MAX_SUPPORT = 1
MAX_LINES_PER_PIVOT = 1
MIN_LINE_PIVOTS = 3
PIVOT_LINE_TOL_PCT = 0.002
NEAR_LINE_TOL_PCT = 0.02
LOCAL_EXTREME_WING = 2
MIN_TOUCH_BAR_GAP = 3
NEARBY_PIVOT_LOOKAHEAD = 6  # from pivot K through K+6 bars
SHARP_PIERCE_GRACE_BARS = 2
TREND_EXCEED_MIN_BARS = 1
TREND_EXCEED_MAX_BARS = 10
TREND_EXCEED_BARS = TREND_EXCEED_MAX_BARS  # legacy alias = max window


def find_pivots(candles: list[dict], length: int, highs_only: bool, lows_only: bool):
    highs, lows = [], []
    for i in range(length, len(candles) - length):
        is_high = is_low = True
        for j in range(1, length + 1):
            if candles[i]["high"] <= candles[i - j]["high"] or candles[i]["high"] <= candles[i + j]["high"]:
                is_high = False
            if candles[i]["low"] >= candles[i - j]["low"] or candles[i]["low"] >= candles[i + j]["low"]:
                is_low = False
        if is_high and not lows_only:
            highs.append({"index": i, "time": candles[i]["time"], "price": candles[i]["high"]})
        if is_low and not highs_only:
            lows.append({"index": i, "time": candles[i]["time"], "price": candles[i]["low"]})
    return highs, lows


def line_price(p1: dict, slope: float, idx: int) -> float:
    return p1["price"] + slope * (idx - p1["index"])


def pivots_in_k_plus_n(i: int, j: int, n: int = NEARBY_PIVOT_LOOKAHEAD) -> bool:
    """True if j is within n bars after i (K .. K+n inclusive)."""
    if j < i:
        i, j = j, i
    return j - i <= n


def pick_best_touch_lines_nearby(candidates: list[dict]) -> list[dict]:
    """Within K+6 bars, keep only the candidate line with the most touches."""
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: c["p2"]["index"])
    groups: list[list[dict]] = []
    for cand in ordered:
        p2 = cand["p2"]["index"]
        if not groups:
            groups.append([cand])
            continue
        last = groups[-1]
        if any(pivots_in_k_plus_n(other["p2"]["index"], p2) for other in last):
            last.append(cand)
        else:
            groups.append([cand])
    return [
        max(group, key=lambda c: (c["pivot_count"], c["span"], -c["p1"]["index"]))
        for group in groups
    ]


def body_crosses(candles: list[dict], i: int, lp: float, resistance: bool) -> bool:
    c = candles[i]
    body_hi = max(c["open"], c["close"])
    body_lo = min(c["open"], c["close"])
    if resistance:
        return body_hi > lp
    return body_lo < lp


def wick_touches_line_build(candles: list[dict], i: int, lp: float, resistance: bool) -> bool:
    """Build-time touch: wick reached the line; piercing beyond is OK."""
    c = candles[i]
    if resistance:
        return c["high"] >= lp
    return c["low"] <= lp


def is_sharp_pierce_bar(candles: list[dict], i: int, resistance: bool) -> bool:
    if resistance:
        return ardr.sharp_up(candles, i)
    return ardr.sharp_down(candles, i)


def is_sharp_line_break_touch(candles: list[dict], i: int, lp: float, resistance: bool) -> bool:
    """Sharp up/down bar that breaks the line — cannot count as a touch point."""
    if not is_sharp_pierce_bar(candles, i, resistance):
        return False
    return wick_touches_line_build(candles, i, lp, resistance)


def validate_line_body_segment(
    candles: list[dict],
    p1: dict,
    slope: float,
    start_i: int,
    end_i: int,
    resistance: bool,
) -> bool:
    """True if no disallowed body pierce between start_i and end_i inclusive.

    Wick exceed alone does not invalidate a candidate line during construction.
    """
    if start_i > end_i:
        return True
    in_grace = False
    after_pierce = 0
    for i in range(start_i, end_i + 1):
        lp = line_price(p1, slope, i)
        if not body_crosses(candles, i, lp, resistance):
            in_grace = False
            after_pierce = 0
            continue
        if is_sharp_pierce_bar(candles, i, resistance):
            in_grace = True
            after_pierce = 0
            continue
        if in_grace:
            after_pierce += 1
            if after_pierce > SHARP_PIERCE_GRACE_BARS:
                return False
            continue
        return False
    return True


def find_line_break_index(candles: list[dict], line: dict) -> int | None:
    """First bar where body rules fail, or None if the line is still valid."""
    p1 = line["p1"]
    resistance = line["type"] == "resistance"
    slope = line["slope"]
    in_grace = False
    after_pierce = 0
    for i in range(p1["index"] + 1, len(candles)):
        lp = line_price(p1, slope, i)
        if not body_crosses(candles, i, lp, resistance):
            in_grace = False
            after_pierce = 0
            continue
        if is_sharp_pierce_bar(candles, i, resistance):
            in_grace = True
            after_pierce = 0
            continue
        if in_grace:
            after_pierce += 1
            if after_pierce > SHARP_PIERCE_GRACE_BARS:
                return i
            continue
        return i
    return None


def wick_near_line(candles: list[dict], i: int, lp: float, resistance: bool) -> bool:
    tol = max(abs(lp) * NEAR_LINE_TOL_PCT, 1e-9)
    wick = candles[i]["high"] if resistance else candles[i]["low"]
    return abs(wick - lp) <= tol


def wick_qualifies_touch_build(
    candles: list[dict], i: int, lp: float, resistance: bool
) -> bool:
    """Build-time: wick reached/exceeded the line, or wick within NEAR_LINE_TOL_PCT."""
    return wick_touches_line_build(candles, i, lp, resistance) or wick_near_line(
        candles, i, lp, resistance
    )


def pivot_on_line(p: dict, lp: float) -> bool:
    tol = max(abs(lp) * PIVOT_LINE_TOL_PCT, 1e-9)
    return abs(p["price"] - lp) <= tol


def is_local_extreme(candles: list[dict], i: int, resistance: bool) -> bool:
    """Looser local high/low (wing=2) for less obvious swing points."""
    wing = LOCAL_EXTREME_WING
    if resistance:
        h = candles[i]["high"]
        for j in range(1, wing + 1):
            if i - j < 0 or i + j >= len(candles):
                return False
            if candles[i - j]["high"] > h or candles[i + j]["high"] > h:
                return False
        return True
    lo = candles[i]["low"]
    for j in range(1, wing + 1):
        if i - j < 0 or i + j >= len(candles):
            return False
        if candles[i - j]["low"] < lo or candles[i + j]["low"] < lo:
            return False
    return True


def count_line_touch_points(
    candles: list[dict],
    p1: dict,
    slope: float,
    start_i: int,
    end_i: int,
    resistance: bool,
    pts: list[dict] | None = None,
    pt_lo: int = 0,
    pt_hi: int = -1,
) -> int:
    """Touch points: strict pivots on line + local extrema with wick on/near line (2%)."""
    if start_i > end_i:
        return 0
    on_line_pivot_idx = set()
    if pts is not None and pt_hi >= pt_lo:
        for k in range(pt_lo, pt_hi + 1):
            idx = pts[k]["index"]
            lp = line_price(p1, slope, idx)
            if pivot_on_line(pts[k], lp):
                on_line_pivot_idx.add(idx)

    count = 0
    last_touch_i = -10**9
    for i in range(start_i, end_i + 1):
        lp = line_price(p1, slope, i)
        if is_sharp_line_break_touch(candles, i, lp, resistance):
            continue
        if i in on_line_pivot_idx:
            qualifies = True
        elif is_local_extreme(candles, i, resistance) and wick_qualifies_touch_build(
            candles, i, lp, resistance
        ):
            qualifies = True
        else:
            qualifies = False
        if not qualifies:
            continue
        if i - last_touch_i < MIN_TOUCH_BAR_GAP:
            continue
        count += 1
        last_touch_i = i
    return count


def count_line_pivots(
    candles: list[dict],
    pts: list[dict],
    a: int,
    c: int,
    p1: dict,
    slope: float,
    resistance: bool,
) -> int:
    return count_line_touch_points(
        candles,
        p1,
        slope,
        pts[a]["index"],
        pts[c]["index"],
        resistance,
        pts,
        a,
        c,
    )


def valid_between_pivots(candles: list[dict], p1: dict, p2: dict, resistance: bool) -> bool:
    if p2["index"] <= p1["index"]:
        return False
    slope = (p2["price"] - p1["price"]) / (p2["index"] - p1["index"])
    return validate_line_body_segment(
        candles, p1, slope, p1["index"] + 1, p2["index"] - 1, resistance
    )


def valid_to_current(candles: list[dict], p1: dict, p2: dict, resistance: bool) -> bool:
    slope = (p2["price"] - p1["price"]) / (p2["index"] - p1["index"])
    return validate_line_body_segment(
        candles, p1, slope, p2["index"] + 1, len(candles) - 1, resistance
    )


def build_auto_trend_lines(candles: list[dict]) -> list[dict]:
    start_idx = max(0, len(candles) - MAX_LOOKBACK)
    slice_c = candles[start_idx:]
    offset = start_idx
    piv_high, _ = find_pivots(slice_c, PIVOT_HIGH, True, False)
    _, piv_low = find_pivots(slice_c, PIVOT_LOW, False, True)
    piv_high = [{**p, "index": p["index"] + offset} for p in piv_high]
    piv_low = [{**p, "index": p["index"] + offset} for p in piv_low]

    def collect(pts: list[dict], resistance: bool) -> list[dict]:
        candidates = []
        n_pts = len(pts)
        for a in range(n_pts):
            from_a: list[dict] = []
            for c in range(a + 1, n_pts):
                p1, p3 = pts[a], pts[c]
                if resistance and p3["price"] >= p1["price"]:
                    continue
                if not resistance and p3["price"] <= p1["price"]:
                    continue
                slope = (p3["price"] - p1["price"]) / (p3["index"] - p1["index"])
                touch_count = count_line_pivots(candles, pts, a, c, p1, slope, resistance)
                if touch_count < MIN_LINE_PIVOTS:
                    continue
                if not valid_between_pivots(candles, p1, p3, resistance):
                    continue
                passes_current = valid_to_current(candles, p1, p3, resistance)
                from_a.append(
                    {
                        "type": "resistance" if resistance else "support",
                        "p1": p1,
                        "p2": p3,
                        "slope": slope,
                        "span": p3["index"] - p1["index"],
                        "pivot_count": touch_count,
                        "valid_to_current": passes_current,
                    }
                )
            from_a = pick_best_touch_lines_nearby(from_a)
            from_a.sort(key=lambda c: (-c["pivot_count"], -c["span"], c["p2"]["index"]))
            candidates.extend(from_a[:MAX_LINES_PER_PIVOT])
        candidates.sort(key=lambda c: (-c["pivot_count"], -c["span"], c["p1"]["index"]))
        picked, used = [], set()
        limit = MAX_RESISTANCE if resistance else MAX_SUPPORT

        def try_pick(require_current: bool, max_add: int) -> None:
            added = 0
            for c in candidates:
                if len(picked) >= limit or added >= max_add:
                    return
                if c["p1"]["index"] in used:
                    continue
                if require_current and not c.get("valid_to_current"):
                    continue
                picked.append(c)
                used.add(c["p1"]["index"])
                added += 1

        try_pick(require_current=True, max_add=limit)
        return picked

    return collect(piv_high, True) + collect(piv_low, False)


def build_best_touch_line(candles: list[dict]) -> dict | None:
    """Single max-touch line in the latest 200 daily bars (broken OK; drawn blue)."""
    start_idx = max(0, len(candles) - BEST_TOUCH_LOOKBACK)
    slice_c = candles[start_idx:]
    offset = start_idx
    piv_high, _ = find_pivots(slice_c, PIVOT_HIGH, True, False)
    _, piv_low = find_pivots(slice_c, PIVOT_LOW, False, True)
    piv_high = [{**p, "index": p["index"] + offset} for p in piv_high]
    piv_low = [{**p, "index": p["index"] + offset} for p in piv_low]

    candidates: list[dict] = []
    for resistance, pts in ((True, piv_high), (False, piv_low)):
        n_pts = len(pts)
        for a in range(n_pts):
            for c in range(a + 1, n_pts):
                p1, p3 = pts[a], pts[c]
                if resistance and p3["price"] >= p1["price"]:
                    continue
                if not resistance and p3["price"] <= p1["price"]:
                    continue
                slope = (p3["price"] - p1["price"]) / (p3["index"] - p1["index"])
                touch_count = count_line_pivots(candles, pts, a, c, p1, slope, resistance)
                if touch_count < MIN_LINE_PIVOTS:
                    continue
                if not valid_between_pivots(candles, p1, p3, resistance):
                    continue
                candidates.append(
                    {
                        "type": "resistance" if resistance else "support",
                        "p1": p1,
                        "p2": p3,
                        "slope": slope,
                        "span": p3["index"] - p1["index"],
                        "pivot_count": touch_count,
                    }
                )

    if not candidates:
        return None
    return max(candidates, key=lambda c: (c["pivot_count"], c["span"], -c["p1"]["index"]))


def check_line_invalidation(candles: list[dict], line: dict) -> bool:
    return find_line_break_index(candles, line) is not None


def find_trend_exceed(
    candles: list[dict],
    line: dict,
    min_n: int = TREND_EXCEED_MIN_BARS,
    max_n: int = TREND_EXCEED_MAX_BARS,
) -> dict | None:
    """Return if the latest consecutive exceed streak is within [min_n, max_n]."""
    if len(candles) < min_n:
        return None
    if find_line_break_index(candles, line) is not None:
        return None
    p1 = line["p1"]
    slope = line["slope"]
    resistance = line["type"] == "resistance"
    line_start = max(line["p2"]["index"], line["p1"]["index"])
    streak = 0
    for i in range(len(candles) - 1, -1, -1):
        if i <= line_start:
            break
        lp = line_price(p1, slope, i)
        if not body_crosses(candles, i, lp, resistance):
            break
        streak += 1
        if streak >= max_n:
            break
    if streak < min_n:
        return None
    last_i = len(candles) - 1
    lp = line_price(p1, slope, last_i)
    c = candles[last_i]
    return {
        "time": c["time"],
        "price": lp,
        "index": last_i,
        "close": c["close"],
        "bars": streak,
    }


def find_trend_touch(candles: list[dict], line: dict) -> dict | None:
    start = max(line["p2"]["index"], line["p1"]["index"]) + 1
    break_i = find_line_break_index(candles, line)
    end = (break_i - 1) if break_i is not None else len(candles) - 1
    for i in range(start, end + 1):
        lp = line_price(line["p1"], line["slope"], i)
        c = candles[i]
        touched = c["high"] >= lp if line["type"] == "resistance" else c["low"] <= lp
        if touched:
            return {"time": c["time"], "price": lp, "index": i, "close": c["close"]}
    return None


def line_end_at_break(candles: list[dict], line: dict) -> tuple[int, float]:
    """Return (end_time, end_price) for drawing; always extend to the latest bar."""
    last_i = len(candles) - 1
    end_i = max(last_i, line["p2"]["index"])
    lp = line_price(line["p1"], line["slope"], end_i)
    return int(candles[end_i]["time"]), float(lp)
