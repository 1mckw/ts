#!/usr/bin/env python3
"""Taiwan TWSE listed + TPEx OTC stocks — AR/AD + trend-line scanner."""

from __future__ import annotations

import argparse
import html
import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import ardr
import trendlines as tl
from universe import GROUP_ORDER, build_scan_jobs, group_label
from watchlist import WATCHLIST_NAME, WATCHLIST_SYMBOLS

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "signals")
STATIC_DIR = os.path.join(ROOT, "static")
CHART_PACKS_PATH = os.path.join(OUT_DIR, "chart-packs.json")

TIMEFRAMES: dict[str, dict[str, Any]] = {
    "1d": {
        "interval": "1d",
        "range": "5y",
        "bars": 1000,
        "chart_bars": 600,
        "touch_window": 20,
        "late_touch_window": 20,
        "late_touch_lookback": 10,
        "late_touch_min_display": 60,
        "near_miss_min_bars": 60,
        "near_miss_lookback": 200,
        "label": "1D",
    },
}
TIMEFRAME_ORDER = ("1d",)
TF_ORDER = {tf: i for i, tf in enumerate(TIMEFRAME_ORDER)}

LOOKBACK = ardr.LOOKBACK
VOL_LEN = ardr.VOL_LEN
DROP_PCT = ardr.DROP_PCT
MIN_STREAK = ardr.MIN_STREAK
VOL_MULT = ardr.VOL_MULT
TOUCH_WINDOW_BARS = ardr.TOUCH_WINDOW_BARS
FRESH_BARS = ardr.FRESH_BARS

detect_signals = ardr.detect_signals
collect_late_ar_dr_touches = ardr.collect_late_ar_dr_touches
collect_late_ar_dr_touches_in_lookback = ardr.collect_late_ar_dr_touches_in_lookback
collect_late_ar_dr_near_misses = ardr.collect_late_ar_dr_near_misses
LATE_TOUCH_LOOKBACK_BARS = ardr.LATE_TOUCH_LOOKBACK_BARS
LATE_TOUCH_MIN_BARS = ardr.LATE_TOUCH_MIN_BARS
LATE_TOUCH_MIN_DISPLAY_BARS = ardr.LATE_TOUCH_MIN_DISPLAY_BARS
NEAR_MISS_MIN_BARS = ardr.NEAR_MISS_MIN_BARS
NEAR_MISS_LOOKBACK_BARS = ardr.NEAR_MISS_LOOKBACK_BARS
fresh_range = ardr.fresh_range

TREND_EXCEED_MIN_BARS = tl.TREND_EXCEED_MIN_BARS
TREND_EXCEED_MAX_BARS = tl.TREND_EXCEED_MAX_BARS
TREND_EXCEED_BARS = tl.TREND_EXCEED_BARS
TREND_SIGNAL_BARS = tl.TREND_SIGNAL_BARS
CHART_BARS = tl.CHART_BARS
build_auto_trend_lines = tl.build_auto_trend_lines
build_auto_trend_lines_expanding = tl.build_auto_trend_lines_expanding
check_line_invalidation = tl.check_line_invalidation
find_trend_touch = tl.find_trend_touch
find_trend_exceed = tl.find_trend_exceed
find_trend_reclaim = tl.find_trend_reclaim
line_end_at_break = tl.line_end_at_break

UA = {"User-Agent": "Mozilla/5.0 (compatible; TW-Alerts/1.0)"}
KIND_ORDER = {
    "trend_exceed": 0,
    "trend_reclaim": 1,
    "ar_dr_touch": 2,
    "ar_dr_late_touch": 3,
    "ar_dr_near": 4,
    "trend_touch": 5,
}


def trend_tag(h: dict) -> tuple[str, str]:
    kind = str(h.get("line_label") or "")
    if kind == "creek":
        return "creek", "Creek"
    if kind == "ice":
        return "ice", "Ice"
    txt = "阻力" if h.get("type") == "resistance" else "支撐"
    if h.get("is_channel"):
        txt = "通道" + txt
    cls = "resist" if h.get("type") == "resistance" else "support"
    return cls, txt


def normalize_signal_type(sig_type: str | None) -> str:
    if sig_type == "DR":
        return "AD"
    return sig_type or ""


def normalize_hit(hit: dict) -> dict:
    out = dict(hit)
    out["type"] = normalize_signal_type(out.get("type"))
    label = str(out.get("label") or "")
    if label.startswith("DR "):
        out["label"] = "AD" + label[2:]
    return out


def compact_num(x: Any, nd: int = 4) -> int | float:
    v = round(float(x), nd)
    iv = int(v)
    return iv if v == iv else v


def compact_point(pt: dict) -> dict:
    out: dict[str, Any] = {"time": int(pt["time"]), "price": compact_num(pt["price"])}
    return out


def compact_candles(candles: list[dict], max_bars: int) -> list[list]:
    rows = candles[-max_bars:] if len(candles) > max_bars else candles
    out: list[list] = []
    for c in rows:
        out.append(
            [
                int(c["time"]),
                compact_num(c["open"]),
                compact_num(c["high"]),
                compact_num(c["low"]),
                compact_num(c["close"]),
            ]
        )
    return out


def compact_rays(rays: list[dict]) -> list[dict]:
    out: list[dict] = []
    for ray in rays:
        segs = []
        for seg in ray.get("segments") or []:
            item = {
                "t0": int(seg["t0"]),
                "t1": int(seg["t1"]),
                "price": compact_num(seg["price"]),
                "active": bool(seg.get("active")),
            }
            if seg.get("side"):
                item["side"] = seg["side"]
            segs.append(item)
        packed = {
            "type": normalize_signal_type(ray.get("type")),
            "active": bool(ray.get("active")),
            "segments": segs,
        }
        if ray.get("time") is not None:
            packed["time"] = int(ray["time"])
        out.append(packed)
    return out


def compact_trend_lines(lines: list[dict]) -> list[dict]:
    out: list[dict] = []
    for line in lines:
        item = {
            "type": line["type"],
            "p1": compact_point(line["p1"]),
            "p2": compact_point(line["p2"]),
            "endTime": int(line["endTime"]),
            "endPrice": compact_num(line["endPrice"]),
            "invalidated": bool(line.get("invalidated")),
            "pivot_count": int(line.get("pivot_count") or 0),
        }
        touches = [compact_point(t) for t in (line.get("touches") or []) if t.get("time") is not None]
        if touches:
            item["touches"] = touches
        if line.get("search_lookback") is not None:
            item["search_lookback"] = int(line["search_lookback"])
        if line.get("label"):
            item["label"] = str(line["label"])
        if line.get("is_channel"):
            item["is_channel"] = True
        if line.get("horizontal"):
            item["horizontal"] = True
        out.append(item)
    return out


