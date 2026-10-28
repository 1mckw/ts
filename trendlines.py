"""Four-point auto trend lines, expanding windows, Creek/Ice, parallel channels.

Touch rules
  ≥4 merged touch points. Support slope ≥ 0; resistance slope ≤ 0.
  Anchor pivots: wing 3 left/right, two anchors ≥5 bars apart.
  Touch tolerance 0.45% of line price. Adjacent ≤5 bars merge to 1.
  Adjacent merged gap >120 bars: keep the longest consecutive segment.
  ≥1 touch in the latest 60 bars or the line is invalid.
  Draw from the first touch to the latest bar of the search window.

Breaks (counted after the first touch)
  Full body cross: at most 3 bars. Wick-only cross: at most 5 bars.
  Exceeding either cap invalidates the line.

Windows
  Try 200 → 300 → 400 → 500 → 600 → 800 → 1000; enlarge only if no line.
  At 1000 bars with slope drift < 1.5%: Creek (resistance) / Ice (support),
  redrawn as a true horizontal. Chart display is a fixed 600 bars.

Channel
  If only one side exists, search the opposite side with the same slope.
  Opposite side also needs 4 points and the same body/wick caps.

Signals (last 5 bars)
  Break and touch are mutually exclusive: a break suppresses the touch.
"""

from __future__ import annotations

from typing import Any

PIVOT_WING = 3
MIN_ANCHOR_GAP = 5
MIN_TOUCH_POINTS = 4
TOUCH_TOL_PCT = 0.0045
MERGE_TOUCH_BARS = 5
MAX_TOUCH_GAP = 120
RECENT_TOUCH_BARS = 60
BODY_BREAK_MAX = 3
WICK_BREAK_MAX = 5
EXPAND_WINDOWS = (200, 300, 400, 500, 600, 800, 1000)
EXPAND_WINDOW = EXPAND_WINDOWS[0]
HORIZONTAL_DRIFT_PCT = 0.015
CHART_BARS = 600
TREND_SIGNAL_BARS = 5
MAX_RESISTANCE = 1
MAX_SUPPORT = 1
TREND_EXCEED_MIN_BARS = 1
TREND_EXCEED_MAX_BARS = TREND_SIGNAL_BARS
TREND_EXCEED_BARS = TREND_EXCEED_MAX_BARS


def find_pivots(candles: list[dict], length: int, highs_only: bool, lows_only: bool):
    highs, lows = [], []
    n = len(candles)
    for i in range(length, n - length):
        is_high = is_low = True
        hi = candles[i]["high"]
        lo = candles[i]["low"]
        for j in range(1, length + 1):
            if hi <= candles[i - j]["high"] or hi <= candles[i + j]["high"]:
                is_high = False
            if lo >= candles[i - j]["low"] or lo >= candles[i + j]["low"]:
                is_low = False
            if not is_high and not is_low:
                break
        if is_high and not lows_only:
            highs.append({"index": i, "time": candles[i]["time"], "price": hi})
        if is_low and not highs_only:
            lows.append({"index": i, "time": candles[i]["time"], "price": lo})
    return highs, lows


def line_price(p1: dict, slope: float, idx: int) -> float:
    return p1["price"] + slope * (idx - p1["index"])


def _tol(lp: float) -> float:
    return max(abs(lp) * TOUCH_TOL_PCT, 1e-9)


def body_hi_lo(c: dict) -> tuple[float, float]:
    return max(c["open"], c["close"]), min(c["open"], c["close"])


def body_full_cross(c: dict, lp: float, resistance: bool) -> bool:
    bhi, blo = body_hi_lo(c)
    if resistance:
        return blo > lp
    return bhi < lp


def wick_only_cross(c: dict, lp: float, resistance: bool) -> bool:
    if body_full_cross(c, lp, resistance):
        return False
    bhi, blo = body_hi_lo(c)
    if resistance:
        return c["high"] > lp and bhi <= lp
    return c["low"] < lp and blo >= lp


def is_touch_bar(c: dict, lp: float, resistance: bool) -> bool:
    wick = c["high"] if resistance else c["low"]
    return abs(wick - lp) <= _tol(lp)


