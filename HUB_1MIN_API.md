# HanStock Hub 1 分 K API

## 新增端點

### 單一股票

`GET /api/hub/bars1m/{stock_code}`

範例：

```text
GET /api/hub/bars1m/2330
```

回傳：

```json
{
  "status": "ok",
  "code": "2330",
  "interval": "1m",
  "bar_count": 2,
  "bars": [
    {
      "ts": 1786064400000,
      "open": 100.0,
      "high": 102.0,
      "low": 99.0,
      "close": 101.0,
      "volume": 15,
      "tick_count": 8
    }
  ]
}
```

### 批次股票

`POST /api/hub/bars1m/batch`

```json
{
  "codes": ["2330", "2344", "2408"]
}
```

最多 200 檔，重複代號會自動去除。

## 資料行為

- 1 分 K 與原有 5 分 K 使用同一份 Shioaji 即時 tick，同步聚合。
- 回傳今日 Hub 自啟動且已訂閱後累積的 K 棒，包含目前尚未收完的 1 分 K。
- 每個 K 棒時間 `ts` 是該分鐘起始時間的 Unix epoch milliseconds。
- Hub 重啟後，記憶體中的盤中 K 棒會重新累積；此版本尚未加入 Redis／資料庫持久化與歷史補齊。
- 股票必須已由即時行情 API 訂閱，才會持續收到 tick。前端開啟 K 線前應先呼叫 `GET /api/realtime/{stock_code}?subscribe=true`。

## 原有端點

`GET /api/hub/bars/{stock_code}` 仍維持 5 分 K，並新增 `interval: "5m"` 欄位，不改變原本 `bars` 格式。
