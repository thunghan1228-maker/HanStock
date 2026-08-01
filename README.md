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
