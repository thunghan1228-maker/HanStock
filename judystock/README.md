# Judy Stock

Judy Stock 是從 HanStock 簡化出來的獨立網站：只保留「股票族群查詢」跟「Rule1 掃描結果」
這兩個核心功能，拿掉即時行情、永豐證券連線等比較複雜的部分。這個資料夾跟 HanStock 本身
完全分開執行，不會互相影響。

## 部署（完全免費，不用買網址）

1. 到 [render.com](https://render.com) 用 GitHub 帳號登入。
2. 右上角 **New +** → **Web Service** → 選這個 repo。
3. **Root Directory** 填 `judystock`（一定要填，Render 才知道只用這個資料夾建站）。
4. Environment／Runtime 選 **Docker**，Plan 選 **Free**，其他欄位不用填，按 **Create Web Service**。
5. 等幾分鐘建置完成，Render 會給一個網址，例如 `https://judystock.onrender.com`，打開就是網站首頁。

不用申請網域，也不用額外付費，Render 的免費方案就夠用。免費方案閒置 15 分鐘會自動休眠，
下次有人連線時大約 1 分鐘內會自動醒來，之後就正常。

## 之後想放真的資料

網站一開始會先顯示內建的範例資料。如果之後想放真正的 Rule1 掃描結果，可以：

1. 在 Render 後台幫這個服務加一個環境變數 `JUDYSTOCK_SYNC_TOKEN`，自訂一組密碼。
2. 把 HanStock 本機掃描出來的 Rule1 JSON，用同一組密碼呼叫 `POST /api/rule1/sync` 上傳即可。

這一步不是必要的，不做也完全不影響網站正常運作。
