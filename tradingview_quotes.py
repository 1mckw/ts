"""TradingView quotes for TAIEX and TXF1! (stdlib HTTP)."""

from __future__ import annotations

import json
import urllib.request

TV_SCAN_GLOBAL = "https://scanner.tradingview.com/global/scan"
TV_SCAN_TAIWAN = "https://scanner.tradingview.com/taiwan/scan"
TV_TAIEX_SYMBOL = "TWSE:IX0001"
TV_TAIEX_DISPLAY = "INDEX:TAIEX"
TV_TAIEX_URL = "https://www.tradingview.com/symbols/INDEX-TAIEX/"
TV_TX_SYMBOL = "TAIFEX:TXF1!"
TV_TX_URL = "https://www.tradingview.com/symbols/TAIFEX-TXF1!/"

_UA = {"User-Agent": "Mozilla/5.0 (compatible; TW-Alerts/1.0)", "Content-Type": "application/json"}
_SCAN_COLUMNS = [
    "close",
    "lp",
    "ch",
    "change",
    "open",
    "high",
    "low",
    "volume",
    "description",
]


def _scan_symbol(symbol: str, *, scan_url: str = TV_SCAN_GLOBAL) -> dict:
    payload = {
        "symbols": {"tickers": [symbol], "query": {"types": []}},
        "columns": _SCAN_COLUMNS,
    }
    req = urllib.request.Request(
        scan_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_UA,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode())
    rows = body.get("data") or []
    if not rows:
        raise ValueError(f"TradingView 查無 {symbol}")
    row = rows[0]["d"]
    close, lp, ch, change, open_, high, low, volume, description = row
    price = lp if lp is not None else close
    if price is None:
        raise ValueError(f"TradingView 無有效價格：{symbol}")
    return {
        "symbol": symbol,
        "name": description or symbol,
        "price": round(float(price), 2),
        "close": round(float(close), 2) if close is not None else None,
        "lp": round(float(lp), 2) if lp is not None else None,
        "change": round(float(ch), 2) if ch is not None else None,
        "change_pct": round(float(change), 4) if change is not None else None,
        "open": round(float(open_), 2) if open_ is not None else None,
        "high": round(float(high), 2) if high is not None else None,
        "low": round(float(low), 2) if low is not None else None,
        "volume": int(volume) if volume is not None else None,
        "source": "TradingView",
    }


def fetch_taiex() -> dict:
    data = _scan_symbol(TV_TAIEX_SYMBOL, scan_url=TV_SCAN_TAIWAN)
    return {**data, "display": TV_TAIEX_DISPLAY, "url": TV_TAIEX_URL}


def fetch_tx_futures() -> dict:
    data = _scan_symbol(TV_TX_SYMBOL, scan_url=TV_SCAN_GLOBAL)
    return {**data, "url": TV_TX_URL}