def normalize_chart_pack(pack: dict | None) -> dict | None:
    if not pack:
        return pack
    candles = pack.get("candles") or []
    if candles and isinstance(candles[0], dict):
        packed_candles = compact_candles(candles, CHART_BARS)
    else:
        packed_candles = candles[-CHART_BARS:] if len(candles) > CHART_BARS else candles
    trend = compact_trend_lines(pack.get("trend_lines") or [])
    expand = compact_trend_lines(pack.get("trend_lines_expand") or [])
    out: dict[str, Any] = {
        "candles": packed_candles,
        "rays": compact_rays(pack.get("rays") or []),
        "trend_lines": trend,
    }
    if expand and expand != trend:
        out["trend_lines_expand"] = expand
    return out


def normalize_result_events(result: dict) -> dict:
    out = dict(result)
    out["events"] = [normalize_hit(ev) for ev in out.get("events") or []]
    return out


def trend_tag(h: dict) -> tuple[str, str]:
    kind = str(h.get("line_label") or h.get("type") or "")
    if kind == "creek":
        return "creek", "Creek"
    if kind == "ice":
        return "ice", "Ice"
    if h.get("type") == "resistance":
        txt = "阻力"
    else:
        txt = "支撐"
    if h.get("is_channel"):
        txt = "通道" + txt
    cls = "resist" if h.get("type") == "resistance" else "support"
    return cls, txt


def chart_key(group: str, symbol: str, timeframe: str) -> str:
    return f"{group}|{symbol}|{timeframe}"


def http_get_json(url: str, timeout: int = 45) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def yahoo_range(timeframe: str, bars: int) -> str:
    cfg = TIMEFRAMES[timeframe]
    if cfg.get("range"):
        return str(cfg["range"])
    if bars <= 500:
        return "2y"
    if bars <= 2000:
        return "5y"
    if bars <= 3000:
        return "10y"
    return "max"


def fetch_yahoo(symbol: str, timeframe: str = "1d", bars: int | None = None) -> list[dict]:
    cfg = TIMEFRAMES[timeframe]
    iv = cfg["interval"]
    want = bars if bars is not None else int(cfg["bars"])
    yrange = yahoo_range(timeframe, want)
    hosts = [
        "https://query1.finance.yahoo.com",
        "https://query2.finance.yahoo.com",
    ]
    last_err: Exception | None = None
    for host in hosts:
        url = (
            f"{host}/v8/finance/chart/"
            + urllib.parse.quote(symbol, safe="=-.^")
            + f"?interval={iv}&range={yrange}&includePrePost=false"
        )
        try:
            payload = http_get_json(url)
            result = (payload.get("chart") or {}).get("result") or []
            if not result:
                continue
            r0 = result[0]
            ts = r0.get("timestamp") or []
            q0 = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
            out = []
            for i, t in enumerate(ts):
                o, h, l, c = (
                    (q0.get("open") or [None])[i],
                    (q0.get("high") or [None])[i],
                    (q0.get("low") or [None])[i],
                    (q0.get("close") or [None])[i],
                )
                v = (q0.get("volume") or [0])[i] or 0
                if None in (o, h, l, c):
                    continue
                out.append(
                    {
                        "time": int(t),
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                        "volume": float(v),
                    }
                )
            if out:
                return out[-want:] if len(out) > want else out
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    if last_err:
        raise last_err
    return []


def with_retries(fn, retries: int = 3, pause: float = 0.8):
    last_err = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(pause * (attempt + 1))
    raise last_err  # type: ignore[misc]


def _trend_hit_extras(line: dict, hit: dict) -> dict:
    if line.get("search_lookback") is not None:
        hit["search_lookback"] = int(line["search_lookback"])
    if line.get("pivot_count") is not None:
        hit["pivot_count"] = int(line["pivot_count"])
    if line.get("label"):
        hit["line_label"] = str(line["label"])
    if line.get("is_channel"):
        hit["is_channel"] = True
    return hit


def collect_trend_touches(candles: list[dict], lines: list[dict]) -> list[dict]:
    if not candles:
        return []
    hits = []
    for line in lines:
        if find_trend_reclaim(candles, line) or find_trend_exceed(candles, line):
            continue
        touch = find_trend_touch(candles, line)
        if not touch:
            continue
        kind = line.get("label") or line["type"]
        if kind == "creek":
            label = "Creek 觸碰"
        elif kind == "ice":
            label = "Ice 觸碰"
        elif line["type"] == "resistance":
            label = "阻力趨勢線觸碰"
        else:
            label = "支撐趨勢線觸碰"
        if line.get("is_channel"):
            label = "通道" + label
        hit = {
            "kind": "trend_touch",
            "label": label,
            "type": line["type"],
            "time": touch["time"],
            "index": touch["index"],
            "level": touch["price"],
            "close": touch["close"],
        }
        hits.append(_trend_hit_extras(line, hit))
    return hits


def collect_trend_exceeds(candles: list[dict], lines: list[dict]) -> list[dict]:
    hits = []
    for line in lines:
        if find_trend_reclaim(candles, line):
            continue
        exc = find_trend_exceed(candles, line)
        if not exc:
            continue
        kind = line.get("label") or line["type"]
        if kind == "creek":
            label = "Creek 突破"
        elif kind == "ice":
            label = "Ice 突破"
        elif line["type"] == "resistance":
            label = "阻力趨勢線超出"
        else:
            label = "支撐趨勢線超出"
        if line.get("is_channel"):
            label = "通道" + label
        hit = {
            "kind": "trend_exceed",
            "label": label,
            "type": line["type"],
            "time": exc["time"],
            "index": exc["index"],
            "level": exc["price"],
            "close": exc["close"],
            "exceed_bars": exc["bars"],
        }
        hits.append(_trend_hit_extras(line, hit))
    return hits


def collect_trend_reclaims(candles: list[dict], lines: list[dict]) -> list[dict]:
    hits = []
    for line in lines:
        rec = find_trend_reclaim(candles, line)
        if not rec:
            continue
        kind = line.get("label") or line["type"]
        if kind == "creek":
            label = "Creek 實體超出後站回"
        elif kind == "ice":
            label = "Ice 實體超出後站回"
        elif line["type"] == "resistance":
            label = "阻力趨勢線實體超出後站回"
        else:
            label = "支撐趨勢線實體超出後站回"
        if line.get("is_channel"):
            label = "通道" + label
        hit = {
            "kind": "trend_reclaim",
            "label": label,
            "type": line["type"],
            "time": rec["time"],
            "index": rec["index"],
            "level": rec["price"],
            "close": rec["close"],
            "exceed_bars": rec["bars"],
        }
        hits.append(_trend_hit_extras(line, hit))
    return hits


def serialize_points(line: dict, key: str) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    for a in line.get(key) or []:
        try:
            t = int(a["time"])
            px = compact_num(a["price"])
        except (KeyError, TypeError, ValueError):
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append({"time": t, "price": px})
    return out


def serialize_anchors(line: dict) -> list[dict]:
    return serialize_points(line, "anchors")


def serialize_touches(line: dict) -> list[dict]:
    return serialize_points(line, "touches")


