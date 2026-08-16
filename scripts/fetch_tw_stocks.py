#!/usr/bin/env python3
"""Fetch TWSE listed + TPEx OTC 4-digit symbols and print Python list tuples."""

from __future__ import annotations

import json
import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; tw-alerts-scanner/1.0)"}
_CODE4 = re.compile(r"^\d{4}$")


def _http_get_json(url: str, timeout: int = 60) -> list | dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_twse() -> list[tuple[str, str]]:
    data = _http_get_json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in data:
        code = str(row.get("Code", "")).strip()
        name = str(row.get("Name", "")).strip()
        if not _CODE4.fullmatch(code) or code in seen:
            continue
        seen.add(code)
        name = name.replace("\\", "\\\\").replace('"', '\\"')
        out.append((code, name))
    out.sort(key=lambda x: x[0])
    return out


def fetch_tpex() -> list[tuple[str, str]]:
    data = _http_get_json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in data:
        code = str(row.get("SecuritiesCompanyCode", "")).strip()
        name = str(row.get("CompanyName", "")).strip()
        if not _CODE4.fullmatch(code) or code in seen:
            continue
        seen.add(code)
        name = name.replace("\\", "\\\\").replace('"', '\\"')
        out.append((code, name))
    out.sort(key=lambda x: x[0])
    return out


def main() -> None:
    twse = fetch_twse()
    tpex = fetch_tpex()
    if len(twse) < 800:
        raise SystemExit(f"expected ~1100 TWSE rows, got {len(twse)}")
    if len(tpex) < 600:
        raise SystemExit(f"expected ~890 TPEx rows, got {len(tpex)}")
    print(f"# TWSE + TPEx (TWSE={len(twse)}, TPEx={len(tpex)})")
    print("TWSE_STOCKS: list[tuple[str, str]] = [")
    for code, name in twse:
        print(f'    ("{code}", "{name}"),')
    print("]")
    print()
    print("TPEX_STOCKS: list[tuple[str, str]] = [")
    for code, name in tpex:
        print(f'    ("{code}", "{name}"),')
    print("]")


if __name__ == "__main__":
    main()
