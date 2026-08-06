# HanStock Hub 1 分 K 驗證報告

## 本機靜態／單元驗證

- Python compileall：通過
- unittest：14 項通過，0 失敗
- 1 分 K OHLCV 聚合：通過
- 1 分／5 分 K 同步聚合：通過
- 延遲 tick 不造成 K 棒時間倒退：通過
- 單一與批次 1 分 K API：通過
- 原有即時行情與訂閱測試：通過
- 敏感檔名與明碼金鑰掃描：未發現 `.env`、憑證或明碼 API Key

## 尚待正式環境驗證

- 本環境沒有使用使用者的永豐憑證登入，因此未執行真實 Shioaji 盤中 tick 測試。
- 尚未部署 Railway。
- 部署後需先訂閱股票，跨過至少一個完整分鐘，再確認 `/api/hub/bars1m/{stock_code}` K 棒數量增加。
- Hub 重啟後，記憶體 K 棒會重新累積；目前未加入歷史補齊或持久化。

## 測試摘要

```text
2026-08-06 12:21:12 [INFO] hanstock.api: ＝＝＝＝ 啟動 Shioaji 即時行情服務 ＝＝＝＝
2026-08-06 12:21:12 [INFO] hanstock.reconnect_monitor: [ReconnectMonitor] 健康監控已啟動
2026-08-06 12:21:12 [INFO] hanstock.api: ＝＝＝＝ Market Data Hub 已啟動 ＝＝＝＝
test_group_query_returns_ranked_quotes (test_api_realtime.RealtimeApiTests.test_group_query_returns_ranked_quotes) ... 2026-08-06 12:21:12 [INFO] httpx: HTTP Request: GET http://testserver/api/realtime/group/%E8%A8%98%E6%86%B6%E9%AB%94 "HTTP/1.1 200 OK"
ok
test_hub_one_minute_bars_endpoint (test_api_realtime.RealtimeApiTests.test_hub_one_minute_bars_endpoint) ... 2026-08-06 12:21:12 [INFO] httpx: HTTP Request: GET http://testserver/api/hub/bars1m/2330 "HTTP/1.1 200 OK"
ok
test_hub_one_minute_batch_endpoint_deduplicates (test_api_realtime.RealtimeApiTests.test_hub_one_minute_batch_endpoint_deduplicates) ... 2026-08-06 12:21:12 [INFO] httpx: HTTP Request: POST http://testserver/api/hub/bars1m/batch "HTTP/1.1 200 OK"
ok
test_single_stock_endpoint (test_api_realtime.RealtimeApiTests.test_single_stock_endpoint) ... 2026-08-06 12:21:12 [INFO] httpx: HTTP Request: GET http://testserver/api/realtime/2330 "HTTP/1.1 200 OK"
ok
test_stock_code_resolves_full_group (test_api_realtime.RealtimeApiTests.test_stock_code_resolves_full_group) ... 2026-08-06 12:21:12 [INFO] httpx: HTTP Request: GET http://testserver/api/realtime/group/2344 "HTTP/1.1 200 OK"
ok
2026-08-06 12:21:12 [INFO] hanstock.api: ＝＝＝＝ 關閉 Shioaji 即時行情服務 ＝＝＝＝
test_batch_returns_independent_symbols (test_market_data_hub_bars.MarketDataHubBarTests.test_batch_returns_independent_symbols) ... ok
test_delayed_tick_does_not_move_bar_time_backwards (test_market_data_hub_bars.MarketDataHubBarTests.test_delayed_tick_does_not_move_bar_time_backwards) ... ok
test_five_minute_aggregator_remains_compatible (test_market_data_hub_bars.MarketDataHubBarTests.test_five_minute_aggregator_remains_compatible) ... ok
test_hub_status_includes_one_minute_stats (test_market_data_hub_bars.MarketDataHubBarTests.test_hub_status_includes_one_minute_stats) ... ok
test_one_minute_ohlcv_and_rollover (test_market_data_hub_bars.MarketDataHubBarTests.test_one_minute_ohlcv_and_rollover) ... ok
test_invalid_contract_returns_failure (test_quote_service_stock.QuoteServiceStockTests.test_invalid_contract_returns_failure) ... 2026-08-06 12:21:12 [WARNING] hanstock.quote_service: [Shioaji] 台股 BAD1 訂閱失敗: 找不到股票合約：BAD1
ok
test_lru_evicts_old_subscription (test_quote_service_stock.QuoteServiceStockTests.test_lru_evicts_old_subscription) ... 2026-08-06 12:21:12 [INFO] hanstock.quote_service: [Shioaji] 已請求訂閱台股 Tick: 2330
2026-08-06 12:21:12 [INFO] hanstock.quote_service: [Shioaji] 已請求訂閱台股 Tick: 2344
2026-08-06 12:21:12 [INFO] hanstock.quote_service: [Shioaji] 已取消台股 Tick 訂閱: 2330
2026-08-06 12:21:12 [INFO] hanstock.quote_service: [Shioaji] 已請求訂閱台股 Tick: 2408
ok
test_stock_tick_normalizes_pct_chg (test_quote_service_stock.QuoteServiceStockTests.test_stock_tick_normalizes_pct_chg) ... ok
test_subscription_is_idempotent (test_quote_service_stock.QuoteServiceStockTests.test_subscription_is_idempotent) ... 2026-08-06 12:21:12 [INFO] hanstock.quote_service: [Shioaji] 已請求訂閱台股 Tick: 2330
2026-08-06 12:21:12 [INFO] hanstock.quote_service: [Shioaji] 已請求訂閱台股 Tick: 2344
ok

----------------------------------------------------------------------
Ran 14 tests in 0.033s

OK
```