def serialize_trend_lines(candles: list[dict], lines: list[dict]) -> list[dict]:
    trend = []
    for line in lines:
        invalidated = check_line_invalidation(candles, line)
        end_time, end_price = line_end_at_break(candles, line)
        item = {
            "type": line["type"],
            "p1": {"time": int(line["p1"]["time"]), "price": compact_num(line["p1"]["price"])},
            "p2": {"time": int(line["p2"]["time"]), "price": compact_num(line["p2"]["price"])},
            "endTime": int(end_time),
            "endPrice": compact_num(end_price),
            "invalidated": invalidated,
            "pivot_count": int(line.get("pivot_count") or 0),
        }
        touches = serialize_touches(line)
        if touches:
            item["touches"] = touches
        if line.get("search_lookback") is not None:
            item["search_lookback"] = int(line["search_lookback"])
        if line.get("label"):
            item["label"] = str(line["label"])
        if line.get("is_channel"):
            item["is_channel"] = True
        if line.get("horizontal"):
            item["horizontal"] = True
        trend.append(item)
    return trend


def chart_pack_start_index(
    candles: list[dict],
    lines: list[dict],
    chart_bars: int,
) -> int:
    """Fixed last chart_bars (600)."""
    return max(0, len(candles) - chart_bars)


def build_chart_pack(
    candles: list[dict],
    signals: list[dict],
    lines: list[dict],
    chart_bars: int = 600,
    lines_expand: list[dict] | None = None,
) -> dict:
    all_lines = list(lines) + list(lines_expand or [])
    start_idx = chart_pack_start_index(candles, all_lines, chart_bars)
    trimmed = candles[start_idx:]
    if not trimmed:
        return {"candles": [], "rays": [], "trend_lines": []}

    t_min = int(trimmed[0]["time"])
    t_max = int(trimmed[-1]["time"])
    last_time = t_max

    visible_signals = [s for s in signals if t_min <= int(s["time"]) <= t_max]
    rays = []
    for sig in visible_signals:
        ray = ardr.signal_to_chart_ray(sig, candles, last_time)
        segs = []
        for seg in ray.get("segments") or []:
            t0, t1 = int(seg["t0"]), int(seg["t1"])
            clip0, clip1 = max(t0, t_min), min(t1, t_max)
            if clip1 > clip0:
                segs.append(
                {
                    "t0": clip0,
                    "t1": clip1,
                    "price": compact_num(seg["price"]),
                    "active": bool(seg.get("active")),
                    **({"side": seg["side"]} if seg.get("side") else {}),
                }
            )
        if segs:
            packed_ray = {
                "type": normalize_signal_type(ray.get("type")),
                "active": bool(ray.get("active")),
                "segments": segs,
            }
            if ray.get("time") is not None:
                packed_ray["time"] = int(ray["time"])
            rays.append(packed_ray)

    trend = serialize_trend_lines(candles, lines)
    expand = serialize_trend_lines(candles, lines_expand or [])
    pack = {
        "candles": compact_candles(trimmed, chart_bars),
        "rays": rays,
        "trend_lines": trend,
    }
    if expand and expand != trend:
        pack["trend_lines_expand"] = expand
    return pack


def scan_job(job: dict[str, str]) -> dict:
    group = job["group"]
    yahoo = job["yahoo"]
    symbol = job["symbol"]
    name = job["name"]
    timeframe = job["timeframe"]
    cfg = TIMEFRAMES[timeframe]
    touch_window = int(cfg["touch_window"])
    late_touch_window = int(cfg.get("late_touch_window") or LATE_TOUCH_MIN_BARS)
    lookback = int(cfg.get("late_touch_lookback") or LATE_TOUCH_LOOKBACK_BARS)
    late_min_display = int(cfg.get("late_touch_min_display") or LATE_TOUCH_MIN_DISPLAY_BARS)
    near_min_bars = int(cfg.get("near_miss_min_bars") or NEAR_MISS_MIN_BARS)
    near_lookback = int(cfg.get("near_miss_lookback") or NEAR_MISS_LOOKBACK_BARS)
    try:
        candles = with_retries(lambda: fetch_yahoo(yahoo, timeframe))
        signals = detect_signals(candles)
        late = collect_late_ar_dr_touches(candles, signals, touch_window)
        late_lb = collect_late_ar_dr_touches_in_lookback(
            candles, signals, late_touch_window, lookback, late_min_display
        )
        near = collect_late_ar_dr_near_misses(candles, signals, near_min_bars, near_lookback)
        lines = build_auto_trend_lines_expanding(candles)
        lines_e = lines
        trend = collect_trend_touches(candles, lines)
        exceed = collect_trend_exceeds(candles, lines)
        reclaim = collect_trend_reclaims(candles, lines)
        trend_e = trend
        exceed_e = exceed
        events = late + late_lb + near + trend + exceed + reclaim
        events_e200 = trend_e + exceed_e
        for ev in events + events_e200:
            ev["timeframe"] = timeframe
        return {
            "group": group,
            "symbol": symbol,
            "yahoo_symbol": yahoo,
            "name": name,
            "source": "yahoo",
            "timeframe": timeframe,
            "bars": len(candles),
            "events": events,
            "events_e200": events_e200,
            "error": None,
            "chart": build_chart_pack(
                candles, signals, lines, int(cfg["chart_bars"]), lines_e
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "group": group,
            "symbol": symbol,
            "yahoo_symbol": yahoo,
            "name": name,
            "source": "yahoo",
            "timeframe": timeframe,
            "bars": 0,
            "events": [],
            "events_e200": [],
            "error": str(exc),
            "chart": None,
        }


def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fmt_num(v: float) -> str:
    return f"{v:.6g}"


def fmt_tf(tf: str) -> str:
    return TIMEFRAMES.get(tf, {}).get("label", (tf or "?").upper())


def read_static(name: str) -> str:
    with open(os.path.join(STATIC_DIR, name), encoding="utf-8") as f:
        return f.read()


FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com" />'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />'
    '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Noto+Sans+TC:wght@400;500;700&family=Noto+Serif+TC:wght@600;700&display=swap" rel="stylesheet" />'
)


def report_style() -> str:
    return read_static("report.css")


def build_symbol_catalog(results: list[dict], charts: dict) -> list[dict]:
    catalog = []
    for r in results:
        if r.get("error"):
            continue
        g, sym, tf = r["group"], r["symbol"], r.get("timeframe") or "1d"
        ck = chart_key(g, sym, tf)
        catalog.append(
            {
                "group": g,
                "symbol": sym,
                "name": r.get("name") or sym,
                "timeframe": tf,
                "hasHit": bool(r.get("events")),
                "hasChart": ck in charts,
            }
        )
    catalog.sort(
        key=lambda x: (
            not x["hasHit"],
            TF_ORDER.get(x.get("timeframe", ""), 99),
            GROUP_ORDER.get(x["group"], 99),
            x["symbol"],
        )
    )
    return catalog


def page_nav_html(active: str) -> str:
    links = (
        ("twse", "index.html", "上市"),
        ("tpex", "tpex.html", "上櫃"),
        ("watchlist", "watchlist.html", WATCHLIST_NAME),
        ("charts", "charts.html", "圖表"),
    )
    parts = ['<nav class="page-nav">']
    for key, href, label in links:
        cls = "active" if active == key else ""
        parts.append(f'<a href="{href}" class="{cls}">{html.escape(label)}</a>')
    parts.append("</nav>")
    return "".join(parts)


