# HanStock 網站 API 使用說明

## 最簡單啟動方式

直接雙擊：

```text
START_API.bat
```

預設網址：

```text
http://127.0.0.1:8787
```

停止 API：在 API 視窗按 `Ctrl + C`。

## API 路徑

| 功能 | 路徑 |
|---|---|
| 健康檢查 | `/api/health` |
| 全部族群 | `/api/groups` |
| 指定族群 | `/api/groups/記憶體` |
| 股票所屬族群 | `/api/stocks/2344/groups` |
| 最新 Rule1 完整結果 | `/api/rule1/latest` |
| 最新 Rule1 摘要 | `/api/rule1/summary` |
| 指定族群 Rule1 | `/api/rule1/groups/股期標的` |
| 指定股票 Rule1 | `/api/rule1/stocks/1477` |

## 網頁 JavaScript 範例

```javascript
const response = await fetch("http://127.0.0.1:8787/api/rule1/latest");
const data = await response.json();
console.log(data);
```

API 已加入 CORS 標頭，方便本機網站測試。

## 重要限制

`127.0.0.1` 只允許這台電腦存取。要讓 `hanstock.xyz` 從網際網路讀取，後續還需要把 API 部署到雲端，並設定 HTTPS、網域及安全限制。

## Market Data Hub K 棒 API

| 功能 | 方法與路徑 |
|---|---|
| 單一股票 1 分 K | `GET /api/hub/bars1m/2330` |
| 批次股票 1 分 K | `POST /api/hub/bars1m/batch` |
| 單一股票 5 分 K | `GET /api/hub/bars/2330` |
| 批次股票 5 分 K | `POST /api/hub/bars/batch` |

開啟股票 K 線前，請先呼叫 `/api/realtime/{stock_code}?subscribe=true` 建立即時 tick 訂閱。完整說明請看 `HUB_1MIN_API.md`。
