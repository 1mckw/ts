"""TWSE listed + TPEx OTC 4-digit stocks (runtime fetch from exchange OpenAPI)."""

from __future__ import annotations

import json
import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; TW-Alerts/1.0)"}
_CODE4 = re.compile(r"^\d{4}$")


def _http_get_json(url: str, timeout: int = 60) -> list | dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_twse_stocks() -> list[tuple[str, str]]:
    """Return (code, name) for TWSE listed 4-digit symbols."""
    data = _http_get_json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in data:
        code = str(row.get("Code", "")).strip()
        name = str(row.get("Name", "")).strip()
        if not _CODE4.fullmatch(code) or code in seen:
            continue
        seen.add(code)
        out.append((code, name))
    out.sort(key=lambda x: x[0])
    return out


def fetch_tpex_stocks() -> list[tuple[str, str]]:
    """Return (code, name) for TPEx OTC 4-digit symbols."""
    data = _http_get_json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in data:
        code = str(row.get("SecuritiesCompanyCode", "")).strip()
        name = str(row.get("CompanyName", "")).strip()
        if not _CODE4.fullmatch(code) or code in seen:
            continue
        seen.add(code)
        out.append((code, name))
    out.sort(key=lambda x: x[0])
    return out
