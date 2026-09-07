# 戰鬥版股票行情模式

戰鬥版預設停用一般／小型個股期貨行情。`SHIOAJI_STOCK_FUTURES_ENABLED` 未設定或為 `false` 時：

- 不建立個股期貨訂閱、不查股期盤後 Snapshot、不回補股期歷史 K 棒。
- 舊頁面呼叫股期報價／股期 K 棒端點會得到 `status: disabled` 與明確原因。
- 共享行情連線仍供股票 Tick 使用；股票訂閱和回調不受影響。
- 原有台指期市場指標保留；停用範圍是個股期貨。

此設定在部署啟動時生效。發布新容器會終止舊容器的股期訂閱，重新建立股票需要的共享連線。歷史資料不刪除。

若日後明確要重新啟用個股期貨，可設定 `SHIOAJI_STOCK_FUTURES_ENABLED=true` 並重新部署。需先重新評估股票與股期共用的訂閱容量。

狀態端點 `/api/hub/stock-futures/status` 應顯示 `stock_futures_enabled: false`、`active_subscription_count: 0`，而股票啟動後 `active_stock_subscription_count` 應大於 0。
