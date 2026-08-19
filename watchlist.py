"""User watchlist symbols (TWSE / TPEx 4-digit codes)."""

from __future__ import annotations

_RAW_SYMBOLS = """
1560, 3587, 6239, 3711, 5347, 2330, 2303, 5274, 3034, 2454, 2379, 6890, 6768, 8936, 6894,
5306, 9914, 6285, 5388, 4906, 3596, 8358, 2317, 8299, 6531, 3260, 2408, 2344, 2337, 8086,
3105, 3081, 2455, 6274, 2383, 5222, 3004, 2645, 8255, 6271, 2351, 6191, 4958, 3715, 7788,
7795, 3583, 8261, 6719, 6525, 3485, 2493, 3675, 8042, 4931, 6953, 1723, 3026, 2472, 2404,
6944, 4991, 6564, 6196, 6449, 3716, 3037, 2313, 8210, 6805, 3533, 3017, 2308, 2301, 2059,
2382, 2493, 2441, 3485, 4931, 6564, 6826, 6196, 2404
"""


def parse_watchlist_symbols(raw: str = _RAW_SYMBOLS) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in raw.replace("\n", " ").split(","):
        code = part.strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


WATCHLIST_SYMBOLS: list[str] = parse_watchlist_symbols()
WATCHLIST_NAME = "自選"