def merge_touch_indices(indices: list[int]) -> list[int]:
    if not indices:
        return []
    clusters: list[list[int]] = [[indices[0]]]
    for i in indices[1:]:
        if i - clusters[-1][-1] <= MERGE_TOUCH_BARS:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    return [cl[0] for cl in clusters]


def longest_touch_segment(indices: list[int]) -> list[int]:
    merged = merge_touch_indices(indices)
    if not merged:
        return []
    segs: list[list[int]] = [[merged[0]]]
    for i in merged[1:]:
        if i - segs[-1][-1] > MAX_TOUCH_GAP:
            segs.append([i])
        else:
            segs[-1].append(i)
    return max(segs, key=lambda s: (len(s), s[-1] - s[0]))


def collect_raw_touches(
    candles: list[dict],
    p1: dict,
    slope: float,
    resistance: bool,
    start_i: int,
    end_i: int,
) -> list[int]:
    hits: list[int] = []
    for i in range(start_i, end_i + 1):
        lp = line_price(p1, slope, i)
        if is_touch_bar(candles[i], lp, resistance):
            hits.append(i)
    return hits


def pierce_counts(
    candles: list[dict],
    p1: dict,
    slope: float,
    resistance: bool,
    start_i: int,
    end_i: int,
) -> tuple[int, int]:
    body_n = wick_n = 0
    for i in range(start_i, end_i + 1):
        lp = line_price(p1, slope, i)
        c = candles[i]
        if body_full_cross(c, lp, resistance):
            body_n += 1
        elif wick_only_cross(c, lp, resistance):
            wick_n += 1
    return body_n, wick_n


def _pack_anchors(anchors: list[dict]) -> list[dict]:
    packed: list[dict] = []
    seen: set[int] = set()
    for a in anchors:
        t = int(a["time"])
        if t in seen:
            continue
        seen.add(t)
        packed.append(
            {
                "index": int(a["index"]),
                "time": t,
                "price": float(a["price"]),
            }
        )
    return packed


def _pack_touch_points(
    candles: list[dict], indices: list[int], p1: dict, slope: float
) -> list[dict]:
    packed: list[dict] = []
    seen: set[int] = set()
    for i in indices:
        t = int(candles[i]["time"])
        if t in seen:
            continue
        seen.add(t)
        packed.append(
            {
                "index": int(i),
                "time": t,
                "price": float(line_price(p1, slope, i)),
            }
        )
    return packed


def evaluate_candidate(
    candles: list[dict],
    p_anchor: dict,
    slope: float,
    resistance: bool,
    win_lo: int,
    last_i: int,
    search_lookback: int,
    anchors: list[dict] | None = None,
) -> dict | None:
    raw = collect_raw_touches(candles, p_anchor, slope, resistance, win_lo, last_i)
    seg = longest_touch_segment(raw)
    if len(seg) < MIN_TOUCH_POINTS:
        return None
    recent_lo = last_i - (RECENT_TOUCH_BARS - 1)
    if not any(i >= recent_lo for i in seg):
        return None
    first_i = seg[0]
    last_touch = seg[-1]
    body_n, wick_n = pierce_counts(
        candles, p_anchor, slope, resistance, first_i + 1, last_i
    )
    if body_n > BODY_BREAK_MAX or wick_n > WICK_BREAK_MAX:
        return None
    p1 = dict(p_anchor)
    p1["index"] = first_i
    p1["time"] = candles[first_i]["time"]
    p1["price"] = line_price(p_anchor, slope, first_i)
    p2 = {
        "index": last_touch,
        "time": candles[last_touch]["time"],
        "price": line_price(p_anchor, slope, last_touch),
    }
    span = last_touch - first_i
    label = "resistance" if resistance else "support"
    horizontal = False
    out_slope = slope
    if search_lookback >= EXPAND_WINDOWS[-1] and p1["price"]:
        drift = abs(slope) * span / abs(p1["price"])
        if drift < HORIZONTAL_DRIFT_PCT:
            level = sum(line_price(p_anchor, slope, i) for i in seg) / len(seg)
            out_slope = 0.0
            p1 = {**p1, "price": level}
            p2 = {**p2, "price": level}
            label = "creek" if resistance else "ice"
            horizontal = True
    return {
        "type": "resistance" if resistance else "support",
        "label": label,
        "p1": p1,
        "p2": p2,
        "slope": out_slope,
        "span": span,
        "pivot_count": len(seg),
        "valid_to_current": True,
        "search_lookback": search_lookback,
        "is_channel": False,
        "horizontal": horizontal,
        "body_breaks": body_n,
        "wick_breaks": wick_n,
        "anchors": _pack_anchors(anchors or [p_anchor]),
        "touches": _pack_touch_points(candles, seg, p1, out_slope),
    }


