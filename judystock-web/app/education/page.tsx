"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  educationArticleCount,
  educationArticles,
  educationCategories,
  type EducationArticle,
  type EducationCategory,
} from "./education-data";
import "./education.css";

type CategoryFilter = "all" | EducationCategory;

function normalizeSearch(value: string) {
  return value.toLocaleLowerCase("zh-TW").replace(/[＋+\s／/・,，。%％()（）-]/g, "");
}

function articleSearchText(article: EducationArticle) {
  return normalizeSearch([
    article.title,
    article.summary,
    article.location,
    article.timeframe,
    ...article.rules,
    ...(article.filters ?? []),
    article.reading ?? "",
    article.version ?? "",
    ...(article.keywords ?? []),
  ].join(" "));
}

function statusClass(status: EducationArticle["status"]) {
  if (status === "正式規則") return "verified";
  if (status === "條件整理中") return "review";
  if (status === "影片製作中") return "video";
  return "reference";
}

function SignalFlowVisual() {
  return (
    <div className="education-flow" aria-label="從市場方向到個股訊號的建議閱讀流程">
      <div><span>01</span><strong>盤勢</strong><small>先分強弱</small></div>
      <i aria-hidden="true">›</i>
      <div><span>02</span><strong>族群</strong><small>確認排名</small></div>
      <i aria-hidden="true">›</i>
      <div><span>03</span><strong>個股</strong><small>查看條件</small></div>
      <i aria-hidden="true">›</i>
      <div><span>04</span><strong>風險</strong><small>設定防守</small></div>
    </div>
  );
}

function ArticleCard({ article }: { article: EducationArticle }) {
  const category = educationCategories.find((item) => item.id === article.category);
  return (
    <article className="education-article" id={`article-${article.id}`}>
      <details>
        <summary>
          <div className="education-article-index"><span>{category?.icon}</span><small>{category?.short}</small></div>
          <div className="education-article-copy">
            <div className="education-article-meta">
              <em className={statusClass(article.status)}>{article.status ?? "名詞說明"}</em>
              <span>{article.timeframe}</span>
            </div>
            <h2>{article.title}</h2>
            <p>{article.summary}</p>
          </div>
          <b className="education-expand" aria-hidden="true">＋</b>
        </summary>
        <div className="education-article-body">
          <div className="education-location"><span>功能位置</span><strong>{article.location}</strong></div>
          <section>
            <h3>定義與判定方式</h3>
            <ol>{article.rules.map((rule) => <li key={rule}>{rule}</li>)}</ol>
          </section>
          {article.filters?.length ? (
            <section className="education-filter-note">
              <h3>排除與補充條件</h3>
              <ul>{article.filters.map((rule) => <li key={rule}>{rule}</li>)}</ul>
            </section>
          ) : null}
          {article.reading ? <aside><strong>怎麼解讀</strong><p>{article.reading}</p></aside> : null}
          <footer>
            <span>{article.version ? `規則版本｜${article.version}` : "內容版本｜第一版"}</span>
            <span>影片｜{article.status === "影片製作中" ? "規劃中" : "預留位置"}</span>
            <a href="#education-search">回到搜尋</a>
          </footer>
        </div>
      </details>
    </article>
  );
}

