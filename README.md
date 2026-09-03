# 台股 AR/AD Touch Alerts

GitHub Pages 每小時掃描 **台股上市、上櫃** 及 **加權指數** 日 K，輸出 AR/AD 與趨勢線觸碰報告。

## 線上報告

- 上市：https://1mckw.github.io/ts/
- 上櫃：https://1mckw.github.io/ts/tpex.html
- 自選：https://1mckw.github.io/ts/watchlist.html
- JSON：`/latest.json`

## 商品池

| 池 | 數量 | 說明 |
|----|------|------|
| **指數** | 1 | 加權指數（`^TWII`） |
| **上市** | ~1100 | TWSE 四碼普通股／ETF（Yahoo：`XXXX.TW`） |
| **上櫃** | ~890 | TPEx 四碼上櫃股票（Yahoo：`XXXX.TWO`） |

清單來源：執行時自 [TWSE OpenAPI](https://openapi.twse.com.tw/) 與 [TPEx OpenAPI](https://www.tpex.org.tw/openapi/) 抓取，四碼代號過濾（排除權證等非四碼商品）。與 DJI/NDX/SP500 重疊時，**上市優先於上櫃**。

共 **~1986** 檔 × **1D** = **~1986** 掃描 jobs（去重後）。

週期：**1D** · 更新：每小時（UTC 整點）

| 週期 | 歷史 K | 圖表顯示 |
|------|--------|----------|
| 1D | 800 | 320 |

### AR/AD 晚觸碰門檻

| 類型 | 條件 |
|------|------|
| **觸碰（即時）** | 信號後 >20 根，且觸碰發生在**最近 2 根**日 K |
| **晚觸碰（10 根）** | 信號後 >20 根，觸碰在**最近 10 根**內，且**根數 ≥ 60** 才顯示 |
| **接近未觸（200 根）** | 主引線仍 active、**根數 ≥ 60**、**最近 200 根**內引線距射線 **0～1%** 未觸 |

## AR/AD 規則

| | AR | AD |
|---|---|---|
| 觸發 | 急跌後反轉陽線 | 急漲後反轉陰線 |
| 射線 | 信號 K 上下引線各向右延伸，碰到即停 |
| 晚觸碰 | AR→上引線、AD→下引線；即時 >20 根 / 晚觸清單 >20 根、10 根內、根數 ≥ 60 |

**趨勢線：** 至少 3 觸點；最多 1 條上升支撐（綠）+ 1 條下降阻力（紅）；須 `valid_to_current`（有效至**最新 K**）；急漲/跌貫穿 grace 2 根 K。另繪 **200D 藍線**：最近 200 根日 K 內觸點最多的線（可已破線）。

## 手動觸發

Repo → **Actions** → **Hourly TW Alerts (上市 + 上櫃)** → **Run workflow**

## 本機

```bash
python scan_signals.py
```

商品清單：`universe.py` · 自選：`watchlist.py`（`scripts/fetch_tw_stocks.py` 可輸出靜態快照）