def _score(line: dict) -> tuple:
    return (line["pivot_count"], line["span"], -line["p1"]["index"])


def _collect_side_lines(
    candles: list[dict],
    pts: list[dict],
    resistance: bool,
    win_lo: int,
    search_lookback: int,
) -> list[dict]:
    last_i = len(candles) - 1
    n_pts = len(pts)
    best: dict | None = None
    for a in range(n_pts):
        for c in range(a + 1, n_pts):
            p1, p3 = pts[a], pts[c]
            if p3["index"] - p1["index"] < MIN_ANCHOR_GAP:
                continue
            if resistance and p3["price"] > p1["price"]:
                continue
            if not resistance and p3["price"] < p1["price"]:
                continue
            slope = (p3["price"] - p1["price"]) / (p3["index"] - p1["index"])
            cand = evaluate_candidate(
                candles,
                p1,
                slope,
                resistance,
                win_lo,
                last_i,
                search_lookback,
                anchors=[p1, p3],
            )
            if cand is None:
                continue
            if best is None or _score(cand) > _score(best):
                best = cand
    if best is None:
        return []
    return [best]


def _pivots_in_lookback(candles: list[dict], lookback: int) -> tuple[list[dict], list[dict]]:
    start_idx = max(0, len(candles) - lookback)
    slice_c = candles[start_idx:]
    offset = start_idx
    piv_high, _ = find_pivots(slice_c, PIVOT_WING, True, False)
    _, piv_low = find_pivots(slice_c, PIVOT_WING, False, True)
    piv_high = [{**p, "index": p["index"] + offset} for p in piv_high]
    piv_low = [{**p, "index": p["index"] + offset} for p in piv_low]
    return piv_high, piv_low


def find_parallel_channel(
    candles: list[dict],
    src: dict,
    lookback: int,
) -> dict | None:
    resistance = src["type"] != "resistance"
    win_lo = max(0, len(candles) - lookback)
    last_i = len(candles) - 1
    slope = float(src["slope"])
    piv_high, piv_low = _pivots_in_lookback(candles, lookback)
    pts = piv_high if resistance else piv_low
    src_p1 = src["p1"]
    src_slope = src["slope"]
    best: dict | None = None
    for p in pts:
        src_lp = line_price(src_p1, src_slope, p["index"])
        if resistance and p["price"] < src_lp:
            continue
        if not resistance and p["price"] > src_lp:
            continue
        cand = evaluate_candidate(
            candles, p, slope, resistance, win_lo, last_i, lookback, anchors=[p]
        )
        if cand is None:
            continue
        if best is None or _score(cand) > _score(best):
            best = cand
    if best is None:
        return None
    best["is_channel"] = True
    return best


def build_auto_trend_lines_expanding(
    candles: list[dict],
    window: int = EXPAND_WINDOW,
) -> list[dict]:
    """Try expanding windows per side; attach a parallel channel if only one side."""
    if not candles:
        return []
    n = len(candles)
    windows = list(EXPAND_WINDOWS)
    if window and window not in windows:
        windows = sorted(set(windows + [max(1, int(window))]))
    found: dict[bool, dict] = {}
    used_lb: dict[bool, int] = {}
    for resistance in (True, False):
        for lookback in windows:
            actual = min(lookback, n)
            win_lo = n - actual
            piv_high, piv_low = _pivots_in_lookback(candles, actual)
            pts = piv_high if resistance else piv_low
            side = _collect_side_lines(
                candles, pts, resistance, win_lo, actual
            )
            if side:
                found[resistance] = side[0]
                used_lb[resistance] = actual
                break
            if actual >= n:
                break
    out: list[dict] = []
    for resistance, line in found.items():
        item = dict(line)
        item["search_lookback"] = used_lb[resistance]
        out.append(item)
    if len(out) == 1:
        src = out[0]
        ch = find_parallel_channel(candles, src, int(src["search_lookback"]))
        if ch is not None:
            out.append(ch)
    order = {"resistance": 0, "support": 1}
    out.sort(key=lambda x: (order.get(x["type"], 9), -x["pivot_count"]))
    return out[: MAX_RESISTANCE + MAX_SUPPORT + 1]


