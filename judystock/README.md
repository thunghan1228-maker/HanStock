# Judy Stock

Judy Stock 是從 HanStock 簡化出來的獨立網站：保留「即時行情」「族群查詢」「四項精選／
特大買賣單」「主力累積」「今日精選」「Rule1 掃描結果」，拿掉券商分點（需要另一個
FinMind 付費帳號）、三角收斂／VCP 盤後選股等比較進階或需要額外付費帳號的部分。這個
資料夾跟 HanStock 本身完全分開執行，不會互相影響。

「四項精選」「特大買賣單」「今日精選」這三個不是 Judy Stock 自己重新計算的——它們的
正式演算法其實是在你另一個網站（hanstock-battle-minimal）裡面，已經天天正確在跑，
Judy Stock 只是唯讀去讀那邊算好的最新結果來顯示，確保跟你平常看到的數字一致，不會
因為重寫一套簡化版算法而算出不一樣的東西。

## 部署（完全免費，不用買網址）

1. 到 [render.com](https://render.com) 用 GitHub 帳號登入。
2. 右上角 **New +** → **Web Service** → 選這個 repo。
3. **Root Directory** 填 `judystock`（一定要填，Render 才知道只用這個資料夾建站）。
4. Environment／Runtime 選 **Docker**，Plan 選 **Free**。
5. 加環境變數（見下方「開啟即時行情」），按 **Create Web Service**。
6. 等幾分鐘建置完成，Render 會給一個網址，例如 `https://judystock.onrender.com`，打開就是網站首頁。

不用申請網域，也不用額外付費，Render 的免費方案就夠用。免費方案閒置 15 分鐘會自動休眠，
下次有人連線時大約 1 分鐘內會自動醒來，之後就正常。

## 開啟即時行情、大戶訊號

這些功能需要永豐證券的 API 金鑰（跟 HanStock 用同一組即可，這只是讀行情，不會下單）。
在 Render 後台幫這個服務加以下環境變數：

- `SHIOAJI_QUOTE_ENABLED` = `true`
- `SHIOAJI_API_KEY` = 你的永豐 API Key
- `SHIOAJI_SECRET_KEY` = 你的永豐 Secret Key

不設定的話，網站一樣能開啟，只是即時行情、大戶訊號那幾個區塊會顯示「尚未啟用」，
族群查詢跟 Rule1 不受影響。

同一組永豐帳號在 HanStock 跟 Judy Stock 兩邊同時登入行情是正常的（永豐允許同一帳號
開多條唯讀行情連線），不會互相干擾。

## 四項精選、特大買賣單、今日精選

首頁這幾個區塊都是唯讀去讀你另一個 Battle 網站（`hanstock-battle-minimal`）已經算好
的正式結果，預設網址已經指向你目前在用的那個。如果之後那個網站的網址改變，可以用
環境變數 `JUDYSTOCK_BATTLE_SITE_URL` 覆寫。這幾個功能不需要 Shioaji 金鑰也能顯示，
只要 Battle 網站本身正常運作就會有資料。

## 之後想放真的 Rule1 資料

網站一開始會先顯示內建的範例資料。如果之後想放真正的 Rule1 掃描結果，可以：

1. 在 Render 後台幫這個服務加一個環境變數 `JUDYSTOCK_SYNC_TOKEN`，自訂一組密碼。
2. 把 HanStock 本機掃描出來的 Rule1 JSON，用同一組密碼呼叫 `POST /api/rule1/sync` 上傳即可。

這一步不是必要的，不做也完全不影響網站正常運作。
