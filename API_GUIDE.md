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