def build_auto_trend_lines(
    candles: list[dict],
    lookback: int | None = None,
) -> list[dict]:
    if lookback is None:
        return build_auto_trend_lines_expanding(candles)
    piv_high, piv_low = _pivots_in_lookback(candles, lookback)
    win_lo = max(0, len(candles) - lookback)
    return _collect_side_lines(
        candles, piv_high, True, win_lo, lookback
    ) + _collect_side_lines(candles, piv_low, False, win_lo, lookback)


def check_line_invalidation(candles: list[dict], line: dict) -> bool:
    p1 = line["p1"]
    resistance = line["type"] == "resistance"
    slope = line["slope"]
    start = p1["index"] + 1
    last_i = len(candles) - 1
    if start > last_i:
        return False
    body_n, wick_n = pierce_counts(candles, p1, slope, resistance, start, last_i)
    return body_n > BODY_BREAK_MAX or wick_n > WICK_BREAK_MAX


def _signal_range(n: int, bars: int = TREND_SIGNAL_BARS) -> tuple[int, int]:
    last = n - 1
    lo = max(0, last - (bars - 1))
    return lo, last


def find_trend_exceed(
    candles: list[dict],
    line: dict,
    min_n: int = TREND_EXCEED_MIN_BARS,
    max_n: int = TREND_EXCEED_MAX_BARS,
) -> dict | None:
    """Break in the latest TREND_SIGNAL_BARS if the historical cap is not already blown."""
    if not candles:
        return None
    p1 = line["p1"]
    slope = line["slope"]
    resistance = line["type"] == "resistance"
    last_i = len(candles) - 1
    hist_end = max(p1["index"] + 1, last_i - TREND_SIGNAL_BARS)
    if hist_end >= p1["index"] + 1:
        body_n, wick_n = pierce_counts(
            candles, p1, slope, resistance, p1["index"] + 1, hist_end
        )
        if body_n > BODY_BREAK_MAX or wick_n > WICK_BREAK_MAX:
            return None
    lo, last = _signal_range(len(candles))
    lo = max(lo, p1["index"] + 1)
    bars = 0
    last_break_i = None
    for i in range(lo, last + 1):
        lp = line_price(p1, slope, i)
        c = candles[i]
        if body_full_cross(c, lp, resistance) or wick_only_cross(c, lp, resistance):
            bars += 1
            last_break_i = i
    if bars < min_n or bars > max_n:
        return None
    i = last_break_i if last_break_i is not None else last
    lp = line_price(p1, slope, i)
    c = candles[i]
    return {
        "time": c["time"],
        "price": lp,
        "index": i,
        "close": c["close"],
        "bars": bars,
    }


def find_trend_touch(candles: list[dict], line: dict) -> dict | None:
    """Touch in the latest TREND_SIGNAL_BARS; caller skips if a break is present."""
    if not candles:
        return None
    p1 = line["p1"]
    slope = line["slope"]
    resistance = line["type"] == "resistance"
    lo, last = _signal_range(len(candles))
    lo = max(lo, p1["index"])
    for i in range(last, lo - 1, -1):
        lp = line_price(p1, slope, i)
        if is_touch_bar(candles[i], lp, resistance):
            c = candles[i]
            return {"time": c["time"], "price": lp, "index": i, "close": c["close"]}
    return None


def line_end_at_break(candles: list[dict], line: dict) -> tuple[int, float]:
    last_i = len(candles) - 1
    end_i = max(last_i, line["p2"]["index"])
    lp = line_price(line["p1"], line["slope"], end_i)
    return int(candles[end_i]["time"]), float(lp)
