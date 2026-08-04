# 5分鐘K線盤中策略規格

## 策略類型
5分鐘K線盤中策略（獨立模組，與日線 Rule1/Rule2 無關）

## 規格來源
依「HanStock K線符號開發規格 V1」實作。
不可自行更改符號、顏色、條件、名稱或觸發流程。

## 基準 K
當日 09:00–09:05 的第一根 5 分 K（收在 09:05）

---

## 做多訊號

| 符號 | 名稱 | 代碼 | 顏色 |
|------|------|------|------|
| 【5】 | 第N次站上5MA過905高 | crossUp905 | 紅 #ef4444（第2次起粉紅 #f472b6） |
| ⑨ | 首次過905高 | firstCross905High | 紅 #ef4444 |
| ㊇ᴺ | 第N次站上昨日高 | crossUpPrevHigh | 紅 #ef4444 |
| ⑳↑ᴺ | 第N次站上20MA | crossUp20ma | 紅 #ef4444 |
| ⑳↑★ | 首次站上20MA | firstCrossUp20ma | 紅 #ef4444 |
| 520↑ᴺ | 五二零上 | ma520Up | 紅 #ef4444 |
| 520↓ᴺ | 五二零下 | ma520Down | 綠 #22c55e |

## 做空訊號

| 符號 | 名稱 | 代碼 | 顏色 |
|------|------|------|------|
| Ⓓ | 破905D | break905d | 深綠 #047857 |
| Ⓐ | A8空 | a8short | 深綠 #047857 |
| ㊟ | 注意12空 | watch12short | 綠 #22c55e |
| ⑫ | 12空 | short12 | 藍 #3b82f6 |
| ⑫實心 | 加強12空 | enhanced12short | 紫 #c084fc |
| ⑳↓ᴺ | 第N次跌破20MA | crossDown20ma | 綠 #22c55e |
| ⑳↓★ | 首次跌破20MA | firstCrossDown20ma | 綠 #22c55e |
| ↑/↓ | 20MA轉向 | ma20turn | 紅 #dc2626 / 綠 #22c55e |

## 大盤警示

| 符號 | 名稱 | 代碼 | 顏色 |
|------|------|------|------|
| 【15】ᴺ | 破15K低點 | break15kLow | 綠 #22c55e |

## 做多前置條件
905收盤價上漲且漲幅 < 6%（相對昨收），不符則不偵測做多訊號。

## 實作位置
- 做空策略：`taiwan-stock-groups/server/shortStrategy.ts`
- 做多策略：`taiwan-stock-groups/server/longStrategy.ts`
- 大盤警示：`taiwan-stock-groups/server/otcIndex.ts`
- K線圖符號：`taiwan-stock-groups/client/src/components/CandleChartDialog.tsx`
- 盤中訊號頁：`taiwan-stock-groups/client/src/pages/IntradaySignals.tsx`

## 5分鐘K線資料來源
- 盤中：Yahoo Finance 5分K API + 證交所即時價
- 儲存：liveBars 資料表（Manus WebDev 資料庫）

## 版本
- 規格確認日期：2026/08/04
- 規格來源：HanStock K線符號開發規格 V1
