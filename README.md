# 翰閣室內設計 作品報導網站

純靜態網頁，不需要任何程式或套件，直接開啟 `index.html` 即可瀏覽。

## 結構

```
index.html                  作品報導列表（首頁）
works/
  autumn-cicada/            《秋蟬 Autumn Cicada》報導
    index.html              報導頁面
    css/style.css           版面樣式（仿雜誌報導版面，含手機版）
    webfonts/               圖示字型
    images/01.jpg ~ 10.jpg  報導照片，依文章順序編號
    images/ad-*.jpg         側欄圖片
    images/related-*.jpg    其他推薦圖片
```

## 新增一篇報導

1. 複製 `works/autumn-cicada` 成新的資料夾，例如 `works/clarity`。
2. 換掉 `images/` 裡的照片，檔名維持 `01.jpg` 到 `10.jpg`。
3. 修改 `index.html` 裡的標題、文章與圖說。
4. 在根目錄的 `index.html` 列表加上新報導的連結。

## 發佈成網站

到 GitHub 倉庫的 Settings → Pages，Source 選 `Deploy from a branch`，Branch 選 `main`、資料夾選 `/ (root)`，儲存後幾分鐘就會有網址。免費方案需要倉庫為公開才能使用 Pages。
