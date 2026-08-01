HanStock 快速連線補丁

目的：不依賴 Railway 同步金鑰，雲端第一次部署時直接顯示內建的最新 Rule1 結果。

使用方式：
1. 用本 ZIP 覆蓋 C:\Projects\HanStock（保留 .env、data、.git）。
2. git add -A
3. git commit -m "Add cloud Rule1 fallback data"
4. git push
5. 等 Railway 自動部署後重新整理首頁。

之後仍可再修復安全同步；本補丁只解決現在要快速顯示資料的需求。
