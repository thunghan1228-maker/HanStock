# 券商分點週資料

本功能只接收合法、正式的券商分點資料，不以 Shioaji 逐筆大單推估券商身分。

## 写入

`POST /api/hub/broker-branch-daily`

- Header：`X-Hub-Key`，沿用 `HANSTOCK_HUB_KEY` 或 `HANSTOCK_SYNC_TOKEN`。
- 每笔：`ticker`、`tradeDate`、`netAmount`、`concentration`、`activeBranches`、`source`。
- 单次上限 5,000 笔。

## 读取

`GET /api/hub/broker-branch-weekly`

公开只读，汇总最近五个有资料的交易日；只回传五日均完整的股票。尚未取得五日正式资料时，`rows` 会保持空阵列，避免以不完整资料计算。

战斗版的分点周分数使用「周净买卖相对分数 65%＋方向化集中度相对分数 35%」，最后与法人 35%、集保 25% 组成三项综合分数。

## FinMind Sponsor 自動收集

- Railway 變數：`FINMIND_TOKEN`（只放在 Railway，不寫入 GitHub）。
- 背景工作會補齊最近五個交易日，之後每天盤後自動補最新一日。
- Sponsor 6,000 次／小時以滾動限制保護，預留部分額度避免被 FinMind 封鎖。
- 只保存每檔每日的淨額、方向集中度與活躍分點數，不保存／公開原始分點明細。
- 狀態：`GET /api/hub/persistence/status` 的 `finmindBrokerCollector`。
