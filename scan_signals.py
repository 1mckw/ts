#!/usr/bin/env python3
"""Taiwan TWSE listed + TPEx OTC stocks — AR/DR + trend-line scanner."""

from __future__ import annotations

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

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "signals")
STATIC_DIR = os.path.join(ROOT, "static")
CHART_PACKS_PATH = os.path.join(OUT_DIR, "chart-packs.json")

TIMEFRAMES: dict[str, dict[str, Any]] = {
    "1d": {
        "interval": "1d",
        "range": "5y",
        "bars": 800,
        "chart_bars": 320,
        "touch_window": 5,
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
collect_late_ar_dr_near_misses = ardr.collect_late_ar_dr_near_misses
fresh_range = ardr.fresh_range

TREND_EXCEED_MIN_BARS = tl.TREND_EXCEED_MIN_BARS
TREND_EXCEED_MAX_BARS = tl.TREND_EXCEED_MAX_BARS
TREND_EXCEED_BARS = tl.TREND_EXCEED_BARS
build_auto_trend_lines = tl.build_auto_trend_lines
check_line_invalidation = tl.check_line_invalidation
find_trend_touch = tl.find_trend_touch
find_trend_exceed = tl.find_trend_exceed
line_end_at_break = tl.line_end_at_break

UA = {"User-Agent": "Mozilla/5.0 (compatible; TW-Alerts/1.0)"}
KIND_ORDER = {"trend_exceed": 0, "ar_dr_touch": 1, "ar_dr_near": 2, "trend_touch": 3}


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


def collect_trend_touches(candles: list[dict], lines: list[dict]) -> list[dict]:
    if not candles:
        return []
    lo, last = fresh_range(len(candles))
    hits = []
    for line in lines:
        if check_line_invalidation(candles, line):
            continue
        touch = find_trend_touch(candles, line)
        if not touch or not (lo <= touch["index"] <= last):
            continue
        label = "阻力趨勢線觸碰" if line["type"] == "resistance" else "支撐趨勢線觸碰"
        hits.append(
            {
                "kind": "trend_touch",
                "label": label,
                "type": line["type"],
                "time": touch["time"],
                "index": touch["index"],
                "level": touch["price"],
                "close": touch["close"],
            }
        )
    return hits


def collect_trend_exceeds(candles: list[dict], lines: list[dict]) -> list[dict]:
    hits = []
    for line in lines:
        exc = find_trend_exceed(candles, line)
        if not exc:
            continue
        label = "阻力趨勢線超出" if line["type"] == "resistance" else "支撐趨勢線超出"
        hits.append(
            {
                "kind": "trend_exceed",
                "label": label,
                "type": line["type"],
                "time": exc["time"],
                "index": exc["index"],
                "level": exc["price"],
                "close": exc["close"],
                "exceed_bars": exc["bars"],
            }
        )
    return hits


def build_chart_pack(
    candles: list[dict],
    signals: list[dict],
    lines: list[dict],
    chart_bars: int = 800,
) -> dict:
    trimmed = candles[-chart_bars:] if len(candles) > chart_bars else candles
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
                segs.append({**seg, "t0": clip0, "t1": clip1})
        if segs:
            ray["segments"] = segs
            rays.append(ray)

    trend = []
    for line in lines:
        invalidated = check_line_invalidation(candles, line)
        end_time, end_price = line_end_at_break(candles, line)
        trend.append(
            {
                "type": line["type"],
                "p1": {"time": int(line["p1"]["time"]), "price": float(line["p1"]["price"])},
                "p2": {"time": int(line["p2"]["time"]), "price": float(line["p2"]["price"])},
                "endTime": int(end_time),
                "endPrice": float(end_price),
                "invalidated": invalidated,
                "pivot_count": int(line.get("pivot_count") or 0),
            }
        )
    return {
        "candles": [
            {
                "time": int(c["time"]),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            }
            for c in trimmed
        ],
        "rays": rays,
        "trend_lines": trend,
    }


def scan_job(job: dict[str, str]) -> dict:
    group = job["group"]
    yahoo = job["yahoo"]
    symbol = job["symbol"]
    name = job["name"]
    timeframe = job["timeframe"]
    cfg = TIMEFRAMES[timeframe]
    touch_window = int(cfg["touch_window"])
    try:
        candles = with_retries(lambda: fetch_yahoo(yahoo, timeframe))
        signals = detect_signals(candles)
        late = collect_late_ar_dr_touches(candles, signals, touch_window)
        near = collect_late_ar_dr_near_misses(candles, signals, touch_window)
        lines = build_auto_trend_lines(candles)
        trend = collect_trend_touches(candles, lines)
        exceed = collect_trend_exceeds(candles, lines)
        events = late + near + trend + exceed
        for ev in events:
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
            "error": None,
            "chart": build_chart_pack(candles, signals, lines, int(cfg["chart_bars"])),
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


def render_html(payload: dict) -> str:
    hits = payload["hits"]
    ar_dr = [h for h in hits if h["kind"] == "ar_dr_touch"]
    ar_near = [h for h in hits if h["kind"] == "ar_dr_near"]
    trend = [h for h in hits if h["kind"] == "trend_touch"]
    exceed = [h for h in hits if h["kind"] == "trend_exceed"]
    c = payload["counts"]
    u = payload["universe"]
    gen = html.escape(payload["generated_at"])

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
        cls = "ar" if h.get("type") == "AR" else "dr"
        tf = str(h.get("timeframe", "1d"))
        return (
            f'<tr data-symbol="{html.escape(str(h.get("symbol","")), quote=True)}" '
            f'data-group="{html.escape(str(h.get("group","")), quote=True)}" '
            f'data-timeframe="{html.escape(tf, quote=True)}">'
            f'<td><span class="tag {cls}">{html.escape(str(h.get("type", "")))}</span></td>'
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
        cls = "ar" if h.get("type") == "AR" else "dr"
        tf = str(h.get("timeframe", "1d"))
        return (
            f'<tr data-symbol="{html.escape(str(h.get("symbol","")), quote=True)}" '
            f'data-group="{html.escape(str(h.get("group","")), quote=True)}" '
            f'data-timeframe="{html.escape(tf, quote=True)}">'
            f'<td><span class="tag {cls}">{html.escape(str(h.get("type", "")))}</span></td>'
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
        cls = "resist" if h.get("type") == "resistance" else "support"
        tf = str(h.get("timeframe", "1d"))
        return (
            f'<tr data-symbol="{html.escape(str(h.get("symbol","")), quote=True)}" '
            f'data-group="{html.escape(str(h.get("group","")), quote=True)}" '
            f'data-timeframe="{html.escape(tf, quote=True)}">'
            f'<td><span class="tag {cls}">{html.escape(str(h.get("type", "")))}</span></td>'
            f"<td>{tf_cell(h)}</td>"
            f"<td>{pool_cell(h)}</td>"
            f"<td>{sym_btn(h)}</td>"
            f"<td>{html.escape(h.get('name', ''))}</td>"
            f'<td class="num">{fmt_num(float(h["level"]))}</td>'
            f"<td>{html.escape(fmt_ts(int(h['time'])))}</td>"
            "</tr>"
        )

    def row_exceed(h: dict) -> str:
        cls = "resist" if h.get("type") == "resistance" else "support"
        tf = str(h.get("timeframe", "1d"))
        return (
            f'<tr data-symbol="{html.escape(str(h.get("symbol","")), quote=True)}" '
            f'data-group="{html.escape(str(h.get("group","")), quote=True)}" '
            f'data-timeframe="{html.escape(tf, quote=True)}">'
            f'<td><span class="tag {cls}">{html.escape(str(h.get("type", "")))}</span></td>'
            f"<td>{tf_cell(h)}</td>"
            f"<td>{pool_cell(h)}</td>"
            f"<td>{sym_btn(h)}</td>"
            f"<td>{html.escape(h.get('name', ''))}</td>"
            f'<td class="num">{fmt_num(float(h["level"]))}</td>'
            f'<td class="num">{int(h.get("exceed_bars", TREND_EXCEED_BARS))}</td>'
            f"<td>{html.escape(fmt_ts(int(h['time'])))}</td>"
            "</tr>"
        )

    catalog = build_symbol_catalog(payload.get("results") or [], payload.get("charts") or {})
    embed_js = (
        "<script>window.CHART_PACKS = {};"
        + "window.SYMBOL_CATALOG = "
        + json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
        + ";window.WATCHLISTS = {};</script>\n"
    )

    filter_script = read_static("report-pool-filter.html")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="refresh" content="3600" />
  <title>台股 Touch Alerts</title>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <style>
    :root {{
      --bg: #000; --panel: rgba(8,12,20,.58); --border: rgba(0,255,213,.18);
      --text: #eefdfb; --muted: #7a93a8; --primary: #00f0c8;
      --ar: #00e896; --dr: #ff4d6d;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: "Space Grotesk", system-ui, sans-serif;
      background: #000; color: var(--text); min-height: 100vh; padding: 28px 18px 48px;
    }}
    .wrap {{ max-width: 1100px; margin: 0 auto; }}
    h1 {{ font-size: 1.5rem; color: var(--primary); }}
    .meta {{ color: var(--muted); font-size: .9rem; margin: 8px 0 18px; line-height: 1.5; }}
    .cards {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 16px; }}
    @media (max-width: 900px) {{ .cards {{ grid-template-columns: repeat(2, 1fr); }} }}
    .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; }}
    .card .lbl {{ font-size: .65rem; color: var(--muted); text-transform: uppercase; }}
    .card .val {{ font-family: "JetBrains Mono", monospace; font-size: 1.15rem; font-weight: 700; margin-top: 4px; }}
    .pool-filters {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 18px; }}
    .pool-filters button {{
      font: inherit; cursor: pointer; height: 30px; padding: 0 12px; border-radius: 999px;
      border: 1px solid var(--border); background: rgba(6,10,18,.55); color: var(--muted); font-size: .78rem;
    }}
    .pool-filters button.active {{
      color: #04110e; border-color: transparent;
      background: linear-gradient(135deg, #00f0c8, #00b894);
    }}
    h2 {{ font-size: 1.05rem; margin: 22px 0 10px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 14px; overflow: hidden; overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .84rem; min-width: 640px; }}
    th, td {{ padding: 9px 12px; text-align: left; border-bottom: 1px solid rgba(0,240,200,.08); }}
    th {{ color: var(--muted); font-size: .68rem; text-transform: uppercase; }}
    td.num, th.num {{ text-align: right; font-family: "JetBrains Mono", monospace; }}
    td.empty {{ text-align: center; color: var(--muted); padding: 22px; }}
    code {{ font-family: "JetBrains Mono", monospace; color: var(--primary); }}
    .tag {{ display: inline-block; font-size: .72rem; padding: 2px 7px; border-radius: 5px; font-weight: 700; }}
    .tag.ar {{ background: rgba(0,232,150,.14); color: var(--ar); }}
    .tag.dr {{ background: rgba(255,77,109,.14); color: var(--dr); }}
    .tag.resist {{ background: rgba(255,77,109,.14); color: var(--dr); }}
    .tag.support {{ background: rgba(0,232,150,.14); color: var(--ar); }}
    .sym-btn {{ background: none; border: 0; padding: 0; cursor: pointer; color: inherit; }}
    .sym-btn:hover code {{ text-decoration: underline; }}
    footer {{ margin-top: 28px; color: var(--muted); font-size: .75rem; }}
    a {{ color: var(--primary); }}
    .search-fab {{
      position: fixed; right: 18px; bottom: 22px; z-index: 70;
      display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-radius: 14px;
      border: 1px solid var(--border); background: rgba(6,10,18,.85); color: var(--text); cursor: pointer; font: inherit;
    }}
    .search-overlay {{
      position: fixed; inset: 0; z-index: 85; background: rgba(0,0,0,.62);
      display: flex; align-items: flex-start; justify-content: center; padding: 10vh 16px;
    }}
    .search-overlay[hidden] {{ display: none !important; }}
    .search-modal {{
      width: min(520px, 100%); max-height: 70vh; background: rgba(8,12,20,.95);
      border: 1px solid var(--border); border-radius: 16px; display: flex; flex-direction: column; overflow: hidden;
    }}
    .search-modal-head {{ display: flex; gap: 8px; padding: 14px; border-bottom: 1px solid var(--border); }}
    #symbolSearch {{ flex: 1; height: 42px; padding: 8px 14px; border-radius: 10px; border: 1px solid var(--border); background: #0a0e14; color: var(--text); font-family: "JetBrains Mono", monospace; }}
    #symbolList {{ list-style: none; margin: 0; padding: 8px; overflow-y: auto; flex: 1; }}
    #symbolList li {{ padding: 10px 12px; border-radius: 10px; cursor: pointer; }}
    #symbolList li:hover {{ background: rgba(0,240,200,.06); }}
    .modal {{
      position: fixed; inset: 0; z-index: 80; display: flex; align-items: center; justify-content: center;
      padding: 16px; background: rgba(0,0,0,.62); opacity: 0; pointer-events: none; transition: opacity .2s;
    }}
    .modal.open {{ opacity: 1; pointer-events: auto; }}
    .modal-panel {{
      width: min(1100px, 100%); height: min(720px, 92vh);
      background: rgba(8,12,20,.9); border: 1px solid var(--border); border-radius: 16px;
      display: flex; flex-direction: column; overflow: hidden;
    }}
    .modal-head {{ display: flex; justify-content: space-between; padding: 14px 16px; border-bottom: 1px solid var(--border); }}
    .modal-close {{ width: 40px; height: 40px; border-radius: 10px; border: 1px solid var(--border); background: transparent; color: var(--text); cursor: pointer; }}
    .modal-chart {{ flex: 1; min-height: 0; position: relative; background: #000; }}
    .modal-chart #lwc {{ position: absolute; inset: 0; width: 100%; height: 100%; }}
    .modal-status {{ position: absolute; inset: 0; display: grid; place-items: center; color: var(--muted); }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>台股 · AR/DR &amp; 趨勢線 Alerts</h1>
    <p class="meta">
      商品池 <strong>上市</strong> + <strong>上櫃</strong> 及 <strong>加權指數</strong> · 週期 <strong>1D</strong> ·
      掃描 {u['total']} 檔 · 更新 {gen}
    </p>
    <div class="cards">
      <div class="card"><div class="lbl">掃描 OK</div><div class="val">{c['ok']}/{c['jobs']}</div></div>
      <div class="card"><div class="lbl">AR/DR 觸碰</div><div class="val">{c['ar_dr_touch']}</div></div>
      <div class="card"><div class="lbl">AR/DR 接近</div><div class="val">{c['ar_dr_near']}</div></div>
      <div class="card"><div class="lbl">趨勢線觸碰</div><div class="val">{c['trend_touch']}</div></div>
      <div class="card"><div class="lbl">趨勢線超出</div><div class="val">{c['trend_exceed']}</div></div>
    </div>
    <div class="pool-filters" id="poolFilters">
      <button type="button" data-pool="all" class="active">全部池</button>
      <button type="button" data-pool="index">指數</button>
      <button type="button" data-pool="twse">上市</button>
      <button type="button" data-pool="tpex">上櫃</button>
    </div>
    <h2>趨勢線超出（最新 {TREND_EXCEED_MIN_BARS}–{TREND_EXCEED_MAX_BARS} 根）</h2>
    <div class="panel"><table><thead><tr>
      <th>類型</th><th>週期</th><th>池</th><th>代碼</th><th>名稱</th><th class="num">價位</th><th class="num">根數</th><th>時間</th>
    </tr></thead><tbody data-section="exceed">{rows(exceed, "目前無超出信號", 8, row_exceed)}</tbody></table></div>

    <h2>AR / DR 觸碰（超過 5 根日 K 後）</h2>
    <div class="panel"><table><thead><tr>
      <th>類型</th><th>週期</th><th>池</th><th>代碼</th><th>名稱</th><th class="num">價位</th><th class="num">根數</th><th>時間</th>
    </tr></thead><tbody data-section="ar_dr">{rows(ar_dr, "目前無 AR/DR 觸碰", 8, row_ar_dr)}</tbody></table></div>

    <h2>AR / DR 接近未觸</h2>
    <div class="panel"><table><thead><tr>
      <th>類型</th><th>週期</th><th>池</th><th>代碼</th><th>名稱</th><th class="num">價位</th><th class="num">差距</th><th class="num">根數</th><th>時間</th>
    </tr></thead><tbody data-section="ar_near">{rows(ar_near, "目前無接近未觸", 9, row_ar_near)}</tbody></table></div>

    <h2>趨勢線觸碰</h2>
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


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    base_jobs = build_scan_jobs()
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
    charts: dict[str, dict] = {}
    slim_results: list[dict] = []

    for r in results:
        pack = r.pop("chart", None)
        g, sym = r["group"], r["symbol"]
        tf = r.get("timeframe") or "1d"
        key = chart_key(g, sym, tf)
        if pack and not r.get("error"):
            # Keep chart for indices, hits, or catalog browsing (all successful scans)
            charts[key] = pack
        slim_results.append({k: v for k, v in r.items() if k != "chart"})
        for ev in r.get("events") or []:
            hits.append({**ev, "group": g, "symbol": sym, "name": r.get("name"), "timeframe": tf})

    hits.sort(
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
            "ar_dr_near": sum(1 for h in hits if h["kind"] == "ar_dr_near"),
            "trend_touch": sum(1 for h in hits if h["kind"] == "trend_touch"),
            "trend_exceed": sum(1 for h in hits if h["kind"] == "trend_exceed"),
            "hits": len(hits),
        },
        "hits": hits,
        "results": slim_results,
        "charts": charts,
    }

    with open(os.path.join(OUT_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in payload.items() if k != "charts"}, f, ensure_ascii=False, indent=2)

    with open(CHART_PACKS_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"generated_at": generated_at, "charts": charts},
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    page = render_html(payload)
    for name in ("latest.html", "index.html"):
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
            f.write(page)

    errs = [r for r in slim_results if r.get("error")]
    if errs:
        print(f"Errors ({len(errs)}):", flush=True)
        for e in errs[:8]:
            print(f"  {e['symbol']} ({e['yahoo_symbol']}): {e['error']}", flush=True)

    print(f"Hits: {len(hits)} · OK: {ok}/{len(jobs)}", flush=True)
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
