# HanStock 台灣股票選股系統

HanStock 是以永豐 Shioaji API、SQLite 與 Python 建立的台灣股票行情、族群辨識及策略選股系統。

## 目前完成項目

- 永豐 Shioaji API 安全登入與自動登出
- 股票行情快照
- 一分鐘 K 線與五分鐘 K 線合成
- 近 30 日日 K 下載與 SQLite 儲存
- 69 個股票分類、854 筆族群成分股紀錄
- 去除重複後 664 檔股票日 K 批次下載
- 輸入族群名稱或族群內任一股票代號，自動辨識並掃描整個族群
- Rule1 單股、單族群及全族群掃描
- Rule1 全族群結果輸出為 JSON
- Git 與 GitHub 私人備份

## 主程式操作

在專案資料夾執行：

```powershell
py main.py
```

進入 HanStock 後可使用：

| 輸入 | 功能 |
|---|---|
| `2344` | 自動辨識所屬族群並掃描整個族群 |
| `記憶體` | 掃描指定族群 |
| `groups` | 顯示全部分類 |
| `r1all` | 掃描全部分類 Rule1，並保存 JSON |
| `latest` | 直接讀取最近一次 Rule1 JSON 結果，不重新掃描 |
| `help` | 顯示操作說明 |
| `q` | 結束程式 |

也可以直接執行：

```powershell
py read_rule1_results.py
```

## Rule1 日線多方條件

1. 五日均線向上。
2. 昨天收盤價為近 10 日收盤新高。
3. 今天即時價或最近一日收盤價大於五日均線。
4. 今天即時價或最近一日收盤價小於前一日收盤價。

目前程式以資料庫中最近一日收盤價作為第 3、4 項判斷基礎。

## 常用資料更新指令

只統計全部族群與去重股票數量：

```powershell
py save_all_daily_bars.py --dry-run
```

下載全部去重股票近 30 日日 K：

```powershell
py save_all_daily_bars.py --delay 0.5
```

執行全族群 Rule1：

```powershell
py scan_rule1_all.py
```

## 安裝套件

```powershell
py -m pip install -r requirements.txt
```

## 機密資料與本機資料

以下內容不應上傳 GitHub，也不應傳給其他人：

- `.env`：永豐 API Key 與 Secret Key
- `data/`：本機 SQLite 資料庫與策略結果
- `*.log`：執行日誌

`.gitignore` 已排除上述內容。

## 後續規劃

- Rule2 五分鐘 K 策略
- 更多日線與五分鐘多空策略
- 即時行情訂閱與盤中更新
- 網站 API 與 HanStock 網站串接
- LINE Bot 通知與查詢
- 手機 App

## 網站 API

新版已加入 FastAPI，讓 hanstock.xyz、LINE Bot 或 App 能讀取 HanStock 資料。

安裝套件後啟動：

```powershell
py -m pip install -r requirements.txt
py run_api.py
```

啟動後可開啟：

- 首頁：`http://127.0.0.1:8000`
- API 文件：`http://127.0.0.1:8000/docs`
- 健康檢查：`http://127.0.0.1:8000/api/health`
- 最新 Rule1：`http://127.0.0.1:8000/api/rule1/latest`
- 符合 Rule1：`http://127.0.0.1:8000/api/rule1/passed`
- 全部族群：`http://127.0.0.1:8000/api/groups`
- 以股票代號找族群：`http://127.0.0.1:8000/api/groups/2344`

網站跨網域來源可用環境變數調整：

```text
HANSTOCK_CORS_ORIGINS=https://hanstock.xyz,https://www.hanstock.xyz
```

注意：本機 API 只能在這台電腦上存取。要讓公開網站存取，後續仍需部署到雲端或建立安全的反向代理。

## 圖形化控制台

執行 `py run_api.py`，或雙擊 `start_hanstock_api.bat`，再開啟：

- 控制台：http://127.0.0.1:8000
- API 文件：http://127.0.0.1:8000/docs

控制台可顯示最新 Rule1 結果、查詢族群名稱或股票代號，以及查看全部族群。

## Railway 雲端部署

此版本已包含 `Dockerfile` 與 `railway.json`。

Railway 需要設定：

- `HANSTOCK_SYNC_TOKEN`：自行建立一組長密碼，用來保護 Rule1 上傳 API。
- `HANSTOCK_DATA_DIR=/data`：搭配 Railway Volume，讓最新 Rule1 JSON 可持久保存。
- `HANSTOCK_CORS_ORIGINS=https://hanstock.xyz,https://www.hanstock.xyz`

Railway Volume 建議掛載到 `/data`。

本機同步到雲端前，於本機 `.env` 增加：

```text
HANSTOCK_CLOUD_API_URL=https://你的-railway-網址
HANSTOCK_SYNC_TOKEN=與 Railway 相同的同步金鑰
```

執行：

```powershell
py sync_rule1_results.py
```

## Hub 1 分 K（API v1.3.0）

已新增由 Shioaji 即時 tick 聚合的 1 分 K：

- `GET /api/hub/bars1m/{stock_code}`
- `POST /api/hub/bars1m/batch`

原有 `GET /api/hub/bars/{stock_code}` 仍為 5 分 K。部署與盤中驗證步驟請看 `HUB_1MIN_API.md` 及 `HUB_1MIN_CHANGELOG.md`。