PAGE_POOLS: dict[str, dict[str, Any]] = {
    "twse": {
        "groups": frozenset({"twse", "index"}),
        "title": "上市",
        "pool_label": "上市 + 加權指數",
    },
    "tpex": {
        "groups": frozenset({"tpex"}),
        "title": "上櫃",
        "pool_label": "上櫃",
    },
}


def hit_counts(hits: list[dict]) -> dict[str, int]:
    return {
        "ar_dr_touch": sum(1 for h in hits if h["kind"] == "ar_dr_touch"),
        "ar_dr_late_touch": sum(1 for h in hits if h["kind"] == "ar_dr_late_touch"),
        "ar_dr_near": sum(1 for h in hits if h["kind"] == "ar_dr_near"),
        "trend_touch": sum(1 for h in hits if h["kind"] == "trend_touch"),
        "trend_exceed": sum(1 for h in hits if h["kind"] == "trend_exceed"),
        "trend_reclaim": sum(1 for h in hits if h["kind"] == "trend_reclaim"),
        "hits": len(hits),
    }


def build_watchlist_items(
    symbols: list[str],
    slim_results: list[dict],
    hits: list[dict],
    charts: dict,
) -> list[dict]:
    result_map: dict[str, dict] = {}
    for r in slim_results:
        if (r.get("timeframe") or "1d") != "1d":
            continue
        result_map[r["symbol"]] = r

    hit_map: dict[str, list[str]] = {}
    for h in hits:
        sym = h["symbol"]
        if sym not in symbols:
            continue
        label = str(h.get("label") or h.get("kind") or "")
        if label and label not in hit_map.get(sym, []):
            hit_map.setdefault(sym, []).append(label)

    items: list[dict] = []
    for sym in symbols:
        r = result_map.get(sym)
        if r and not r.get("error"):
            g = r["group"]
            name = r.get("name") or sym
            ck = chart_key(g, sym, "1d")
            items.append(
                {
                    "symbol": sym,
                    "group": g,
                    "name": name,
                    "timeframe": "1d",
                    "hasHit": sym in hit_map,
                    "hasChart": ck in charts,
                    "signals": hit_map.get(sym, []),
                    "error": None,
                }
            )
        elif r and r.get("error"):
            items.append(
                {
                    "symbol": sym,
                    "group": r.get("group", "?"),
                    "name": r.get("name") or sym,
                    "timeframe": "1d",
                    "hasHit": False,
                    "hasChart": False,
                    "signals": [],
                    "error": r.get("error"),
                }
            )
        else:
            items.append(
                {
                    "symbol": sym,
                    "group": "?",
                    "name": sym,
                    "timeframe": "1d",
                    "hasHit": False,
                    "hasChart": False,
                    "signals": [],
                    "error": "不在掃描結果",
                }
            )
    return items


def build_chartpack_items(slim_results: list[dict], charts: dict) -> list[dict]:
    result_map: dict[tuple[str, str, str], dict] = {}
    for r in slim_results:
        tf = r.get("timeframe") or "1d"
        result_map[(r["group"], r["symbol"], tf)] = r
    items: list[dict] = []
    for key, pack in (charts or {}).items():
        parts = str(key).split("|")
        if len(parts) != 3:
            continue
        g, sym, tf = parts
        r = result_map.get((g, sym, tf)) or {}
        candles = (pack or {}).get("candles") or []
        kinds = []
        seen: set[str] = set()
        for ev in r.get("events") or []:
            k = str(ev.get("kind") or "")
            if k and k not in seen:
                seen.add(k)
                kinds.append(k)
        items.append(
            {
                "group": g,
                "symbol": sym,
                "name": r.get("name") or sym,
                "timeframe": tf,
                "bars": len(candles),
                "hasHit": bool(kinds),
                "kinds": kinds,
            }
        )
    items.sort(
        key=lambda x: (
            GROUP_ORDER.get(x["group"], 99),
            TF_ORDER.get(x.get("timeframe", ""), 99),
            x["symbol"],
        )
    )
    return items


