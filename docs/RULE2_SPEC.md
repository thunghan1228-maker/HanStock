# Rule2 日線空方策略規格

## 策略類型
日線策略（非5分鐘策略）

## 觸發條件（三項全部成立＝空方確認）

| 條件 | 名稱 | 判斷邏輯 |
|------|------|---------|
| 1 | 今日高點未突破昨日高點 | 今日最高價 < 昨日最高價（壓力未破） |
| 2 | 20日均線持續下彎 | 20MA 連續 3 日下降，且非走平（相對降幅 > 0.05%） |
| 3 | 近5日反彈收盤皆未站上20日均線 | 近 5 日每日收盤 < 當日 20MA |

## 重要說明
- 三項條件全部成立才算「空方確認」
- 「持續下彎」不可只是走平，必須有明確下降趨勢
- 此為日線策略，不是5分鐘策略

## 資料需求
- 至少 23 個交易日的日線收盤價和最高價
- 20MA 計算需要 20 日收盤價

## 排除條件
- 處置股
- 權證

## 實作位置
- 前端 + 後端：`taiwan-stock-groups/server/quotes.ts` → `checkRule2()`
- 前端頁面：`taiwan-stock-groups/client/src/pages/Rule1Scan.tsx`（Rule2 掃描 tab）
- 後端 tRPC：`taiwan-stock-groups/server/routers.ts` → `stocks.rule2Scan`

## 注意
- HanStock 後端（hanstock.xyz）目前沒有 Rule2 實作
- Rule2 完全在 taiwan-stock-groups 前端專案中實作

## 版本
- 規格確認日期：2026/08/04
- 規格來源：使用者確認
