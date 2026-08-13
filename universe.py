"""Scan universe: Taiwan TWSE listed + TPEx OTC common stocks."""

from __future__ import annotations

import json
import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; TW-Alerts/1.0)"}

INDICES: list[tuple[str, str, str, str]] = [
    ("index", "^TWII", "TWII", "加權指數"),
]

GROUP_ORDER = {"index": 0, "twse": 1, "tpex": 2}

_CODE4 = re.compile(r"^\d{4}$")


def _http_get_json(url: str, timeout: int = 60) -> list | dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_twse_stocks() -> list[tuple[str, str, str]]:
    """Return (code, name, yahoo_symbol) for TWSE listed 4-digit symbols."""
    data = _http_get_json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL")
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for row in data:
        code = str(row.get("Code", "")).strip()
        name = str(row.get("Name", "")).strip()
        if not _CODE4.fullmatch(code) or code in seen:
            continue
        seen.add(code)
        out.append((code, name, f"{code}.TW"))
    out.sort(key=lambda x: x[0])
    return out


def fetch_tpex_stocks() -> list[tuple[str, str, str]]:
    """Return (code, name, yahoo_symbol) for TPEx OTC 4-digit symbols."""
    data = _http_get_json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes")
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for row in data:
        code = str(row.get("SecuritiesCompanyCode", "")).strip()
        name = str(row.get("CompanyName", "")).strip()
        if not _CODE4.fullmatch(code) or code in seen:
            continue
        seen.add(code)
        out.append((code, name, f"{code}.TWO"))
    out.sort(key=lambda x: x[0])
    return out


def build_scan_jobs() -> list[dict[str, str]]:
    """Return deduplicated scan jobs; TWSE group wins when a code exists on both markets."""
    jobs: list[dict[str, str]] = []
    seen: set[str] = set()

    for group, yahoo, display, name in INDICES:
        jobs.append(
            {
                "group": group,
                "yahoo": yahoo,
                "symbol": display,
                "name": name,
            }
        )

    twse_stocks = fetch_twse_stocks()
    tpex_stocks = fetch_tpex_stocks()
    twse_set = {code for code, _, _ in twse_stocks}

    for code, name, yahoo in twse_stocks:
        if code in seen:
            continue
        seen.add(code)
        jobs.append(
            {
                "group": "twse",
                "yahoo": yahoo,
                "symbol": code,
                "name": name,
            }
        )

    for code, name, yahoo in tpex_stocks:
        if code in seen:
            continue
        seen.add(code)
        jobs.append(
            {
                "group": "tpex",
                "yahoo": yahoo,
                "symbol": code,
                "name": name,
            }
        )

    for job in jobs:
        if job["group"] == "twse" and job["symbol"] in {c for c, _, _ in tpex_stocks}:
            job["also_tpex"] = "1"
        elif job["group"] == "tpex" and job["symbol"] in twse_set:
            job["also_twse"] = "1"

    return jobs


def group_label(group: str) -> str:
    return {"index": "指數", "twse": "上市", "tpex": "上櫃"}.get(group, group)