def render_html(payload: dict, pool: str = "twse") -> str:
    cfg = PAGE_POOLS[pool]
    groups = cfg["groups"]
    all_hits = payload["hits"]
    hits = [h for h in all_hits if h.get("group") in groups]
    results = [r for r in (payload.get("results") or []) if r.get("group") in groups]
    ar_dr = [h for h in hits if h["kind"] == "ar_dr_touch"]
    ar_dr_late = [h for h in hits if h["kind"] == "ar_dr_late_touch"]
    ar_dr_late.sort(key=lambda h: int(h.get("bars_after_signal") or 0), reverse=True)
    ar_near = [h for h in hits if h["kind"] == "ar_dr_near"]
    ar_near.sort(key=lambda h: int(h.get("bars_after_signal") or 0), reverse=True)
    trend = [h for h in hits if h["kind"] == "trend_touch"]
    exceed = [h for h in hits if h["kind"] == "trend_exceed"]
    reclaim = [h for h in hits if h["kind"] == "trend_reclaim"]
    hc = hit_counts(hits)
    ok = sum(1 for r in results if not r.get("error"))
    jobs_n = len(results)
    u = payload["universe"]
    gen = html.escape(payload["generated_at"])
    page_title = html.escape(str(cfg["title"]))
    pool_label = html.escape(str(cfg["pool_label"]))

    def sym_btn(h: dict) -> str:
        sym = str(h.get("symbol", ""))
        grp = str(h.get("group", ""))
        name = str(h.get("name", sym))
        tf = str(h.get("timeframe", "1d"))
        attrs = (
            f'data-symbol="{html.escape(sym, quote=True)}" '
            f'data-group="{html.escape(grp, quote=True)}" '
            f'data-name="{html.escape(name, quote=True)}" '
            f'data-tf="{html.escape(tf, quote=True)}" '
            f'data-level="{html.escape(str(h.get("level", "")), quote=True)}" '
            f'data-type="{html.escape(str(h.get("type", "")), quote=True)}" '
            f'data-kind="{html.escape(str(h.get("kind", "")), quote=True)}" '
            f'data-time="{html.escape(str(h.get("time", "")), quote=True)}"'
        )
        return (
            f'<button type="button" class="sym-btn" {attrs} title="開啟蠟燭圖">'
            f"<code>{html.escape(sym)}</code></button>"
        )

    def tf_cell(h: dict) -> str:
        return html.escape(fmt_tf(str(h.get("timeframe", "1d"))))

    def pool_cell(h: dict) -> str:
        g = h.get("group", "")
        return html.escape(group_label(str(g)))

    def rows(items: list[dict], empty: str, cols: int, builder) -> str:
        if not items:
            return f'<tr><td colspan="{cols}" class="empty">{empty}</td></tr>'
        return "\n".join(builder(h) for h in items)

    def row_ar_dr(h: dict) -> str:
        sig_type = normalize_signal_type(h.get("type"))
        cls = "ar" if sig_type == "AR" else "ad"
        tf = str(h.get("timeframe", "1d"))
        return (
            f'<tr data-symbol="{html.escape(str(h.get("symbol","")), quote=True)}" '
            f'data-group="{html.escape(str(h.get("group","")), quote=True)}" '
            f'data-timeframe="{html.escape(tf, quote=True)}">'
            f'<td><span class="tag {cls}">{html.escape(sig_type)}</span></td>'
            f"<td>{tf_cell(h)}</td>"
            f"<td>{pool_cell(h)}</td>"
            f"<td>{sym_btn(h)}</td>"
            f"<td>{html.escape(h.get('name', ''))}</td>"
            f'<td class="num">{fmt_num(float(h["level"]))}</td>'
            f'<td class="num">{int(h.get("bars_after_signal", 0))}</td>'
            f"<td>{html.escape(fmt_ts(int(h['time'])))}</td>"
            "</tr>"
        )

    def row_ar_near(h: dict) -> str:
        sig_type = normalize_signal_type(h.get("type"))
        cls = "ar" if sig_type == "AR" else "ad"
        tf = str(h.get("timeframe", "1d"))
        return (
            f'<tr data-symbol="{html.escape(str(h.get("symbol","")), quote=True)}" '
            f'data-group="{html.escape(str(h.get("group","")), quote=True)}" '
            f'data-timeframe="{html.escape(tf, quote=True)}">'
            f'<td><span class="tag {cls}">{html.escape(sig_type)}</span></td>'
            f"<td>{tf_cell(h)}</td>"
            f"<td>{pool_cell(h)}</td>"
            f"<td>{sym_btn(h)}</td>"
            f"<td>{html.escape(h.get('name', ''))}</td>"
            f'<td class="num">{fmt_num(float(h["level"]))}</td>'
            f'<td class="num">{float(h.get("gap_pct", 0)):.3g}%</td>'
            f'<td class="num">{int(h.get("bars_after_signal", 0))}</td>'
            f"<td>{html.escape(fmt_ts(int(h['time'])))}</td>"
            "</tr>"
        )

    def row_trend(h: dict) -> str:
        cls, txt = trend_tag(h)
        tf = str(h.get("timeframe", "1d"))
        return (
            f'<tr data-symbol="{html.escape(str(h.get("symbol","")), quote=True)}" '
            f'data-group="{html.escape(str(h.get("group","")), quote=True)}" '
            f'data-timeframe="{html.escape(tf, quote=True)}">'
            f'<td><span class="tag {cls}">{html.escape(txt)}</span></td>'
            f"<td>{tf_cell(h)}</td>"
            f"<td>{pool_cell(h)}</td>"
            f"<td>{sym_btn(h)}</td>"
            f"<td>{html.escape(h.get('name', ''))}</td>"
            f'<td class="num">{fmt_num(float(h["level"]))}</td>'
            f"<td>{html.escape(fmt_ts(int(h['time'])))}</td>"
            "</tr>"
        )

    def row_exceed(h: dict) -> str:
        cls, txt = trend_tag(h)
        tf = str(h.get("timeframe", "1d"))
        return (
            f'<tr data-symbol="{html.escape(str(h.get("symbol","")), quote=True)}" '
            f'data-group="{html.escape(str(h.get("group","")), quote=True)}" '
            f'data-timeframe="{html.escape(tf, quote=True)}">'
            f'<td><span class="tag {cls}">{html.escape(txt)}</span></td>'
            f"<td>{tf_cell(h)}</td>"
            f"<td>{pool_cell(h)}</td>"
            f"<td>{sym_btn(h)}</td>"
            f"<td>{html.escape(h.get('name', ''))}</td>"
            f'<td class="num">{fmt_num(float(h["level"]))}</td>'
            f'<td class="num">{int(h.get("exceed_bars", TREND_EXCEED_BARS))}</td>'
            f"<td>{html.escape(fmt_ts(int(h['time'])))}</td>"
            "</tr>"
        )

    def row_reclaim(h: dict) -> str:
        return row_exceed(h)

    catalog = build_symbol_catalog(results, payload.get("charts") or {})
    embed_js = (
        "<script>window.CHART_PACKS = {};"
        + "window.SYMBOL_CATALOG = "
        + json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
        + ";window.WATCHLISTS = "
        + json.dumps({WATCHLIST_NAME: WATCHLIST_SYMBOLS}, ensure_ascii=False, separators=(",", ":"))
        + ";</script>\n"
    )

    tf_labels = " · ".join(fmt_tf(tf) for tf in TIMEFRAME_ORDER)
    filter_script = read_static("report-pool-filter.html")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="refresh" content="3600" />
  <title>台股 · {page_title} Alerts</title>
  {FONT_LINKS}
  <style>
{report_style()}
  </style>
