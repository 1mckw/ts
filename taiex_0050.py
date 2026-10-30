"""TAIEX / 台指期 → 0050、0050反 價格換算（整合自 1mckw/TAIEX20050）。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from scan_signals import (
    FONT_LINKS,
    OUT_DIR,
    fetch_yahoo,
    page_nav_html,
    report_style,
    with_retries,
)
from tradingview_quotes import (
    TV_TAIEX_SYMBOL,
    TV_TAIEX_URL,
    TV_TX_SYMBOL,
    TV_TX_URL,
    fetch_taiex,
    fetch_tx_futures,
)

ETF_PRODUCTS: dict[str, dict[str, str]] = {
    "0050": {"symbol": "0050.TW", "column": "etf_0050"},
    "0050反": {"symbol": "00632R.TW", "column": "etf_0050_inv"},
}
LOOKBACK_BARS = 60
DATA_NAME = "taiex-0050.json"
PAGE_NAME = "taiex.html"


def _closes(symbol: str, bars: int = 80) -> dict[str, float]:
    rows = with_retries(lambda: fetch_yahoo(symbol, "1d", bars))
    out: dict[str, float] = {}
    for r in rows:
        day = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc).strftime("%Y-%m-%d")
        out[day] = float(r["close"])
    return out


def fetch_aligned_series(lookback: int = LOOKBACK_BARS) -> list[dict[str, Any]]:
    twii = _closes("^TWII")
    e50 = _closes("0050.TW")
    inv = _closes("00632R.TW")
    days = sorted(set(twii) & set(e50) & set(inv))
    if len(days) < 10:
        raise ValueError("無法對齊 TAIEX / 0050 / 0050反 歷史收盤")
    days = days[-lookback:]
    return [
        {"date": d, "taiex": twii[d], "etf_0050": e50[d], "etf_0050_inv": inv[d]}
        for d in days
    ]


def fit_linear(xs: list[float], ys: list[float]) -> tuple[float, float]:
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return my, 0.0
    beta = num / den
    alpha = my - beta * mx
    return float(alpha), float(beta)


def _product_payload(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    col = ETF_PRODUCTS[name]["column"]
    xs = [float(r["taiex"]) for r in rows]
    ys = [float(r[col]) for r in rows]
    alpha, beta = fit_linear(xs, ys)
    ref_taiex = xs[-1]
    ref_etf = ys[-1]
    ratio = ref_etf / ref_taiex if ref_taiex else 0.0
    scenarios = []
    for pct in (-0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03):
        t = ref_taiex * (1 + pct)
        implied = ref_etf + beta * (t - ref_taiex)
        scenarios.append(
            {
                "label": f"{pct * 100:+.0f}%" if pct != 0 else "基準",
                "taiex": round(t, 2),
                "etf": round(implied, 2),
            }
        )
    history = [
        {"date": r["date"], "taiex": round(float(r["taiex"]), 2), "etf": round(float(r[col]), 2)}
        for r in rows
    ]
    return {
        "symbol": ETF_PRODUCTS[name]["symbol"],
        "latest": round(ref_etf, 2),
        "model": {
            "method": "spread",
            "alpha": round(alpha, 6),
            "beta": round(beta, 8),
            "ratio": round(ratio, 8),
            "ref_taiex": round(ref_taiex, 2),
            "ref_etf": round(ref_etf, 2),
        },
        "scenarios": scenarios,
        "history": history,
    }


def export_payload(lookback: int = LOOKBACK_BARS) -> dict[str, Any]:
    rows = fetch_aligned_series(lookback)
    yahoo_taiex = round(float(rows[-1]["taiex"]), 2)
    taiex_payload = taiex_error = None
    tx_payload = tx_error = None
    try:
        taiex_payload = fetch_taiex()
    except Exception as exc:  # noqa: BLE001
        taiex_error = str(exc)
    try:
        tx_payload = fetch_tx_futures()
    except Exception as exc:  # noqa: BLE001
        tx_error = str(exc)

    ref_taiex = taiex_payload["price"] if taiex_payload else yahoo_taiex
    latest_out: dict[str, Any] = {
        "taiex": ref_taiex,
        "0050": round(float(rows[-1]["etf_0050"]), 2),
        "0050反": round(float(rows[-1]["etf_0050_inv"]), 2),
    }
    if tx_payload:
        latest_out["tx"] = tx_payload["price"]
        latest_out["basis"] = round(tx_payload["price"] - ref_taiex, 2)

    products = {name: _product_payload(rows, name) for name in ETF_PRODUCTS}
    sources = {
        "taiex": {
            "provider": "TradingView" if taiex_payload else "Yahoo Finance",
            "symbol": TV_TAIEX_SYMBOL if taiex_payload else "^TWII",
            "url": TV_TAIEX_URL if taiex_payload else None,
        },
        "0050": {"provider": "Yahoo Finance", "symbol": "0050.TW"},
        "0050反": {"provider": "Yahoo Finance", "symbol": "00632R.TW"},
    }
    if tx_payload:
        sources["tx"] = {"provider": "TradingView", "symbol": TV_TX_SYMBOL, "url": TV_TX_URL}

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": {
            "taiex": TV_TAIEX_SYMBOL if taiex_payload else "^TWII",
            "tx": TV_TX_SYMBOL,
        },
        "latest": latest_out,
        "taiex": taiex_payload,
        "taiex_error": taiex_error,
        "tx": tx_payload,
        "tx_error": tx_error,
        "sources": sources,
        "sample": {"start": rows[0]["date"], "end": rows[-1]["date"], "days": len(rows)},
        "products": products,
    }


def render_taiex_html() -> str:
    extra_css = read_taiex_css()
    js = read_taiex_js()
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>台股 · TAIEX → 0050 換算</title>
  {FONT_LINKS}
  <style>
{report_style()}
{extra_css}
  </style>
</head>
<body>
  <div class="wrap taiex-page">
    {page_nav_html("taiex")}
    <h1>台股 · TAIEX → 0050 / 0050反</h1>
    <p class="meta">輸入加權或台指期，用近 60 日價差關係推算 0050 與 0050反。僅供參考。</p>
    <div class="status-bar">
      <span id="status">載入中…</span>
      <span id="countdown" class="muted">--</span>
    </div>
    <div class="panel taiex-panel">
      <div class="taiex-toolbar">
        <label class="field-compact">
          <span class="lbl">換算方法</span>
          <select id="method">
            <option value="spread" selected>價差法</option>
            <option value="ratio">倍率法</option>
            <option value="regression">迴歸法</option>
          </select>
        </label>
        <button id="refresh-btn" type="button" class="taiex-btn">立即更新</button>
      </div>
      <div class="taiex-block">
        <h2>輸入點位</h2>
        <div class="pool-filters" id="modeTabs" role="tablist">
          <button type="button" class="mode-tab active" data-mode="taiex">TAIEX</button>
          <button type="button" class="mode-tab" data-mode="tx">台指期</button>
        </div>
        <div class="taiex-grid">
          <label>
            <span class="lbl" id="input-label">TAIEX 點位</span>
            <input id="price-input" type="number" step="0.01" inputmode="decimal" placeholder="例如 23000" />
          </label>
          <label class="basis-field" id="basis-wrap" hidden>
            <span class="lbl">基差（期貨 − 現貨）</span>
            <input id="basis" type="number" step="1" inputmode="numeric" value="0" />
          </label>
        </div>
        <p class="conversion-note" id="implied-taiex-line" hidden>
          <span id="tx-source-line">台指期 --</span>
          → 隱含 TAIEX <strong id="implied-taiex">--</strong>
        </p>
        <button type="button" id="latest-btn" class="taiex-btn ghost">帶入最新 TAIEX</button>
      </div>
      <div class="taiex-block">
        <h2>推算結果</h2>
        <div class="cards cols-2">
          <div class="card">
            <div class="lbl">0050</div>
            <div class="val" id="implied-etf50">--</div>
          </div>
          <div class="card">
            <div class="lbl">0050反</div>
            <div class="val" id="implied-etf-inv">--</div>
          </div>
        </div>
      </div>
    </div>
    <footer>
      TAIEX / 台指期：TradingView · 0050 / 0050反：Yahoo Finance ·
      工具來源 <a href="https://github.com/1mckw/TAIEX20050">TAIEX20050</a>
    </footer>
  </div>
{js}
</body>
</html>
"""


def read_taiex_css() -> str:
    path = os.path.join(os.path.dirname(__file__), "static", "taiex-0050.css")
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_taiex_js() -> str:
    path = os.path.join(os.path.dirname(__file__), "static", "taiex-0050.js")
    with open(path, encoding="utf-8") as f:
        return f"<script>\n{f.read()}\n</script>"


def write_outputs(out_dir: str = OUT_DIR) -> dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    payload = export_payload()
    json_path = os.path.join(out_dir, DATA_NAME)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    html_path = os.path.join(out_dir, PAGE_NAME)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_taiex_html())
    return payload


def main() -> None:
    payload = write_outputs()
    lat = payload["latest"]
    extra = ""
    if lat.get("tx") is not None:
        extra = f" | TX {lat['tx']}"
    if payload.get("tx_error"):
        extra += f" (TX: {payload['tx_error']})"
    if payload.get("taiex_error"):
        extra += f" (TV TAIEX: {payload['taiex_error']})"
    print(
        f"wrote {DATA_NAME} / {PAGE_NAME} · TAIEX {lat['taiex']}{extra} · "
        f"0050 {lat['0050']} · 0050反 {lat['0050反']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
