"""個股研究補強（2026-10-10 使用者：照莊爸「個股快查」補上我們缺的兩塊——產業白話介紹、季報）。

- 產業白話：先看我們的族群（STOCK_GROUPS），每個族群一段白話介紹；不在族群裡的用官方產業別的介紹。
  同族群公司＋同官方產業的公司一起列出來（官方產業別來自 FinMind TaiwanStockInfo，免登入，一週更新一次）。
- 季報：FinMind TaiwanStockFinancialStatements（免登入，單季數字）近 8 季：營收、毛利率、營益率、淨利率、EPS、
  營收／EPS 年增；近四季 EPS 合計與用最新收盤算的本益比。每檔查過存起來，3 天內不重抓。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
INFO_TTL_DAYS = 7
FIN_TTL_DAYS = 3
QUARTERS = 8
PEERS_MAX = 14
EPS_BASE_MIN = 0.1           # 去年同季 EPS 絕對值小於這個就不算 EPS 年增 %

# 族群白話介紹（2026-10-10 寫；成員見 stock_groups.STOCK_GROUPS）
GROUP_INTROS: dict[str, str] = {
    "被動元件": "電阻、電容、電感這些「自己不會運作、但每個電子產品都少不了」的小零件。AI 伺服器、車用電子用量大增時最受惠，報價跟著供需漲跌很明顯。",
    "記憶體": "DRAM（電腦的暫存記憶）和 NAND Flash（手機、SSD 的儲存）。報價一漲獲利就跳得很快，景氣循環很明顯，要看合約價走勢。",
    "矽光子": "用「光」取代「電」在晶片之間傳資料，更快也更省電。AI 資料中心的高速傳輸是主要需求，包含光通訊模組、雷射元件與檢測設備。",
    "摺疊手機": "摺疊手機的關鍵零件，最重要的是轉軸（鉸鏈）。看三星、蘋果等品牌摺疊機的出貨量。",
    "矽晶圓": "把矽做成一片片圓形薄片，是所有晶片的地基。看晶圓廠產能利用率與長約價格，景氣循環比晶片晚一步。",
    "D電腦": "工業電腦：放在工廠、交通、醫療設備裡長時間運作的電腦。客製化多、毛利穩，近年加上邊緣 AI 題材。",
    "化學": "石化原料與化學品（燒鹼、樹脂、溶劑…），跟油價、中國供需與景氣循環連動。",
    "化學二": "化學與化纖原料（聚酯、溶劑、紡織用化學品），跟油價、中國供需連動，常常一起漲跌。",
    "軍工": "國防與航太：飛機零組件、無人機、造船、軍用通訊。看國防預算、無人機國家隊與國際軍備需求。",
    "設備股": "半導體設備與耗材：晶圓廠擴產時要買的機台、濕製程、檢測、搬運設備。跟台積電等大廠的資本支出同步。",
    "玻璃基板": "先進封裝的新材料：用玻璃取代傳統載板，讓 AI 晶片封得更大、更平。多在研發送樣階段，題材性強。",
    "重電": "變壓器、配電盤、開關設備等電網設備。台電強韌電網計畫、AI 資料中心用電、美國電網汰換都需要。",
    "神盾": "神盾集團（指紋辨識、IC 設計、矽智財）相關公司，消息面常一起漲跌。",
    "小電腦": "電池模組與備援電力（BBU）：筆電電池、AI 伺服器斷電時頂上的備援電池。AI 伺服器出貨帶動。",
    "PCB": "印刷電路板與 IC 載板：晶片要「坐」在載板上才能接到電路。AI 伺服器用的高階 ABF 載板層數多、單價高。",
    "小電組": "電子零組件中小型股（電路板、連接器、線材…），跟 AI 伺服器、網通設備出貨連動。",
    "特化": "特用化學品：半導體製程用的高純度化學品、氣體與材料。跟晶圓廠擴產、供應鏈在地化有關。",
    "散熱": "散熱模組、風扇、水冷板。AI 晶片越來越熱，從氣冷走向水冷，單價大幅提高。",
    "PA": "功率放大器（手機、WiFi 發射訊號用）與砷化鎵晶圓代工。看手機、網通需求。",
    "二極體": "二極體、MOSFET 等功率元件：控制電流的開關，車用、工控、電源都要用。",
    "石英": "石英元件：電子產品的「心跳」（時脈），每個電子裝置都要。AI 伺服器、低軌衛星帶來高階需求。",
    "探針卡": "晶片做好後用來測試的探針卡與測試座。AI 晶片越複雜、測試越久越貴，台灣廠商高階市占提升。",
    "低軌衛星": "低軌衛星的地面接收設備、天線、射頻元件與電路板。看 SpaceX Starlink、Amazon Kuiper 的發射與用戶數。",
    "工具機": "加工金屬的機台（車床、銑床、CNC）與滾珠螺桿、線性滑軌等傳動元件。跟全球製造業景氣、匯率連動。",
    "機器人": "工業機器人、自動化設備與機器人零組件（馬達、減速機、控制器），人形機器人是近年題材。",
    "光電": "LED 與光電元件（照明、顯示、車用、感測）。",
    "功率半導體": "處理大電流、高電壓的晶片（MOSFET、IGBT、碳化矽、氮化鎵）。電動車、充電樁、AI 伺服器電源都要用。",
    "光學鏡頭": "手機、車用、監控鏡頭與光學元件。看手機鏡頭規格升級、車用鏡頭搭載數量。",
    "上曜": "盤面上常一起漲跌的一群中小型股（以上曜為首），多半是題材連動、籌碼驅動，波動大。",
    "金融股": "銀行、壽險、證券與金控。看利率、股市成交量與投資收益；股息相對穩定。",
    "航運": "貨櫃與散裝航運。看運價指數（貨櫃看 SCFI、散裝看 BDI）、紅海等航線事件與全球貿易量。",
    "空運": "航空公司。看客運旅遊需求、油價與航空貨運量。",
    "散裝": "散裝航運：載鐵礦、煤、穀物等大宗物資。看 BDI 指數與中國原物料需求。",
    "聯電股": "聯電集團（聯電、世界先進、矽統）：成熟製程晶圓代工。看成熟製程報價與產能利用率。",
    "鴻家軍": "鴻海集團相關公司：伺服器、電子代工與零組件。跟鴻海的 AI 伺服器、電動車進度連動。",
    "台塑四寶": "台塑集團石化股。看油價、石化產品利差與中國需求，景氣循環股。",
    "AI": "AI 伺服器組裝代工（廣達、緯創、英業達、技嘉、微星）。看輝達新平台出貨與雲端大廠資本支出。",
    "彬彬": "一小群常一起漲跌的股票（以彬台為首），題材連動。",
    "IP": "矽智財與 IC 設計服務：幫客戶設計晶片、授權電路設計。AI 客製化晶片（ASIC）是主要成長動能。",
    "AI眼鏡": "智慧眼鏡的零組件：光學、晶片、軟板、聲學。看 Meta 等品牌智慧眼鏡出貨。",
    "面板": "液晶面板。看面板報價、電視與筆電需求，也在轉型做車用顯示、先進封裝。",
    "扇形封裝": "扇出型面板級封裝（FOPLP）設備與材料：用方形大面板做先進封裝，產能更大、成本更低。",
    "千元": "股價 1000 元以上的高價股（俗稱千金股），多是各產業龍頭或高成長股，波動也大。",
    "太陽能": "太陽能電池、模組與材料。看政府綠能政策、模組價格。",
    "電零組": "連接器、電源供應器、線材等電子零組件，跟伺服器、網通、車用出貨連動。",
    "小光電": "監控攝影機、影像辨識等安控產品與光電小型股。",
    "鋼鐵": "鋼鐵與不鏽鋼。看中國鋼價、原料（鐵礦砂、鎳）價格與基礎建設需求。",
    "機殼": "伺服器與電腦機殼、滑軌。AI 伺服器出貨帶動，規格越大單價越高。",
    "資訊": "軟體與資訊服務：系統整合、雲端代理、資安。接政府與企業數位轉型的案子。",
    "電纜": "電線電纜與銅材，跟銅價、電網建設連動。",
    "電腦周邊": "電腦周邊、電源、機殼與散熱，跟電競、DIY 市場連動。",
    "便宜電腦": "主機板、顯示卡的中小型品牌。看顯卡與 AI PC 換機潮，股價基期相對低。",
    "電通": "電子零組件通路商：幫原廠把晶片賣給客戶。營收大、毛利薄，看 AI 晶片出貨量。",
    "口罩": "防疫概念：口罩、疫苗、防護用品。有疫情消息時才比較會動。",
    "汽車零": "汽車零組件（車燈、保險桿、鈑金、輪圈），多做美國售後維修市場。看美國車市與關稅。",
}

# 官方產業別白話（族群外的股票用）
INDUSTRY_INTROS: dict[str, str] = {
    "半導體業": "做晶片的整條產業鏈：IC 設計、晶圓代工、封裝測試與相關設備材料。",
    "電子零組件業": "電子產品裡的零件：電路板、連接器、被動元件、電源、機構件等。",
    "電腦及週邊設備業": "電腦、伺服器、主機板與周邊設備的品牌與代工。",
    "光電業": "面板、LED、光學鏡頭、太陽能等跟「光」有關的元件與產品。",
    "通信網路業": "手機、網通設備、交換器、射頻元件與電信服務。",
    "電子通路業": "電子零組件的經銷商：幫原廠把晶片、零件賣給製造商。",
    "資訊服務業": "軟體、系統整合、雲端與資安服務。",
    "其他電子業": "不屬於上面幾類的電子相關公司，例如電子代工服務、量測設備。",
    "生技醫療業": "藥品、醫材、檢測與醫療服務；新藥股常跟著臨床試驗消息大漲大跌。",
    "化學生技醫療": "化學品與生技醫療公司。",
    "金融保險": "銀行、壽險、產險、證券與金控。看利率與股市。",
    "金融業": "銀行、保險、證券等金融公司。",
    "建材營造": "蓋房子的建設公司與營造廠，看房市景氣、利率與政府打房政策。",
    "航運業": "海運、空運與物流。看運價與全球貿易量。",
    "鋼鐵工業": "鋼鐵與金屬製品，看國際鋼價、原料價格與基建需求。",
    "化學工業": "化學原料與化學品，跟油價、景氣循環連動。",
    "塑膠工業": "塑膠原料（石化）與塑膠製品，看油價與石化利差。",
    "紡織纖維": "紡織、成衣、機能布與化纖，看品牌客戶訂單與匯率。",
    "食品工業": "食品、飲料、飼料與油脂，景氣防禦型、波動相對小。",
    "汽車工業": "汽車、機車與車用零組件。",
    "電機機械": "機械設備、工具機、自動化與馬達。",
    "電器電纜": "電線電纜與電器，跟銅價、電網建設連動。",
    "水泥工業": "水泥與預拌混凝土，看營建需求與中國市場。",
    "造紙工業": "紙漿與紙製品，看紙漿價格與包材需求。",
    "橡膠工業": "輪胎與橡膠製品，看橡膠原料價格與車市。",
    "玻璃陶瓷": "玻璃與陶瓷製品。",
    "油電燃氣業": "加油站、天然氣與電力相關公司，營收穩、看政策與油價。",
    "觀光餐旅": "飯店、餐廳與旅遊，看觀光人潮與消費景氣。",
    "觀光事業": "飯店、餐廳與旅遊，看觀光人潮與消費景氣。",
    "貿易百貨": "百貨、量販、超商與貿易商，看國內消費。",
    "文化創意業": "遊戲、影視、出版等內容產業。",
    "綠能環保": "再生能源、節能與環保處理公司，看政府綠能政策。",
    "數位雲端": "雲端服務、電商與數位平台。",
    "運動休閒": "運動用品、自行車、健身器材與休閒產業。",
    "居家生活": "家具、家電、居家用品。",
    "農業科技": "農業、畜牧與農業科技。",
    "電子商務業": "網路購物與電商平台。",
    "電子工業": "電子相關產業的公司。",
    "其他": "不屬於特定分類的公司，業務看個別公司。",
}

_SKIP_INDUSTRY = {"ETF", "上櫃ETF", "上櫃指數股票型基金(ETF)", "ETN", "指數投資證券(ETN)", "Index", "大盤", "所有證券", "存託憑證", "受益證券",
                  "創新板股票", "創新版股票"}


def _schema(connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS stock_profile_cache (key TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, payload TEXT NOT NULL)")


def _now() -> datetime:
    return datetime.now(TW_TZ)


def _default_fetcher(params: dict[str, str]) -> Any:
    def call(extra: dict[str, str]) -> Any:
        url = FINMIND_URL + "?" + urllib.parse.urlencode({**params, **extra})
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)"})
        with urllib.request.urlopen(request, timeout=40) as response:  # noqa: S310
            return json.load(response)

    try:
        return call({})
    except Exception:  # noqa: BLE001
        token = os.getenv("FINMIND_TOKEN", "").strip()
        if not token:
            raise
        return call({"token": token})


def _cache_get(key: str, ttl_days: int, now: datetime) -> Any | None:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT fetched_at, payload FROM stock_profile_cache WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    try:
        fresh = datetime.fromisoformat(row["fetched_at"]) >= now - timedelta(days=ttl_days)
    except ValueError:
        fresh = False
    data = json.loads(row["payload"])
    return data if fresh else {"__stale__": data}


def _cache_put(key: str, payload: Any, now: datetime) -> None:
    with get_connection() as connection:
        _schema(connection)
        connection.execute("INSERT INTO stock_profile_cache (key, fetched_at, payload) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                           "fetched_at = excluded.fetched_at, payload = excluded.payload",
                           (key, now.isoformat(timespec="seconds"), json.dumps(payload, ensure_ascii=False)))


def _cached(key: str, ttl_days: int, build: Callable[[], Any], now: datetime) -> tuple[Any, str | None]:
    """有新鮮的就用；過期或沒有就抓，抓失敗就用舊的。回 (資料, 錯誤)。"""
    hit = _cache_get(key, ttl_days, now)
    if hit is not None and "__stale__" not in (hit if isinstance(hit, dict) else {}):
        return hit, None
    try:
        data = build()
        _cache_put(key, data, now)
        return data, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("stock profile fetch %s failed: %s", key, exc)
        return (hit or {}).get("__stale__") if isinstance(hit, dict) else None, f"{type(exc).__name__}: {exc}"[:200]


def parse_info(payload: Any) -> dict[str, dict[str, str]]:
    """TaiwanStockInfo → {代號: {name, industry, market}}；同一檔有好幾列時，第一個不是 ETF／指數類的產業別為準。"""
    out: dict[str, dict[str, str]] = {}
    for r in (payload or {}).get("data") or []:
        code, ind = str(r.get("stock_id") or "").strip().upper(), str(r.get("industry_category") or "").strip()
        if not code or not ind or ind in _SKIP_INDUSTRY:
            continue
        item = out.setdefault(code, {"name": str(r.get("stock_name") or ""), "industry": ind, "market": "TSE" if r.get("type") == "twse" else "OTC"})
        if item["industry"] in ("電子工業", "其他") and ind not in ("電子工業", "其他"):
            item["industry"] = ind
    return out


def parse_financials(payload: Any) -> list[dict[str, Any]]:
    """TaiwanStockFinancialStatements（單季）→ 每季一列，新到舊，含年增。"""
    by_date: dict[str, dict[str, float]] = {}
    for r in (payload or {}).get("data") or []:
        if r.get("date") and r.get("type") and r.get("value") is not None:
            by_date.setdefault(str(r["date"]), {})[str(r["type"])] = float(r["value"])
    rows = []
    for d in sorted(by_date):
        v = by_date[d]
        rev = v.get("Revenue")
        net = v.get("EquityAttributableToOwnersOfParent", v.get("IncomeAfterTaxes"))

        def margin(x: float | None) -> float | None:
            return round(x / rev * 100, 2) if x is not None and rev else None

        q = (int(d[5:7]) - 1) // 3 + 1
        rows.append({"date": d, "label": f"{int(d[:4])}Q{q}", "revenue": round(rev / 1e8, 2) if rev is not None else None,
                     "gross": margin(v.get("GrossProfit")), "op": margin(v.get("OperatingIncome")), "net": margin(net),
                     "netIncome": round(net / 1e8, 2) if net is not None else None, "eps": v.get("EPS")})
    for i, r in enumerate(rows):
        prev = next((p for p in rows[:i] if p["date"][5:] == r["date"][5:] and int(p["date"][:4]) == int(r["date"][:4]) - 1), None)
        r["revYoY"] = round((r["revenue"] / prev["revenue"] - 1) * 100, 1) if prev and r["revenue"] and prev["revenue"] else None
        r["epsYoY"], r["epsTurn"] = None, None
        if prev and r["eps"] is not None and prev["eps"] is not None:
            if prev["eps"] <= 0 < r["eps"]:
                r["epsTurn"] = "轉盈"
            elif r["eps"] <= 0 < prev["eps"]:
                r["epsTurn"] = "轉虧"
            elif abs(prev["eps"]) >= EPS_BASE_MIN:       # 去年同季 EPS 太小，百分比沒意義
                r["epsYoY"] = round((r["eps"] - prev["eps"]) / abs(prev["eps"]) * 100, 1)
    return list(reversed(rows))


def _latest_close(code: str) -> tuple[str | None, float | None]:
    with get_connection() as connection:
        row = connection.execute("SELECT bar_time, close FROM bars_1d WHERE stock_code = ? AND close > 0 ORDER BY bar_time DESC LIMIT 1", (code,)).fetchone()
    return (str(row["bar_time"])[:10], float(row["close"])) if row else (None, None)


def profile(code: str, *, fetcher: Callable[[dict[str, str]], Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    from stock_groups import STOCK_GROUPS

    code = str(code or "").strip().upper()
    now = now or _now()
    call = fetcher or _default_fetcher
    errors: list[str] = []
    info, err = _cached("info", INFO_TTL_DAYS, lambda: parse_info(call({"dataset": "TaiwanStockInfo"})), now)
    if err:
        errors.append("產業別：" + err)
    info = info or {}
    me = info.get(code) or {}
    groups = [name for name, members in STOCK_GROUPS.items() if name != "股期標的" and any(str(c) == code for c, _ in members)]
    name = me.get("name") or next((n for g in groups for c, n in STOCK_GROUPS[g] if str(c) == code), None)
    intro_groups = [{"group": g, "text": GROUP_INTROS.get(g, ""), "members": [{"code": str(c), "name": n} for c, n in STOCK_GROUPS[g] if str(c) != code]}
                    for g in groups]
    industry = me.get("industry")
    peers = [{"code": c, "name": v["name"]} for c, v in sorted(info.items()) if industry and v["industry"] == industry and c != code
             and c.isdigit() and len(c) == 4]
    in_groups = {m["code"] for g in intro_groups for m in g["members"]}
    peers = [p for p in peers if p["code"] not in in_groups][:PEERS_MAX]

    start = f"{now.year - 3}-01-01"
    fin, err = _cached(f"fin:{code}", FIN_TTL_DAYS,
                       lambda: parse_financials(call({"dataset": "TaiwanStockFinancialStatements", "data_id": code, "start_date": start})), now)
    if err:
        errors.append("季報：" + err)
    quarters = (fin or [])[:QUARTERS]
    ttm = round(sum(q["eps"] for q in quarters[:4]), 2) if len(quarters) >= 4 and all(q["eps"] is not None for q in quarters[:4]) else None
    close_date, close = _latest_close(code)
    pe = round(close / ttm, 1) if close and ttm and ttm > 0 else None
    return {
        "status": "ok" if (me or groups or quarters) else "missing", "code": code, "name": name, "market": me.get("market"),
        "industry": industry, "industryText": INDUSTRY_INTROS.get(industry or "", ""), "groups": intro_groups, "peers": peers,
        "quarters": quarters, "ttmEps": ttm, "close": close, "closeDate": close_date, "pe": pe, "errors": errors,
        "source": "族群介紹為本站整理；官方產業別、季報來自 FinMind 公開資料（公開資訊觀測站財報），季報每季公布後更新",
    }
