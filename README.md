# 台股 AR/DR Touch Alerts

GitHub Pages 每小時掃描 **台股上市、上櫃** 及 **加權指數** 日 K，輸出 AR/DR 與趨勢線觸碰報告。

## 線上報告

- HTML：https://1mckw.github.io/ts/
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

### AR/DR 晚觸碰門檻

| 類型 | 條件 |
|------|------|
| **觸碰（即時）** | 信號後 >10 根，且觸碰發生在**最近 2 根**日 K |
| **晚觸碰（20 根）** | 信號後 >20 根，觸碰在**最近 20 根**內，且**根數 ≥ 60** 才顯示 |
| **接近未觸（200 根）** | 主引線仍 active、**根數 ≥ 60**、**最近 200 根**內引線距射線 **0～1%** 未觸 |

## AR/DR 規則

| | AR | DR |
|---|----|----|
| 觸發 | 急跌後反轉陽線 | 急漲後反轉陰線 |
| 射線 | 信號 K 上下引線各向右延伸，碰到即停 |
| 晚觸碰 | AR→上引線、DR→下引線；即時 >10 根 / 晚觸清單 >20 根、20 根內、根數 ≥ 60 |

**趨勢線：** 至少 3 觸點；最多 1 條上升支撐 + 1 條下降阻力；觸點較少者圖上 50% 透明；急漲/跌貫穿 grace 2 根 K。圖表會延伸顯示最佳觸碰趨勢線（`best_touch_line`）。

## 手動觸發

Repo → **Actions** → **Hourly TW Alerts (上市 + 上櫃)** → **Run workflow**

## 本機

```bash
python scan_signals.py
```

商品清單：`universe.py` · 自選：`watchlist.py`（`scripts/fetch_tw_stocks.py` 可輸出靜態快照）