</head>
<body>
  <div class="wrap">
    {page_nav_html(pool)}
    <h1>台股 · {page_title} · AR/AD &amp; 趨勢線</h1>
    <p class="meta">
      商品池 <strong>{pool_label}</strong> · 週期 <strong>{tf_labels}</strong> ·
      本頁 {jobs_n} 檔 · 全池掃描 {u['total']} 檔 × {u['timeframes']} 週期 = {u['jobs']} jobs · 更新 {gen}
    </p>
    <div class="cards">
      <div class="card"><div class="lbl">本頁 OK</div><div class="val">{ok}/{jobs_n}</div></div>
      <div class="card"><div class="lbl">AR/AD 觸碰</div><div class="val">{hc['ar_dr_touch']}</div></div>
      <div class="card"><div class="lbl">AR/AD 晚觸10</div><div class="val">{hc['ar_dr_late_touch']}</div></div>
      <div class="card"><div class="lbl">AR/AD 接近</div><div class="val">{hc['ar_dr_near']}</div></div>
      <div class="card"><div class="lbl">趨勢線觸碰</div><div class="val">{hc['trend_touch']}</div></div>
      <div class="card"><div class="lbl">趨勢線超出</div><div class="val">{hc['trend_exceed']}</div></div>
      <div class="card"><div class="lbl">實體超出站回</div><div class="val">{hc['trend_reclaim']}</div></div>
    </div>

    <h2>趨勢線超出（近 {TREND_SIGNAL_BARS} 根 · 與觸碰／站回互斥）</h2>
    <div class="panel"><table><thead><tr>
      <th>類型</th><th>週期</th><th>池</th><th>代碼</th><th>名稱</th><th class="num">價位</th><th class="num">根數</th><th>時間</th>
    </tr></thead><tbody data-section="exceed">{rows(exceed, "目前無超出信號", 8, row_exceed)}</tbody></table></div>

    <h2>趨勢線實體超出後站回（近 {TREND_SIGNAL_BARS} 根實體超出，之後任一根實體站回）</h2>
    <div class="panel"><table><thead><tr>
      <th>類型</th><th>週期</th><th>池</th><th>代碼</th><th>名稱</th><th class="num">價位</th><th class="num">超出根</th><th>時間</th>
    </tr></thead><tbody data-section="reclaim">{rows(reclaim, "目前無實體超出後站回", 8, row_reclaim)}</tbody></table></div>

    <h2>AR / AD 觸碰（超過 20 根日 K 後 · 最近 2 根）</h2>
    <div class="panel"><table><thead><tr>
      <th>類型</th><th>週期</th><th>池</th><th>代碼</th><th>名稱</th><th class="num">價位</th><th class="num">根數</th><th>時間</th>
    </tr></thead><tbody data-section="ar_dr">{rows(ar_dr, "目前無 AR/AD 觸碰", 8, row_ar_dr)}</tbody></table></div>

    <h2>AR / AD 晚觸碰（10 根日 K 內曾觸碰 · 超過 20 根後 · 根數 ≥ 60）</h2>
    <div class="panel"><table><thead><tr>
      <th>類型</th><th>週期</th><th>池</th><th>代碼</th><th>名稱</th><th class="num">價位</th><th class="num">根數</th><th>時間</th>
    </tr></thead><tbody data-section="ar_dr_late">{rows(ar_dr_late, "目前無符合條件的晚觸碰（10 根內 · 根數 ≥ 60）", 8, row_ar_dr)}</tbody></table></div>

    <h2>AR / AD 接近未觸（200 根日 K 內 · 根數 ≥ 60 · 誤差 0～1%）</h2>
    <div class="panel"><table><thead><tr>
      <th>類型</th><th>週期</th><th>池</th><th>代碼</th><th>名稱</th><th class="num">價位</th><th class="num">差距</th><th class="num">根數</th><th>時間</th>
    </tr></thead><tbody data-section="ar_near">{rows(ar_near, "目前無符合條件的接近未觸（200 根內 · 根數 ≥ 60）", 9, row_ar_near)}</tbody></table></div>

    <h2>趨勢線觸碰（近 {TREND_SIGNAL_BARS} 根 · 有突破或站回不標）</h2>
    <div class="panel"><table><thead><tr>
      <th>類型</th><th>週期</th><th>池</th><th>代碼</th><th>名稱</th><th class="num">價位</th><th>時間</th>
    </tr></thead><tbody data-section="trend">{rows(trend, "目前無趨勢線觸碰", 7, row_trend)}</tbody></table></div>

    <footer>每小時自動更新 · <a href="latest.json">latest.json</a></footer>
  </div>

  <button type="button" class="search-fab" id="searchFab" aria-label="搜尋商品">
    <span>🔍</span><span>搜尋商品</span>
  </button>
  <div class="search-overlay" id="symbolOverlay" hidden>
    <div class="search-modal">
      <div class="search-modal-head">
        <input id="symbolSearch" type="search" placeholder="代碼或名稱…" autocomplete="off" />
        <button type="button" id="symbolSearchClose">關閉</button>
      </div>
      <ul id="symbolList"></ul>
    </div>
  </div>

  <div id="chart-modal" class="modal" hidden aria-hidden="true">
    <div class="modal-panel" role="dialog">
      <div class="modal-head">
        <div>
          <div id="chart-title" class="modal-title">Chart</div>
          <div id="chart-sub" class="modal-sub"></div>
        </div>
        <button type="button" class="modal-close" id="chart-close" aria-label="關閉">×</button>
      </div>
      <div class="modal-chart" id="chart-body">
        <div class="modal-status" id="chart-status">載入中…</div>
        <div id="lwc" hidden></div>
        <iframe id="tv-frame" title="TradingView chart" hidden></iframe>
      </div>
    </div>
  </div>
{embed_js}{filter_script}{read_static("report-chart-modal.html")}
</body>
</html>
"""


def render_charts_html(payload: dict, items: list[dict]) -> str:
    gen = html.escape(payload["generated_at"])
    hit_n = sum(1 for x in items if x.get("hasHit"))
    catalog = [
        {
            "group": x["group"],
            "symbol": x["symbol"],
            "name": x.get("name") or x["symbol"],
            "timeframe": x.get("timeframe") or "1d",
            "hasHit": bool(x.get("hasHit")),
            "kinds": list(x.get("kinds") or []),
            "hasChart": True,
        }
        for x in items
    ]
    embed_js = (
        "<script>window.CHART_PACKS = {};"
        + "window.SYMBOL_CATALOG = "
        + json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
        + ";window.WATCHLISTS = "
        + json.dumps({WATCHLIST_NAME: WATCHLIST_SYMBOLS}, ensure_ascii=False, separators=(",", ":"))
        + ";</script>\n"
    )
    extra_css = """