export default function EducationPage() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<CategoryFilter>("all");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initialQuery = params.get("q") ?? "";
    const initialCategory = params.get("category");
    const frame = window.requestAnimationFrame(() => {
      if (initialQuery) setQuery(initialQuery);
      if (educationCategories.some((item) => item.id === initialCategory)) setCategory(initialCategory as CategoryFilter);
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const categoryCounts = useMemo(() => new Map(educationCategories.map((item) => [
    item.id,
    item.id === "all" ? educationArticleCount : educationArticles.filter((article) => article.category === item.id).length,
  ])), []);

  const filteredArticles = useMemo(() => {
    const normalized = normalizeSearch(query);
    return educationArticles.filter((article) => (
      (category === "all" || article.category === category)
      && (!normalized || articleSearchText(article).includes(normalized))
    ));
  }, [category, query]);

  const chooseCategory = (next: CategoryFilter) => {
    setCategory(next);
    const url = new URL(window.location.href);
    if (next === "all") url.searchParams.delete("category");
    else url.searchParams.set("category", next);
    window.history.replaceState({}, "", url);
  };

  const updateQuery = (value: string) => {
    setQuery(value);
    const url = new URL(window.location.href);
    if (value.trim()) url.searchParams.set("q", value.trim());
    else url.searchParams.delete("q");
    window.history.replaceState({}, "", url);
  };

  const clearFilters = () => {
    setQuery("");
    setCategory("all");
    window.history.replaceState({}, "", window.location.pathname);
  };

  return (
    <main className="education-shell">
      <header className="education-topbar">
        <Link href="/" aria-label="返回 HanStock 盤中戰鬥版">← <span>返回戰鬥版</span></Link>
        <div className="education-brand"><i>H</i><span><strong>HanStock</strong><small>教學中心</small></span></div>
        <nav aria-label="教學中心快速連結">
          <Link href="/stock-screener">選股程式</Link>
          <a href="#education-search">全文搜尋</a>
        </nav>
      </header>

      <section className="education-hero">
        <div className="education-hero-copy">
          <span>HANSTOCK LEARNING CENTER · V1.0</span>
          <h1>看得懂訊號，<br />才知道<span>怎麼用。</span></h1>
          <p>把盤中訊號、五分 K 符號、日線策略、選股與籌碼算法整理成同一套可搜尋的說明。規則以現行 HanStock 程式為準；尚未核實的公式會明確標示「條件整理中」。</p>
          <div className="education-hero-actions">
            <a href="#education-search">開始找名詞</a>
            <Link href="/stock-screener">前往選股程式 ›</Link>
          </div>
        </div>
        <aside>
          <div className="education-stat"><strong>{educationArticleCount}</strong><span>篇首版文字教學</span></div>
          <div className="education-stat"><strong>8</strong><span>大內容分類</span></div>
          <div className="education-stat wide"><strong>文字先行</strong><span>圖片與影片欄位已預留</span></div>
          <SignalFlowVisual />
        </aside>
      </section>

      <section className="education-search-panel" id="education-search">
        <div className="education-search-copy">
          <span>SEARCH THE GLOSSARY</span>
          <h2>搜尋名詞、訊號或算法</h2>
          <p>可搜尋「一二空、1＋2 多、905D、520、特大買單、紅劍、三角收斂、綜合分數」等關鍵字。</p>
        </div>
        <label className="education-search-box">
          <span aria-hidden="true">⌕</span>
          <input
            type="search"
            value={query}
            onChange={(event) => updateQuery(event.target.value)}
            placeholder="輸入名詞、功能、數字門檻或策略名稱…"
            aria-label="搜尋教學內容"
          />
          {query ? <button type="button" onClick={() => updateQuery("")} aria-label="清除搜尋">×</button> : null}
        </label>
        <div className="education-quick-search" aria-label="熱門搜尋">
          <span>熱門：</span>
          {["盤中特大買單", "12空", "1+2多", "905D", "日線11策略", "均線分數"].map((term) => (
            <button type="button" key={term} onClick={() => updateQuery(term)}>{term}</button>
          ))}
        </div>
      </section>

      <nav className="education-categories" aria-label="教學分類">
        {educationCategories.map((item) => (
          <button
            type="button"
            key={item.id}
            className={category === item.id ? "active" : ""}
            onClick={() => chooseCategory(item.id)}
            aria-pressed={category === item.id}
          >
            <span>{item.icon}</span>
            <strong>{item.label}</strong>
            <small>{categoryCounts.get(item.id) ?? 0}</small>
          </button>
        ))}
      </nav>

      <section className="education-results" aria-live="polite">
        <header>
          <div>
            <span>KNOWLEDGE INDEX</span>
            <h2>{category === "all" ? "全部教學" : educationCategories.find((item) => item.id === category)?.label}</h2>
          </div>
          <aside><strong>{filteredArticles.length}</strong><span>篇符合目前條件</span></aside>
        </header>
        <div className="education-result-note">
          <span><i className="verified" />正式規則</span>
          <span><i className="reference" />名詞說明</span>
          <span><i className="review" />條件整理中</span>
          <small>點文章標題即可展開完整公式、排除條件與解讀方式。</small>
        </div>
        <div className="education-article-list">
          {filteredArticles.map((article) => <ArticleCard key={article.id} article={article} />)}
          {filteredArticles.length === 0 ? (
            <div className="education-empty">
              <span>0</span>
              <strong>找不到符合的內容</strong>
              <p>可改用較短的關鍵字，或清除分類後查看全部文章。</p>
              <button type="button" onClick={clearFilters}>清除搜尋與分類</button>
            </div>
          ) : null}
        </div>
      </section>

      <section className="education-video-roadmap">
        <div><span>VIDEO ROADMAP</span><h2>文字規則先建立，影片接在同一篇下面。</h2></div>
        <ol>
          <li><b>30 秒</b><span>快速名詞與畫面位置</span></li>
          <li><b>3–5 分鐘</b><span>完整操作與判讀流程</span></li>
          <li><b>案例版</b><span>成立、未成立與失效對照</span></li>
        </ol>
      </section>

      <footer className="education-footer">
        <div><strong>HanStock 教學中心</strong><span>規則版本 v1.0 · 2026/09/04</span></div>
        <p>本站內容依公開市場資料與 HanStock 現行程式條件整理，僅供研究與功能教學，不代表投資建議或獲利保證。</p>
        <Link href="/">返回盤中戰鬥版 ↑</Link>
      </footer>
    </main>
  );
}
