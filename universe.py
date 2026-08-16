"""Scan universe: TWSE listed + TPEx OTC stocks."""

from __future__ import annotations

from tw_stocks_constituents import fetch_tpex_stocks, fetch_twse_stocks

INDICES: list[tuple[str, str, str, str]] = [
    ("index", "^TWII", "TWII", "加權指數"),
]

GROUP_ORDER = {"index": 0, "twse": 1, "tpex": 2}


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
    twse_set = {code for code, _ in twse_stocks}
    tpex_set = {code for code, _ in tpex_stocks}

    for code, name in twse_stocks:
        if code in seen:
            continue
        seen.add(code)
        jobs.append(
            {
                "group": "twse",
                "yahoo": f"{code}.TW",
                "symbol": code,
                "name": name,
            }
        )

    for code, name in tpex_stocks:
        if code in seen:
            continue
        seen.add(code)
        jobs.append(
            {
                "group": "tpex",
                "yahoo": f"{code}.TWO",
                "symbol": code,
                "name": name,
            }
        )

    for job in jobs:
        sym = job.get("symbol", "")
        if job["group"] == "twse" and sym in tpex_set:
            job["also_tpex"] = "1"
        elif job["group"] == "tpex" and sym in twse_set:
            job["also_twse"] = "1"

    return jobs


def group_label(group: str) -> str:
    return {"index": "指數", "twse": "上市", "tpex": "上櫃"}.get(group, group)