.wrap.charts-page{max-width:1480px}
.charts-page .cards.cols-3{grid-template-columns:repeat(3,minmax(0,1fr));margin-bottom:16px}
.charts-page .card{padding:10px 12px}
.charts-page .card .val{font-size:1.05rem;margin-top:4px}
@media (max-width:900px){
  .charts-page .cards.cols-3{grid-template-columns:repeat(3,minmax(0,1fr))}
  .charts-page .card{border-bottom:0}
  .charts-page .cards .card:nth-child(even){border-right:1px solid var(--rule)}
  .charts-page .cards .card:last-child{border-right:0}
}
.charts-page .pool-filters{margin:0 0 12px}
#chartsFilter{width:100%;box-sizing:border-box;margin:0 0 10px;padding:10px 12px;border:1px solid var(--rule);border-radius:2px;background:var(--paper);color:inherit;font:inherit}
#chartsFilter:focus-visible{outline:2px solid var(--gold);outline-offset:2px}
#chartsStatus{color:var(--muted);font-size:.82rem;margin:0 0 14px}
.charts-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.chart-tile{background:var(--paper);border:1px solid var(--rule);cursor:pointer}
.chart-tile.hit{border-color:rgba(201,160,90,.55)}
.chart-tile-head{display:flex;justify-content:space-between;gap:8px;padding:8px 10px;font-size:.8rem;align-items:baseline}
.chart-tile-head code{font-family:"IBM Plex Mono",ui-monospace,monospace;color:var(--gold-soft)}
.chart-tile-head .muted{color:var(--muted);white-space:nowrap}
.chart-tile-body{height:220px;background:#000;position:relative}
.chart-tile-empty{display:grid;place-items:center;height:100%;min-height:80px;color:var(--muted);font-size:.85rem}
"""
    charts_script = read_static("report-charts.html")
    chart_script = read_static("report-chart-modal.html")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="refresh" content="3600" />
  <title>台股 Chart Packs</title>
  {FONT_LINKS}
  <style>
{report_style()}
{extra_css}
  </style>
</head>
<body>
  <div class="wrap charts-page">
    {page_nav_html("charts")}
    <h1>台股 · Chart Packs</h1>
    <p class="meta">
      共 {len(items)} 份 · 有信號 {hit_n} · 頁面直接畫 Chart Pack · 點擊放大 · 更新 {gen}
    </p>
    <div class="cards cols-3">
      <div class="card"><div class="lbl">Chart Pack</div><div class="val">{len(items)}</div></div>
      <div class="card"><div class="lbl">有信號</div><div class="val">{hit_n}</div></div>
      <div class="card"><div class="lbl">K 棒上限</div><div class="val">{CHART_BARS}</div></div>
    </div>
    <div class="pool-filters" id="chartsKindFilters" role="group" aria-label="訊號類型">
      <button type="button" data-kind="all" class="active">全部</button>
      <button type="button" data-kind="trend_exceed">趨勢超出</button>
      <button type="button" data-kind="trend_reclaim">實體超出站回</button>
      <button type="button" data-kind="ar_dr_touch">AR/AD 觸碰</button>
      <button type="button" data-kind="ar_dr_late_touch">AR/AD 晚觸</button>
      <button type="button" data-kind="ar_dr_near">AR/AD 接近</button>
      <button type="button" data-kind="trend_touch">趨勢觸碰</button>
    </div>
    <input id="chartsFilter" type="search" placeholder="代碼或名稱…" autocomplete="off" />
    <p id="chartsStatus">載入 Chart Packs…</p>
    <div id="chartsGrid" class="charts-grid"></div>
    <footer>每小時自動更新 · <a href="index.html">回上市</a> · <a href="latest.json">latest.json</a></footer>
  </div>

  <div id="chart-modal" class="modal" hidden aria-hidden="true">
    <div class="modal-panel" role="dialog">
      <div class="modal-head">
        <div>
          <div id="chart-title" class="modal-title">Chart</div>
          <div id="chart-sub" class="modal-sub"></div>
        </div>
        <button type="button" class="modal-close" id="chart-close" aria-label="關閉">×</button>
      </div>
      <div class="modal-chart" id="chart-body">
        <div class="modal-status" id="chart-status">載入中…</div>
        <div id="lwc" hidden></div>
        <iframe id="tv-frame" title="TradingView chart" hidden></iframe>
      </div>
    </div>
  </div>
{embed_js}{chart_script}{charts_script}
</body>
</html>
"""


def render_watchlist_html(payload: dict, items: list[dict]) -> str:
    gen = html.escape(payload["generated_at"])
    hit_n = sum(1 for x in items if x.get("hasHit"))
    chart_n = sum(1 for x in items if x.get("hasChart"))

    def wl_row(i: int, item: dict) -> str:
        sym = item["symbol"]
        grp = item["group"]
        name = item.get("name") or sym
        pool = group_label(str(grp)) if grp != "?" else "—"
        sigs = item.get("signals") or []
        if sigs:
            sig_txt = " · ".join(html.escape(s) for s in sigs[:4])
            if len(sigs) > 4:
                sig_txt += " …"
        elif item.get("error"):
            sig_txt = f'<span class="muted">{html.escape(str(item["error"]))}</span>'
        else:
            sig_txt = '<span class="muted">—</span>'
        chart_txt = "有" if item.get("hasChart") else "—"
        attrs = (
            f'data-symbol="{html.escape(sym, quote=True)}" '
            f'data-group="{html.escape(str(grp), quote=True)}" '
            f'data-name="{html.escape(name, quote=True)}" '
            f'data-tf="1d" data-kind="watchlist"'
        )
        btn = (
            f'<button type="button" class="sym-btn" {attrs} title="開啟蠟燭圖">'
            f"<code>{html.escape(sym)}</code></button>"
        )
        return (
            f'<tr data-symbol="{html.escape(sym, quote=True)}" '
            f'data-group="{html.escape(str(grp), quote=True)}" data-timeframe="1d">'
            f'<td class="num">{i}</td>'
            f"<td>{html.escape(pool)}</td>"
            f"<td>{btn}</td>"
            f"<td>{html.escape(name)}</td>"
            f"<td>{sig_txt}</td>"
            f'<td class="num">{chart_txt}</td>'
            "</tr>"
        )

    rows = "\n".join(wl_row(i + 1, item) for i, item in enumerate(items))
    catalog = [
        {
            "group": x["group"],
            "symbol": x["symbol"],
            "name": x.get("name") or x["symbol"],
            "timeframe": "1d",
            "hasHit": bool(x.get("hasHit")),
            "hasChart": bool(x.get("hasChart")),
        }
        for x in items
        if x.get("hasChart")
    ]
    embed_js = (
        "<script>window.CHART_PACKS = {};"
        + "window.SYMBOL_CATALOG = "
        + json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
        + ";window.WATCHLISTS = "
        + json.dumps({WATCHLIST_NAME: WATCHLIST_SYMBOLS}, ensure_ascii=False, separators=(",", ":"))
        + ";</script>\n"
    )
    wl_script = read_static("report-watchlist.html")
    chart_script = read_static("report-chart-modal.html")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="refresh" content="3600" />
  <title>台股 {html.escape(WATCHLIST_NAME)}</title>
  {FONT_LINKS}
  <style>
{report_style()}
  </style>
</head>
<body>
  <div class="wrap">
    {page_nav_html("watchlist")}
    <h1>台股 · {html.escape(WATCHLIST_NAME)}</h1>
    <p class="meta">
      共 {len(items)} 檔 · 有信號 {hit_n} · 有圖表 {chart_n} · 更新 {gen}
    </p>
    <div class="cards cols-3">
      <div class="card"><div class="lbl">自選檔數</div><div class="val">{len(items)}</div></div>
      <div class="card"><div class="lbl">有信號</div><div class="val">{hit_n}</div></div>
      <div class="card"><div class="lbl">有 Chart Pack</div><div class="val">{chart_n}</div></div>
    </div>
    <div class="panel" id="watchlistTable">
      <table>
        <thead><tr>
          <th class="num">#</th><th>池</th><th>代碼</th><th>名稱</th><th>信號</th><th class="num">圖表</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    <footer>每小時自動更新 · <a href="index.html">回上市</a> · <a href="latest.json">latest.json</a></footer>
  </div>

  <div id="chart-modal" class="modal" hidden aria-hidden="true">
    <div class="modal-panel" role="dialog">
      <div class="modal-head">
        <div>
          <div id="chart-title" class="modal-title">Chart</div>
          <div id="chart-sub" class="modal-sub"></div>
        </div>
        <button type="button" class="modal-close" id="chart-close" aria-label="關閉">×</button>
      </div>
      <div class="modal-chart" id="chart-body">
        <div class="modal-status" id="chart-status">載入中…</div>
        <div id="lwc" hidden></div>
        <iframe id="tv-frame" title="TradingView chart" hidden></iframe>
      </div>
    </div>
  </div>
{embed_js}{wl_script}{chart_script}
</body>
</html>
"""


def filter_watchlist_jobs(base_jobs: list[dict[str, str]]) -> list[dict[str, str]]:
    by_sym = {j["symbol"]: j for j in base_jobs if j.get("symbol")}
    out: list[dict[str, str]] = []
    missing: list[str] = []
    for code in WATCHLIST_SYMBOLS:
        job = by_sym.get(code)
        if job:
            out.append(job)
        else:
            missing.append(code)
    if missing:
        print(f"Watchlist missing from universe: {', '.join(missing)}", flush=True)
    return out


def merge_chart_packs(new_charts: dict[str, dict]) -> dict[str, dict]:
    charts = dict(new_charts)
    if os.path.isfile(CHART_PACKS_PATH):
        try:
            prev = json.load(open(CHART_PACKS_PATH, encoding="utf-8"))
            old = prev.get("charts") or {}
            if isinstance(old, dict):
                merged = dict(old)
                merged.update(charts)
                charts = merged
        except (OSError, json.JSONDecodeError):
            pass
    return charts


def main() -> int:
    parser = argparse.ArgumentParser(description="Taiwan AR/AD + trend-line scanner")
    parser.add_argument(
        "--watchlist",
        action="store_true",
        help="只掃自選股，更新 watchlist.html 與對應 chart packs",
    )
    args = parser.parse_args()
    watchlist_only = bool(args.watchlist)

    os.makedirs(OUT_DIR, exist_ok=True)
    base_jobs = build_scan_jobs()
    if watchlist_only:
        base_jobs = filter_watchlist_jobs(base_jobs)
        print(f"Watchlist-only: {len(base_jobs)} symbols", flush=True)
    jobs: list[dict[str, str]] = []
    for job in base_jobs:
        for tf in TIMEFRAME_ORDER:
            jobs.append({**job, "timeframe": tf})
    indices_n = sum(1 for j in base_jobs if j["group"] == "index")
    twse_n = sum(1 for j in base_jobs if j["group"] == "twse")
    tpex_n = sum(1 for j in base_jobs if j["group"] == "tpex")
    print(
        f"Scanning {len(jobs)} jobs "
        f"({len(base_jobs)} symbols × {len(TIMEFRAME_ORDER)} TF: {', '.join(fmt_tf(t) for t in TIMEFRAME_ORDER)})…",
        flush=True,
    )

    results: list[dict] = []
    workers = 16 if os.environ.get("GITHUB_ACTIONS") else 8
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(scan_job, j): j for j in jobs}
        done = 0
        for fut in as_completed(futs):
            results.append(fut.result())
            done += 1
            if done % 20 == 0:
                print(f"  progress {done}/{len(jobs)}", flush=True)

    hits: list[dict] = []
    hits_e200: list[dict] = []
    charts: dict[str, dict] = {}
    slim_results: list[dict] = []

    for r in results:
        pack = r.pop("chart", None)
        events_e200 = r.pop("events_e200", None) or []
        g, sym = r["group"], r["symbol"]
        tf = r.get("timeframe") or "1d"
        key = chart_key(g, sym, tf)
        if pack and not r.get("error"):
            # Keep chart for indices, hits, or catalog browsing (all successful scans)
            charts[key] = normalize_chart_pack(pack) or pack
        slim_results.append(normalize_result_events({k: v for k, v in r.items() if k != "chart"}))
        for ev in r.get("events") or []:
            hits.append(
                normalize_hit(
                    {**ev, "group": g, "symbol": sym, "name": r.get("name"), "timeframe": tf}
                )
            )
        for ev in events_e200:
            hits_e200.append(
                normalize_hit(
                    {**ev, "group": g, "symbol": sym, "name": r.get("name"), "timeframe": tf}
                )
            )

    hits.sort(
        key=lambda x: (
            KIND_ORDER.get(x["kind"], 99),
            TF_ORDER.get(x.get("timeframe", ""), 99),
            GROUP_ORDER.get(x.get("group", ""), 99),
            x["symbol"],
        )
    )
    hits_e200.sort(
        key=lambda x: (
            KIND_ORDER.get(x["kind"], 99),
            TF_ORDER.get(x.get("timeframe", ""), 99),
            GROUP_ORDER.get(x.get("group", ""), 99),
            x["symbol"],
        )
    )

    ok = sum(1 for r in slim_results if not r.get("error"))
    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    payload = {
        "generated_at": generated_at,
        "timeframes": list(TIMEFRAME_ORDER),
        "timeframe": "+".join(TIMEFRAME_ORDER),
        "universe": {
            "total": len(base_jobs),
            "indices": indices_n,
            "twse": twse_n,
            "tpex": tpex_n,
            "timeframes": len(TIMEFRAME_ORDER),
            "jobs": len(jobs),
        },
        "params": {
            "timeframes": {tf: TIMEFRAMES[tf] for tf in TIMEFRAME_ORDER},
            "drop_pct": DROP_PCT,
            "min_streak": MIN_STREAK,
            "vol_mult": VOL_MULT,
        },
        "counts": {
            "jobs": len(jobs),
            "ok": ok,
            "errors": len(jobs) - ok,
            "ar_dr_touch": sum(1 for h in hits if h["kind"] == "ar_dr_touch"),
            "ar_dr_late_touch": sum(1 for h in hits if h["kind"] == "ar_dr_late_touch"),
            "ar_dr_near": sum(1 for h in hits if h["kind"] == "ar_dr_near"),
            "trend_touch": sum(1 for h in hits if h["kind"] == "trend_touch"),
            "trend_exceed": sum(1 for h in hits if h["kind"] == "trend_exceed"),
            "trend_reclaim": sum(1 for h in hits if h["kind"] == "trend_reclaim"),
            "trend_touch_e200": sum(1 for h in hits_e200 if h["kind"] == "trend_touch"),
            "trend_exceed_e200": sum(1 for h in hits_e200 if h["kind"] == "trend_exceed"),
            "hits": len(hits),
            "hits_e200": len(hits_e200),
        },
        "hits": hits,
        "hits_e200": hits_e200,
        "results": slim_results,
        "charts": charts,
        "watchlist": build_watchlist_items(WATCHLIST_SYMBOLS, slim_results, hits, charts),
    }

    if watchlist_only:
        charts = merge_chart_packs(charts)
        payload["charts"] = charts

    if not watchlist_only:
        with open(os.path.join(OUT_DIR, "latest.json"), "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in payload.items() if k != "charts"}, f, ensure_ascii=False, indent=2)

    with open(CHART_PACKS_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"generated_at": generated_at, "charts": charts},
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    wl_page = render_watchlist_html(payload, payload["watchlist"])
    with open(os.path.join(OUT_DIR, "watchlist.html"), "w", encoding="utf-8") as f:
        f.write(wl_page)
    charts_page = render_charts_html(payload, build_chartpack_items(slim_results, charts))
    with open(os.path.join(OUT_DIR, "charts.html"), "w", encoding="utf-8") as f:
        f.write(charts_page)
    if not watchlist_only:
        twse_page = render_html(payload, "twse")
        tpex_page = render_html(payload, "tpex")
        for name in ("latest.html", "index.html"):
            with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
                f.write(twse_page)
        with open(os.path.join(OUT_DIR, "tpex.html"), "w", encoding="utf-8") as f:
            f.write(tpex_page)

    errs = [r for r in slim_results if r.get("error")]
    if errs:
        print(f"Errors ({len(errs)}):", flush=True)
        for e in errs[:8]:
            print(f"  {e['symbol']} ({e['yahoo_symbol']}): {e['error']}", flush=True)

    print(
        f"Hits: {len(hits)} · e200: {len(hits_e200)} · OK: {ok}/{len(jobs)}",
        flush=True,
    )
    if ok == 0:
        return 1
    if ok < len(jobs) * 0.5:
        print(f"Warning: fewer than half of jobs succeeded ({ok}/{len(jobs)})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
