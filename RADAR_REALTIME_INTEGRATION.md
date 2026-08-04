# HanStock 台股族群雷達即時行情串接（第一版）

## 完成範圍

這個版本把 HanStock Railway 上的同一條 Shioaji 登入連線擴充為：

1. 保留既有台指期 `TXFR1` 即時行情。
2. 網站查詢單一股票時，動態訂閱該股票 Tick。
3. 網站點開族群時，動態訂閱該族群全部股票 Tick。
4. 後端快取最新 Tick，網站每 1～3 秒讀取快取，不輪詢 Shioaji snapshots/ticks/kbars。
5. 使用 LRU 管理訂閱；預設最多 150 檔，為官方 200 個訂閱上限預留安全空間。

## 新增 API

### 即時行情狀態

```http
GET /api/realtime/status
```

### 指定股票

```http
GET /api/realtime/2330?subscribe=true
```

第一次呼叫可能回傳 `status: waiting`，表示訂閱請求已成功、正在等第一筆成交 Tick。盤中再次呼叫後會取得行情。

### 指定族群或族群內任一股票代號

```http
GET /api/realtime/group/記憶體?subscribe=true&sort=change_desc
GET /api/realtime/group/2344?subscribe=true&sort=change_desc
```

輸入族群內股票代號時，會依 HanStock 規則回傳該股票所屬的完整族群。

可用排序：

- `change_desc`：漲跌幅高到低（預設）
- `code`：股票代號
- `group_order`：原族群順序

### 多檔股票

```http
GET /api/realtime/latest?codes=2330,2344,2408&subscribe=true
```

## 前端串接

把 `web/realtime-radar-client.js` 放入「台股族群雷達」前端，頁面載入：

```html
<script src="/realtime-radar-client.js"></script>
<script>
  HanStockRealtime.setApiBase('https://hanstock.xyz');

  const stopPolling = HanStockRealtime.pollGroup('記憶體', {
    intervalMs: 1500,
    sort: 'change_desc',
    onData(payload) {
      const rows = payload.groups[0]?.stocks || [];
      renderRealtimeRanking(rows);
    },
    onError(error) {
      showRealtimeError(error.message);
    },
  });

  // 切換頁面或族群時：stopPolling();
</script>
```

每個 `stocks` 項目格式：

```json
{
  "rank": 1,
  "stock_code": "2344",
  "stock_name": "華邦電",
  "quote_available": true,
  "quote": {
    "close": 66.5,
    "price_chg": 1.5,
    "pct_chg": 2.31,
    "volume": 20,
    "total_volume": 12345,
    "tick_time": "2026-08-04T09:12:00.123456+08:00",
    "received_at": "2026-08-04T09:12:00.124000+08:00",
    "quote_age_seconds": 0.4,
    "quote_stale": false,
    "subscribed": true
  }
}
```

## Railway 環境變數

既有變數保持不變：

- `SHIOAJI_API_KEY`
- `SHIOAJI_SECRET_KEY`
- `SHIOAJI_QUOTE_ENABLED=true`

可選：

```text
SHIOAJI_STOCK_MAX_SUBSCRIPTIONS=150
SHIOAJI_QUOTE_STALE_SECONDS=60
HANSTOCK_REALTIME_GROUP_MAX_CODES=100
```

若「台股族群雷達」不是部署在 `hanstock.xyz` 同網域，請把它的正式網域加入：

```text
HANSTOCK_CORS_ORIGINS=https://hanstock.xyz,https://www.hanstock.xyz,https://你的族群雷達網域
```

## 驗證順序

1. 部署 PR 測試環境。
2. 開啟 `/api/health`，確認 `shioaji_logged_in=true`。
3. 開啟 `/api/realtime/group/記憶體?subscribe=true`。
4. 第一次若沒有行情，等待 1～3 秒再重新整理。
5. 確認 `available_quote_count` 增加，且 `quote_age_seconds` 很小。
6. 確認原有 `/api/quote/futures` 仍持續更新。
7. 把族群雷達前端接到 PR API 測試網址，確認切換族群不需重新整理整頁。
8. 通過後才合併到 production。

## 安全與限制

- 不會把 API Key 或 Secret Key 傳到瀏覽器。
- 瀏覽器只讀取 HanStock 的公開行情快取 API。
- 官方 `api.subscribe()` 總數上限為 200；此版預設最多訂閱 150 檔。
- 新族群超過容量時會取消最久未使用的族群股票訂閱，再訂閱新族群。
- 盤中不使用 `snapshots`、`ticks`、`kbars` 輪詢。
