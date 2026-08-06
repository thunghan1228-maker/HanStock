# HanStock Hub 1 分 K 修改紀錄

版本：API v1.3.0

完成項目：

- 新增獨立 1 分 K 即時聚合器。
- 保留原有 5 分 K 聚合器與 API 相容性。
- 新增 `GET /api/hub/bars1m/{stock_code}`。
- 新增 `POST /api/hub/bars1m/batch`。
- WebSocket 新增 `bar1m_completed` 事件。
- Hub status 新增 1 分 K 完成數與股票數統計。
- 股票代號輸入驗證及批次去重。
- 新增 1 分／5 分 K 聚合單元測試。

部署前請由曼娜執行：

```bash
python -m unittest discover -s tests -p "test_*.py"
python -m compileall .
```

部署後驗證：

```text
/api/health
/api/hub/status
/api/realtime/2330?subscribe=true
/api/hub/bars1m/2330
/api/hub/bars/2330
```

盤中至少等待跨過一個完整分鐘，再確認 1 分 K 由 1 根增加為 2 根。
