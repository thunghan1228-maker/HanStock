"""國際大事行事曆（2026-10-10 使用者：照莊爸 zhuang.tw/calendar 做，含名詞小學堂）。

資料：TradingView 公開經濟日曆（economic-calendar.tradingview.com，網站小工具用的那支），美國／中國／日本／歐元區，
有預期值、前值，公布後有公布值；時間一律換成台灣時間。挑會影響台股的重要事件，翻成中文、標星等（★★★ 大事）；
同一時間公布的同一份報告（例如 CPI 年增、月增、核心 CPI）合成一筆，底下列各指標的前值／預期／公布。
另外加上台灣自己的固定行程：台指期每月結算（第三個星期三）、美股季度結算（3／6／9／12 月第三個星期五），
以及幾個公司法說／財報（慣例時間，待公司公告確認）。每小時更新一次；抓不到就沿用上一次存的。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))
TV_URL = "https://economic-calendar.tradingview.com/events"
COUNTRIES = ("US", "CN", "JP", "EU")
BACK_DAYS = 7          # 往回留幾天（看公布值）
AHEAD_DAYS = 42        # 往後抓幾天（約六週）
POLL_SECONDS = 60 * 60

# 一份報告＝一筆事件：群組 → (中文名, 星等, 小學堂鍵)
GROUPS: dict[str, tuple[str, int, str]] = {
    "us_jobs": ("美 非農就業報告", 3, "nfp"),
    "us_adp": ("美 ADP 就業（小非農）", 1, "adp"),
    "us_claims": ("美 初領失業金人數", 1, "claims"),
    "us_jolts": ("美 JOLTS 職缺", 2, "jolts"),
    "us_cpi": ("美 消費者物價指數 CPI", 3, "cpi"),
    "us_ppi": ("美 生產者物價指數 PPI", 2, "ppi"),
    "us_pce": ("美 PCE 物價／個人所得支出", 2, "pce"),
    "us_retail": ("美 零售銷售", 2, "retail"),
    "us_ism_m": ("美 ISM 製造業指數", 2, "ism_m"),
    "us_ism_s": ("美 ISM 服務業指數", 2, "ism_s"),
    "us_gdp": ("美 GDP", 2, "gdp"),
    "us_umich": ("美 密大消費者信心", 1, "umich"),
    "us_durable": ("美 耐久財訂單", 1, "durable"),
    "us_home": ("美 成屋銷售", 1, "housing"),
    "us_starts": ("美 新屋開工／營建許可", 1, "housing"),
    "fomc": ("美 聯準會利率決議", 3, "fomc"),
    "fomc_minutes": ("美 聯準會會議紀要", 2, "minutes"),
    "fed_chair": ("美 聯準會主席談話", 2, "fedchair"),
    "us_midterm": ("美 期中選舉", 3, "election"),
    "cn_gdp": ("中 GDP", 3, "cn_data"),
    "cn_activity": ("中 工業生產／零售／投資", 1, "cn_data"),
    "cn_cpi": ("中 CPI／PPI", 1, "cpi"),
    "cn_trade": ("中 進出口", 2, "cn_trade"),
    "cn_pmi": ("中 官方 PMI", 2, "cn_pmi"),
    "cn_lpr": ("中 貸款市場報價利率 LPR", 1, "lpr"),
    "cn_plenum": ("中 中共全會", 2, "plenum"),
    "boj": ("日 日銀利率決議", 3, "boj"),
    "boj_gov": ("日 日銀總裁談話", 1, "boj"),
    "jp_cpi": ("日 消費者物價 CPI", 1, "cpi"),
    "tankan": ("日 日銀短觀", 2, "tankan"),
    "ecb": ("歐 歐洲央行利率決議", 2, "ecb"),
    "eu_cpi": ("歐 歐元區 CPI", 1, "cpi"),
    "eu_gdp": ("歐 歐元區 GDP", 1, "gdp"),
}

# (國家, TradingView 標題, 群組, 指標名, 數字格式)；標題結尾「*」＝用開頭比對（例如 GDP Growth Rate QoQ Adv / Final）。
# 指標名開頭「@」＝附註（例如記者會），掛在同一天同群組那筆的說明裡；指標名 None＝本身就是一筆事件、沒有數字。
# 數字格式：% 百分比、k 千→萬（就業人數）、m 百萬→萬、usd_b 十億美元→億美元、None 照原樣（或依 TradingView 單位）。
CATALOG: list[tuple[str, str, str, str | None, str | None]] = [
    ("US", "Non Farm Payrolls", "us_jobs", "非農新增就業", "k"),
    ("US", "Unemployment Rate", "us_jobs", "失業率", "%"),
    ("US", "Average Hourly Earnings MoM", "us_jobs", "平均時薪月增", "%"),
    ("US", "Average Hourly Earnings YoY", "us_jobs", "平均時薪年增", "%"),
    ("US", "ADP Employment Change", "us_adp", "ADP 新增就業", "k"),
    ("US", "Initial Jobless Claims", "us_claims", "初領失業金", "k"),
    ("US", "Continuing Jobless Claims", "us_claims", "續領失業金", "k"),
    ("US", "JOLTs Job Openings", "us_jolts", "職缺數", "m"),
    ("US", "Inflation Rate YoY", "us_cpi", "CPI 年增率", "%"),
    ("US", "Inflation Rate MoM", "us_cpi", "CPI 月增率", "%"),
    ("US", "Core Inflation Rate YoY", "us_cpi", "核心 CPI 年增率", "%"),
    ("US", "Core Inflation Rate MoM", "us_cpi", "核心 CPI 月增率", "%"),
    ("US", "PPI MoM", "us_ppi", "PPI 月增率", "%"),
    ("US", "PPI YoY", "us_ppi", "PPI 年增率", "%"),
    ("US", "Core PPI MoM", "us_ppi", "核心 PPI 月增率", "%"),
    ("US", "PCE Price Index YoY", "us_pce", "PCE 年增率", "%"),
    ("US", "Core PCE Price Index YoY", "us_pce", "核心 PCE 年增率", "%"),
    ("US", "Core PCE Price Index MoM", "us_pce", "核心 PCE 月增率", "%"),
    ("US", "Personal Income MoM", "us_pce", "個人所得月增", "%"),
    ("US", "Personal Spending MoM", "us_pce", "個人支出月增", "%"),
    ("US", "Retail Sales MoM", "us_retail", "零售銷售月增", "%"),
    ("US", "Retail Sales Ex Autos MoM", "us_retail", "扣除汽車月增", "%"),
    ("US", "ISM Manufacturing PMI", "us_ism_m", "製造業指數", None),
    ("US", "ISM Manufacturing New Orders", "us_ism_m", "新訂單", None),
    ("US", "ISM Manufacturing Prices", "us_ism_m", "物價", None),
    ("US", "ISM Services PMI", "us_ism_s", "服務業指數", None),
    ("US", "GDP Growth Rate QoQ*", "us_gdp", "GDP 季增年率", "%"),
    ("US", "Michigan Consumer Sentiment*", "us_umich", "消費者信心", None),
    ("US", "Michigan Inflation Expectations*", "us_umich", "一年通膨預期", "%"),
    ("US", "Durable Goods Orders MoM", "us_durable", "耐久財訂單月增", "%"),
    ("US", "Existing Home Sales", "us_home", "成屋銷售（年化）", "m"),
    ("US", "Housing Starts", "us_starts", "新屋開工（年化）", "m"),
    ("US", "Building Permits Prel", "us_starts", "營建許可（年化）", "m"),
    ("US", "Fed Interest Rate Decision", "fomc", "聯邦基金利率上限", "%"),
    ("US", "Fed Press Conference", "fomc", "@主席記者會", None),
    ("US", "FOMC Minutes", "fomc_minutes", None, None),
    ("US", "Fed Chair*", "fed_chair", None, None),
    ("US", "Midterm Elections", "us_midterm", None, None),
    ("CN", "GDP Growth Rate YoY", "cn_gdp", "GDP 年增率", "%"),
    ("CN", "GDP Growth Rate QoQ", "cn_gdp", "GDP 季增率", "%"),
    ("CN", "Industrial Production YoY", "cn_activity", "工業生產年增", "%"),
    ("CN", "Retail Sales YoY", "cn_activity", "社會消費品零售年增", "%"),
    ("CN", "Fixed Asset Investment (YTD) YoY", "cn_activity", "固定資產投資年增（累計）", "%"),
    ("CN", "Inflation Rate YoY", "cn_cpi", "CPI 年增率", "%"),
    ("CN", "PPI YoY", "cn_cpi", "PPI 年增率", "%"),
    ("CN", "Exports YoY", "cn_trade", "出口年增", "%"),
    ("CN", "Imports YoY", "cn_trade", "進口年增", "%"),
    ("CN", "Balance of Trade", "cn_trade", "貿易順差", "usd_b"),
    ("CN", "NBS Manufacturing PMI", "cn_pmi", "製造業 PMI", None),
    ("CN", "NBS Non Manufacturing PMI", "cn_pmi", "非製造業 PMI", None),
    ("CN", "Loan Prime Rate 1Y", "cn_lpr", "1 年期", "%"),
    ("CN", "Loan Prime Rate 5Y", "cn_lpr", "5 年期", "%"),
    ("CN", "Communist Party*", "cn_plenum", None, None),
    ("JP", "BoJ Interest Rate Decision", "boj", "政策利率", "%"),
    ("JP", "BoJ Gov*", "boj_gov", None, None),
    ("JP", "Inflation Rate YoY", "jp_cpi", "CPI 年增率", "%"),
    ("JP", "Core Inflation Rate YoY", "jp_cpi", "核心 CPI 年增率", "%"),
    ("JP", "Tankan Large Manufacturers Index", "tankan", "大型製造業", None),
    ("JP", "Tankan Large Non-Manufacturers Index", "tankan", "大型非製造業", None),
    ("EU", "ECB Interest Rate Decision", "ecb", "主要再融資利率", "%"),
    ("EU", "Deposit Facility Rate", "ecb", "存款利率", "%"),
    ("EU", "ECB Press Conference", "ecb", "@總裁記者會", None),
    ("EU", "Inflation Rate YoY Flash", "eu_cpi", "CPI 年增率", "%"),
    ("EU", "Core Inflation Rate YoY Flash", "eu_cpi", "核心 CPI 年增率", "%"),
    ("EU", "GDP Growth Rate QoQ Flash", "eu_gdp", "GDP 季增率", "%"),
    ("EU", "GDP Growth Rate YoY Flash", "eu_gdp", "GDP 年增率", "%"),
]

# 標題結尾 → 中文階段（初值、終值…），只標在這幾個群組的名稱上
STAGE_GROUPS = {"us_gdp", "us_umich", "eu_cpi", "eu_gdp"}
STAGES = (("Adv", "初值"), ("Prel", "初值"), ("Flash", "初值"), ("2nd Est", "第二次估計"), ("3rd Est", "終值"), ("Final", "終值"))
MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6, "Jul": 7, "Aug": 8, "Sep": 9, "Sept": 9, "Oct": 10, "Nov": 11, "Dec": 12}

# 事件列上的附加說明（固定的）
EVENT_NOTES: dict[str, str] = {
    "us_midterm": "開票結果約在台灣時間隔天上午起陸續出來",
    "cn_plenum": "會期內每天都可能有政策消息，會後發公報",
}

# 小學堂：分類 → 名詞卡片。who 誰公布、when 什麼時候（台灣時間，括號為美國冬令時間）、read 怎麼讀、why 為什麼要看
GLOSSARY_CATS: list[tuple[str, str, str]] = [
    ("jobs", "👷", "就業"), ("prices", "🛒", "物價"), ("growth", "🏭", "景氣"), ("cb", "🏛", "央行"),
    ("abroad", "🌏", "中國・日本・歐洲"), ("politics", "🗳", "政治"), ("tw", "🏢", "公司與台股"), ("basics", "✏️", "基本功"),
]
GLOSSARY: dict[str, dict[str, str]] = {
    "nfp": {"cat": "jobs", "term": "非農就業", "en": "Nonfarm Payrolls・NFP",
            "gist": "美國上個月扣掉農業以外，新增（或減少）了多少個工作。",
            "who": "美國勞工統計局（BLS）",
            "when": "每月一次，多半是月初第一個星期五；台灣時間 20:30（冬令 21:30）",
            "read": "新增人數比預期多＝就業強；同一份報告裡還有失業率、平均時薪，三個數字要合起來看",
            "why": "就業太強→市場擔心聯準會晚降息；太弱→擔心景氣衰退。公布時台股已收盤，影響先反映在當晚美股、再帶到下個交易日的台股"},
    "unemp": {"cat": "jobs", "term": "失業率", "en": "Unemployment Rate",
              "gist": "想工作的人裡面，找不到工作的比例。",
              "who": "美國勞工統計局（BLS），跟非農同一份報告",
              "when": "跟非農同時公布",
              "read": "往上走＝就業轉弱。非農是問企業、失業率是問家庭，兩者偶爾方向不一樣",
              "why": "聯準會的任務是「物價穩定」加「充分就業」，失業率就是看後者最直接的數字"},
    "adp": {"cat": "jobs", "term": "ADP 就業（小非農）", "en": "ADP National Employment Report",
            "gist": "薪資處理公司 ADP 從客戶薪資資料算出來的民間就業人數變化。",
            "who": "ADP 公司",
            "when": "每月一次，通常在非農前兩天的星期三；台灣時間 20:15（冬令 21:15）",
            "read": "可以當非農的暖身，但算法不同，跟非農方向不一致很常見",
            "why": "比非農早出來，市場常用它先猜非農"},
    "claims": {"cat": "jobs", "term": "初領失業金人數", "en": "Initial Jobless Claims",
               "gist": "上一週第一次去申請失業救濟的人數。",
               "who": "美國勞工部",
               "when": "每週四；台灣時間 20:30（冬令 21:30）",
               "read": "人數上升＝被裁員的人變多。單週容易受假日、天災影響，看四週平均比較準",
               "why": "最即時的就業數據，就業市場轉弱通常最先在這裡看到"},
    "jolts": {"cat": "jobs", "term": "JOLTS 職缺", "en": "Job Openings and Labor Turnover Survey",
              "gist": "美國企業還有多少職缺開著沒補到人。",
              "who": "美國勞工統計局（BLS）",
              "when": "每月一次，大約月初（資料是前兩個月的）；台灣時間 22:00（冬令 23:00）",
              "read": "職缺多＝企業還在搶人、就業市場緊；一路往下＝徵才降溫",
              "why": "聯準會很看重職缺跟失業人數的比例，用來判斷薪資會不會繼續推高通膨"},
    "cpi": {"cat": "prices", "term": "消費者物價指數 CPI", "en": "Consumer Price Index",
            "gist": "一般人日常買東西、付房租的價格，比去年同期貴了多少——也就是常說的「通膨」。",
            "who": "美國勞工統計局（BLS）；中國是國家統計局、日本是總務省、歐元區是歐盟統計局",
            "when": "美國每月一次，大約月中；台灣時間 20:30（冬令 21:30）",
            "read": "最常看「年增率」。「核心 CPI」扣掉波動大的食物和能源，比較看得出通膨的底子",
            "why": "通膨降不下來，聯準會就不敢降息；利率預期一變，科技股的評價最敏感"},
    "ppi": {"cat": "prices", "term": "生產者物價指數 PPI", "en": "Producer Price Index",
            "gist": "工廠、批發商賣出去的價格變化，可以想成「上游的通膨」。",
            "who": "美國勞工統計局（BLS）",
            "when": "每月一次，通常跟 CPI 差一兩天；台灣時間 20:30（冬令 21:30）",
            "read": "上游漲價之後可能轉嫁到消費者，所以常被當成 CPI 的前哨",
            "why": "重要性比 CPI 低一級，但兩個數字方向一致時，市場反應會放大"},
    "pce": {"cat": "prices", "term": "PCE 物價指數", "en": "Personal Consumption Expenditures Price Index",
            "gist": "另一種算通膨的方法，也是聯準會自己最看重的那一個。",
            "who": "美國經濟分析局（BEA），跟個人所得、個人支出同一份報告",
            "when": "每月一次，通常月底；台灣時間 20:30（冬令 21:30）",
            "read": "聯準會說的「2% 通膨目標」指的就是 PCE 年增率；涵蓋範圍比 CPI 廣，權重會跟著消費習慣調整",
            "why": "這是聯準會訂目標用的指標，離 2% 多遠直接影響市場對降息的預期"},
    "retail": {"cat": "growth", "term": "零售銷售", "en": "Retail Sales",
               "gist": "美國商店和網路上個月總共賣了多少錢，比前一個月多還是少。",
               "who": "美國商務部普查局",
               "when": "每月一次，大約月中；台灣時間 20:30（冬令 21:30）",
               "read": "看「月增率」。數字是金額、沒有扣掉物價上漲，所以要跟通膨一起看",
               "why": "消費大約佔美國經濟的三分之二，美國人還敢不敢花錢是景氣的關鍵"},
    "ism_m": {"cat": "growth", "term": "ISM 製造業指數", "en": "ISM Manufacturing PMI",
              "gist": "問美國製造業的採購經理「這個月比上個月好還是差」做出來的景氣指數。",
              "who": "美國供應管理協會（ISM）",
              "when": "每月第 1 個營業日；台灣時間 22:00（冬令 23:00）",
              "read": "50 是分水嶺：高於 50＝製造業擴張、低於 50＝收縮。細項的「新訂單」「物價」常被單獨拿出來看",
              "why": "每個月最早出爐的重要景氣數據；台灣以電子出口為主，美國製造業的冷熱跟訂單息息相關"},
    "ism_s": {"cat": "growth", "term": "ISM 服務業指數", "en": "ISM Services PMI",
              "gist": "同一套問法，問的是服務業（餐飲、金融、運輸、醫療…）。",
              "who": "美國供應管理協會（ISM）",
              "when": "每月第 3 個營業日；台灣時間 22:00（冬令 23:00）",
              "read": "一樣以 50 為分水嶺",
              "why": "服務業佔美國經濟的大部分，這個數字更能代表美國整體景氣"},
    "gdp": {"cat": "growth", "term": "GDP", "en": "Gross Domestic Product",
            "gist": "一段期間內整個經濟生產了多少東西，看經濟成長多快。",
            "who": "美國經濟分析局（BEA）；歐元區是歐盟統計局",
            "when": "美國每季結束後約一個月出初值（1、4、7、10 月底），之後還有修正值、終值；台灣時間 20:30（冬令 21:30）",
            "read": "美國公布的是「季增年率」＝把這一季的成長換算成一整年的速度。三次估計裡，初值最受注意",
            "why": "經濟的總成績單；連續兩季負成長常被稱為「技術性衰退」"},
    "umich": {"cat": "growth", "term": "密大消費者信心", "en": "University of Michigan Consumer Sentiment",
              "gist": "密西根大學問美國民眾「覺得現在和未來的經濟好不好」。",
              "who": "密西根大學",
              "when": "每月兩次：月中初值、月底終值；台灣時間 22:00（冬令 23:00）",
              "read": "數字越高越樂觀。報告裡的「一年通膨預期」也很受聯準會重視",
              "why": "消費者有信心才會花錢；通膨預期一升高，聯準會就更難降息"},
    "durable": {"cat": "growth", "term": "耐久財訂單", "en": "Durable Goods Orders",
                "gist": "用得比較久的東西（車、機械、飛機）上個月接了多少新訂單。",
                "who": "美國商務部普查局",
                "when": "每月一次，大約月底；台灣時間 20:30（冬令 21:30）",
                "read": "飛機訂單一筆就很大，常把數字拉得忽上忽下，看「扣除運輸」比較穩",
                "why": "反映企業願不願意花錢投資設備"},
    "housing": {"cat": "growth", "term": "房市數據", "en": "Existing Home Sales・Housing Starts",
                "gist": "成屋賣了多少間、新蓋了多少間、申請了多少建照。",
                "who": "全美不動產經紀人協會（成屋銷售）、美國商務部普查局（新屋開工、營建許可）",
                "when": "每月一次；新屋開工 20:30、成屋銷售 22:00（冬令各晚一小時）",
                "read": "數字是「年化」後的戶數；房市對利率最敏感",
                "why": "看高利率把房市壓得多冷，間接影響消費與營建相關需求"},
    "fomc": {"cat": "cb", "term": "聯準會利率決議", "en": "FOMC Meeting",
             "gist": "美國的中央銀行（聯準會）開會決定利率要升、要降，還是不動。",
             "who": "聯邦公開市場委員會（FOMC）",
             "when": "一年 8 次，每次開兩天；結果在台灣時間隔天凌晨 02:00（冬令 03:00），半小時後主席開記者會",
             "read": "除了利率本身，聲明的用字和主席在記者會上的說法更重要。3、6、9、12 月的會議還會公布「點陣圖」",
             "why": "美元利率是全球資金的價格，這是所有國際大事裡影響範圍最廣的一個"},
    "minutes": {"cat": "cb", "term": "聯準會會議紀要", "en": "FOMC Minutes",
                "gist": "上一次利率會議裡，官員們實際討論了什麼的詳細紀錄。",
                "who": "聯準會",
                "when": "每次會議結束後三週；台灣時間凌晨 02:00（冬令 03:00）",
                "read": "看官員之間意見有多分歧、有多少人傾向降息或升息",
                "why": "可以補上決議聲明沒寫出來的細節，有時會改變市場對下一次會議的預期"},
    "dotplot": {"cat": "cb", "term": "點陣圖", "en": "Dot Plot",
                "gist": "每位聯準會官員各自預測未來幾年利率會在哪裡，畫成一張點點圖。",
                "who": "聯準會",
                "when": "每年 3、6、9、12 月的利率會議同時公布",
                "read": "看中間那個點（中位數）在哪，就知道官員們預期今年、明年大概會降息或升息幾次",
                "why": "這是聯準會對未來利率最直接的表態"},
    "fedchair": {"cat": "cb", "term": "聯準會主席談話", "en": "Fed Chair Speech",
                 "gist": "聯準會主席在國會、研討會或公開活動上的發言。",
                 "who": "聯準會主席",
                 "when": "不定期",
                 "read": "注意有沒有說出跟上次決議不一樣的方向，例如暗示下次會降息",
                 "why": "主席的一句話就可能改變市場對利率的預期"},
    "boj": {"cat": "abroad", "term": "日銀利率決議", "en": "BoJ Interest Rate Decision",
            "gist": "日本央行決定日本的利率。",
            "who": "日本銀行（日銀）",
            "when": "一年 8 次；結果多半在台灣時間中午前後出來，沒有固定時間，下午 13:30 總裁記者會",
            "read": "日本長年零利率，升息代表資金變貴、日圓走強",
            "why": "很多人借便宜的日圓去買全球資產（套利交易），日銀突然升息曾引發全球股市急跌"},
    "tankan": {"cat": "abroad", "term": "日銀短觀", "en": "Tankan Survey",
               "gist": "日本央行每季問企業「景氣好不好」。",
               "who": "日本銀行（日銀）",
               "when": "每季一次（4、7、10 月初，12 月中）；台灣時間 07:50",
               "read": "大型製造業指數最常被引用；正數＝覺得景氣好的企業比較多",
               "why": "日本製造業景氣跟台灣的電子、機械供應鏈關係密切"},
    "ecb": {"cat": "abroad", "term": "歐洲央行利率決議", "en": "ECB Interest Rate Decision",
            "gist": "歐元區的中央銀行決定利率。",
            "who": "歐洲中央銀行（ECB）",
            "when": "約六週一次；台灣時間 20:15（歐洲冬令 21:15），45 分鐘後總裁記者會",
            "read": "跟聯準會一樣，除了利率也要聽總裁對未來的說法",
            "why": "影響歐元、美元匯率，以及歐洲的需求"},
    "cn_data": {"cat": "abroad", "term": "中國 GDP 與月度經濟數據", "en": "China GDP・Industrial Production・Retail Sales",
                "gist": "中國經濟成長多快，以及工廠產出、消費、投資的月成績。",
                "who": "中國國家統計局",
                "when": "GDP 每季（1、4、7、10 月中）；工業生產、零售、固定資產投資每月中；台灣時間 10:00",
                "read": "看年增率有沒有達到官方目標；投資數字是「今年累計」",
                "why": "中國是台灣最大的出口市場，景氣好壞直接影響傳產、原物料與部分電子需求"},
    "cn_trade": {"cat": "abroad", "term": "中國進出口", "en": "China Trade Balance",
                 "gist": "中國上個月出口、進口比去年同期多還是少。",
                 "who": "中國海關總署",
                 "when": "每月一次，大約上旬到中旬；台灣時間上午 11:00 前後，沒有固定時間",
                 "read": "出口反映全球需求，進口反映中國內需",
                 "why": "可以看出全球（尤其電子）供應鏈的冷熱"},
    "cn_pmi": {"cat": "abroad", "term": "中國官方 PMI", "en": "NBS Manufacturing PMI",
               "gist": "中國統計局問企業採購經理景氣好壞做出來的指數。",
               "who": "中國國家統計局",
               "when": "每月最後一天；台灣時間 09:30",
               "read": "50 以上擴張、50 以下收縮",
               "why": "每月最早出來的中國景氣數字"},
    "lpr": {"cat": "abroad", "term": "貸款市場報價利率 LPR", "en": "Loan Prime Rate",
            "gist": "中國銀行放款的參考利率，實際上就是中國的基準利率。",
            "who": "中國人民銀行（全國銀行間同業拆借中心公布）",
            "when": "每月 20 日（遇假日順延）；台灣時間 09:15",
            "read": "1 年期影響企業貸款，5 年期影響房貸；調降＝人行在放寬資金",
            "why": "看中國有沒有加大力道救經濟"},
    "election": {"cat": "politics", "term": "美國期中選舉", "en": "US Midterm Elections",
                 "gist": "總統任期過一半時舉行的國會選舉。",
                 "who": "美國各州選務機關",
                 "when": "四年兩次中的一次（偶數年 11 月第一個星期一之後的星期二）",
                 "read": "看國會參眾兩院由哪一黨掌握，決定總統的政策推不推得動",
                 "why": "影響之後兩年的關稅、預算與產業政策，開票前後市場波動通常比較大"},
    "plenum": {"cat": "politics", "term": "中共全會", "en": "CPC Plenary Session",
               "gist": "中共中央委員會的全體會議。",
               "who": "中國共產黨中央委員會",
               "when": "不定期，一年一到兩次，通常連開四天",
               "read": "會後公報會定調經濟方向，例如五年規劃的重點產業",
               "why": "政策方向會影響中國相關的產業與兩岸情勢"},
    "call": {"cat": "tw", "term": "法說會", "en": "Investor Conference",
             "gist": "公司對投資法人說明上一季賺多少、接下來怎麼看的說明會。",
             "who": "各上市櫃公司",
             "when": "每季一次（台積電通常在 1、4、7、10 月中旬的星期四下午 14:00）",
             "read": "重點通常是三個：上一季成績、下一季財測、全年展望與資本支出",
             "why": "龍頭公司的展望會帶動整個產業鏈的氣氛，例如台積電法說之於半導體"},
    "earnings": {"cat": "tw", "term": "財報", "en": "Earnings",
                 "gist": "公司公布上一季的營收和獲利。",
                 "who": "各公司",
                 "when": "每季一次；美股集中在 1、4、7、10 月中旬起的幾週（俗稱財報季）",
                 "read": "市場看的是「有沒有比預期好」，以及公司對下一季的預估",
                 "why": "輝達、艾司摩爾、蘋果這類大廠是很多台灣公司的客戶或同業，財報會直接影響相關供應鏈"},
    "taifex": {"cat": "tw", "term": "台指期結算", "en": "TAIFEX Settlement",
               "gist": "台指期貨每個月到期、把合約了結的那一天。",
               "who": "臺灣期貨交易所",
               "when": "每月第三個星期三（遇休市順延）；結算價用當天收盤前 30 分鐘的加權指數平均計算",
               "read": "結算前後，期貨與選擇權的部位要了結或轉到下個月，盤中波動有時會比較大",
               "why": "權值股在結算日尾盤常有比較大的成交量"},
    "witching": {"cat": "tw", "term": "美股季度結算（四巫日）", "en": "Quadruple Witching",
                 "gist": "美股的股票與指數期貨、選擇權在同一天到期。",
                 "who": "美國各交易所",
                 "when": "3、6、9、12 月第三個星期五，美股收盤時（台灣隔天清晨）",
                 "read": "成交量會特別大，指數調整也常在這天生效",
                 "why": "美股尾盤的異常波動，可能影響下個交易日台股開盤"},
    "basics": {"cat": "basics", "term": "前值・預期・公布", "en": "Previous・Forecast・Actual",
               "gist": "每個數據公布時都會看到的三個數字。",
               "read": "前值＝上一次公布的數字；預期＝公布前市場分析師預估的平均；公布＝這次實際的數字",
               "why": "行情反應的通常不是數字好不好，而是「公布值跟預期差多少」。數字很好但市場早就預期到了，價格往往不太動；跟預期差很多才會大波動"},
    "dst": {"cat": "basics", "term": "為什麼有時 20:30、有時 21:30", "en": "Daylight Saving Time",
            "gist": "因為美國有夏令時間，台灣沒有。",
            "read": "美國每年 3 月第二個星期日到 11 月第一個星期日是夏令時間，這段期間美東跟台灣差 12 小時；其他時候是冬令時間，差 13 小時（歐洲是 3 月最後一個星期日到 10 月最後一個星期日）",
            "why": "所以同樣是美東早上 8:30 公布的數據，夏天在台灣是 20:30、冬天變成 21:30。這頁的時間都已經換算成台灣時間"},
}
GLOSSARY_EXTRA = ("unemp", "dotplot", "basics", "dst")   # 沒有直接對應事件，但小學堂一律列出

# 公司法說／財報：慣例時間，待公司公告確認（2026-10-10 照莊爸行事曆）
COMPANY_EVENTS: list[dict[str, Any]] = [
    {"date": "2026-10-14", "time": "13:00", "country": "EU", "zh": "歐 艾司摩爾 ASML 財報", "period": "Q3", "stars": 2, "key": "earnings",
     "note": "公布時間為慣例時間，待確認"},
    {"date": "2026-10-15", "time": "14:00", "country": "TW", "zh": "台 台積電法說會", "period": "Q3", "stars": 3, "key": "call",
     "note": "慣例時間，以公司公告為準"},
]

_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "lastFetch": None, "lastError": None, "count": 0}


def _schema(connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS macro_calendar (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")


def _now() -> datetime:
    return datetime.now(TW_TZ)


def _default_fetcher(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/",
                                                   "User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def classify(country: str, title: str) -> tuple[str, str | None, str | None] | None:
    """→ (群組, 指標名, 數字格式)；第一個符合的為準。"""
    for c, pattern, group, label, fmt in CATALOG:
        if c != country:
            continue
        if title == pattern or (pattern.endswith("*") and title.startswith(pattern[:-1])):
            return group, label, fmt
    return None


def _stage(title: str) -> str:
    for suffix, zh in STAGES:
        if title.endswith(" " + suffix):
            return zh
    return ""


def _period(text: Any) -> str:
    """Sep → 9月、Q3 → Q3、Oct/03 → 10/3 當週、20261014 → 10/14。"""
    text = str(text or "").strip()
    if not text:
        return ""
    if text in MONTHS:
        return f"{MONTHS[text]}月"
    if "/" in text:
        mon, _, day = text.partition("/")
        if mon in MONTHS and day.isdigit():
            return f"{MONTHS[mon]}/{int(day)} 當週"
    if len(text) == 8 and text.isdigit():
        return f"{int(text[4:6])}/{int(text[6:])}"
    return text


def _num(value: float, digits: int = 2) -> str:
    return f"{value:,.{digits}f}".rstrip("0").rstrip(".")


def _fmt(value: Any, fmt: str | None, unit: Any = None, scale: Any = None) -> str | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if fmt == "%" or (fmt is None and unit == "%"):
        return _num(number) + "%"
    if fmt == "k":                 # 千人 → 萬人（29 → 2.9 萬）
        return _num(number / 10) + " 萬"
    if fmt == "m":                 # 百萬 → 萬（7.079 → 707.9 萬）
        return _num(number * 100, 1) + " 萬"
    if fmt == "usd_b":             # 十億美元 → 億美元
        return _num(number * 10, 1) + " 億美元"
    if scale in ("K", "M", "B"):
        return _num(number) + {"K": " 千", "M": " 百萬", "B": " 十億"}[scale]
    return _num(number)


def _vs(actual: Any, forecast: Any) -> str | None:
    try:
        a, f = float(actual), float(forecast)
    except (TypeError, ValueError):
        return None
    if abs(a - f) < 1e-9:
        return "符合預期"
    return "高於預期" if a > f else "低於預期"


def _event(group: str, day: str, at: str, country: str, period: str) -> dict[str, Any]:
    zh, stars, key = GROUPS[group]
    return {"id": f"{group}-{day}-{at or 'allday'}", "date": day, "time": at, "country": country, "group": group, "zh": zh, "stars": stars,
            "key": key, "period": period, "indicators": [], "notes": [EVENT_NOTES[group]] if group in EVENT_NOTES else []}


def parse_events(payload: Any) -> list[dict[str, Any]]:
    """TradingView 回傳 → 我們要的事件（只留 CATALOG 裡的），時間換台灣時間，同時同報告合一筆。"""
    rows = (payload or {}).get("result") if isinstance(payload, dict) else None
    events: dict[tuple[str, str, str], dict[str, Any]] = {}
    notes: list[tuple[str, str, str, str, str]] = []      # (群組, 日期, 時間, 國家, 附註)
    for r in rows or []:
        country, title = str(r.get("country") or ""), str(r.get("title") or "")
        hit = classify(country, title)
        if not hit or not r.get("date"):
            continue
        group, label, fmt = hit
        try:
            utc = datetime.fromisoformat(str(r["date"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        local = utc.astimezone(TW_TZ)
        all_day = utc.hour == 0 and utc.minute == 0 and label is None      # 選舉、全會：整天的事
        day, at = (utc.date().isoformat(), "") if all_day else (local.date().isoformat(), local.strftime("%H:%M"))
        if label and label.startswith("@"):
            notes.append((group, day, at, country, label[1:]))
            continue
        event = events.setdefault((group, day, at), _event(group, day, at, country, ""))
        rank = next(i for i, c in enumerate(CATALOG) if c[2] == group and c[3] == label)
        if rank < event.get("_rank", 999):            # 期別跟著主指標（續領失業金比初領晚一週）
            event["_rank"], event["period"] = rank, _period(r.get("period"))
        stage = _stage(title) if group in STAGE_GROUPS else ""
        if stage and stage not in event["zh"]:
            event["zh"] += f"（{stage}）"
        if label:
            unit, scale = r.get("unit"), r.get("scale")
            actual, forecast = r.get("actual"), r.get("forecast")
            event["indicators"].append({"label": label, "actual": _fmt(actual, fmt, unit, scale), "forecast": _fmt(forecast, fmt, unit, scale),
                                        "previous": _fmt(r.get("previous"), fmt, unit, scale), "vs": _vs(actual, forecast)})
    for group, day, at, country, text in notes:
        host = next((e for (g, d, _), e in sorted(events.items()) if g == group and d == day), None)
        if host is not None:
            host["notes"].append(f"{at} {text}".strip())
        else:
            e = events.setdefault((group, day, at), _event(group, day, at, country, ""))
            e["zh"] += " " + text
    out = _merge_all_day(list(events.values()))
    order = {(group, label): i for i, (_, _, group, label, _) in enumerate(CATALOG)}
    for e in out:
        e.pop("_rank", None)
        e["indicators"].sort(key=lambda x: order.get((e["group"], x["label"]), 99))
        e["released"] = any(x["actual"] is not None for x in e["indicators"])
    out.sort(key=lambda e: (e["date"], e["time"] or "99:99", -e["stars"], e["zh"]))
    return out


def _merge_all_day(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """連續幾天的整天事件（例如全會開四天）合成一筆，記 until。"""
    out: list[dict[str, Any]] = []
    last: dict[str, dict[str, Any]] = {}
    for e in sorted(events, key=lambda e: (e["date"], e["time"])):
        prev = last.get(e["group"]) if not e["time"] else None
        if prev is not None and date.fromisoformat(prev.get("until") or prev["date"]) + timedelta(days=1) == date.fromisoformat(e["date"]):
            prev["until"] = e["date"]
            continue
        out.append(e)
        if not e["time"]:
            last[e["group"]] = e
    return out


def _third_weekday(year: int, month: int, weekday: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 14)


def _fixed(key: str, day: date, at: str, country: str, zh: str, stars: int, note: str = "", period: str = "") -> dict[str, Any]:
    return {"id": f"{key}-{day.isoformat()}", "date": day.isoformat(), "time": at, "country": country, "group": key, "zh": zh, "stars": stars,
            "key": key, "period": period, "indicators": [], "notes": [note] if note else [], "released": False}


def fixed_events(start: date, end: date) -> list[dict[str, Any]]:
    """台指期結算（每月第三個星期三 13:30）、美股季度結算（3／6／9／12 月第三個星期五，美股收盤＝台灣隔天清晨）、公司法說。"""
    out: list[dict[str, Any]] = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        settle = _third_weekday(y, m, 2)
        if start <= settle <= end:
            out.append(_fixed("taifex", settle, "13:30", "TW", f"台 台指期 {m} 月結算", 2))
        if m in (3, 6, 9, 12):
            witch = _third_weekday(y, m, 4)
            if start <= witch <= end:
                out.append(_fixed("witching", witch, "", "US", "美 季度結算（四巫日）", 2, "美股收盤時到期（台灣隔天清晨）"))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for e in COMPANY_EVENTS:
        day = date.fromisoformat(e["date"])
        if start <= day <= end:
            out.append(_fixed(e["key"], day, e["time"], e["country"], e["zh"], e["stars"], e.get("note", ""), e.get("period", "")))
            out[-1]["id"] = f"co-{e['date']}-{e['zh']}"
    return out


def fetch(*, fetcher: Callable[[str], Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    call = fetcher or _default_fetcher
    start = (now - timedelta(days=BACK_DAYS)).astimezone(timezone.utc)
    end = (now + timedelta(days=AHEAD_DAYS)).astimezone(timezone.utc)
    url = (f"{TV_URL}?from={start.strftime('%Y-%m-%dT00:00:00.000Z')}&to={end.strftime('%Y-%m-%dT00:00:00.000Z')}"
           f"&countries={','.join(COUNTRIES)}")
    events = parse_events(call(url))
    if not events:
        raise RuntimeError("經濟日曆抓到 0 筆")
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        connection.execute("INSERT INTO macro_calendar (key, value, updated_at) VALUES ('events', ?, ?) "
                           "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                           (json.dumps(events, ensure_ascii=False), now.isoformat(timespec="seconds")))
    with _lock:
        _state.update({"lastFetch": now.isoformat(timespec="seconds"), "lastError": None, "count": len(events)})
    return {"count": len(events), "at": now.isoformat(timespec="seconds")}


def glossary() -> list[dict[str, Any]]:
    """小學堂：依分類排好的名詞卡片。"""
    out = []
    for cat, icon, name in GLOSSARY_CATS:
        cards = [{"key": k, **{f: v for f, v in g.items() if f != "cat"}} for k, g in GLOSSARY.items() if g["cat"] == cat]
        if cards:
            out.append({"cat": cat, "icon": icon, "name": name, "cards": cards})
    return out


def calendar(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT value, updated_at FROM macro_calendar WHERE key = 'events'").fetchone()
    stored = json.loads(row["value"]) if row else []
    start, end = (now - timedelta(days=BACK_DAYS)).date(), (now + timedelta(days=AHEAD_DAYS)).date()
    events = [e for e in stored if start.isoformat() <= e["date"] <= end.isoformat()] + fixed_events(start, end)
    events.sort(key=lambda e: (e["date"], e["time"] or "99:99", -e["stars"], e["zh"]))
    stamp = (now.date().isoformat(), now.strftime("%H:%M"))
    upcoming = [e for e in events if e["stars"] >= 3 and (e["date"], e["time"] or "00:00") >= stamp]
    return {
        "status": "ok" if events else "missing", "updatedAt": row["updated_at"] if row else None, "now": now.isoformat(timespec="seconds"),
        "today": now.date().isoformat(), "from": start.isoformat(), "to": end.isoformat(), "events": events,
        "next": upcoming[0] if upcoming else None, "glossary": glossary(),
        "source": "TradingView 經濟日曆（美／中／日／歐）＋台指期結算、美股季度結算、公司法說（慣例時間）",
    }


def _loop() -> None:
    time.sleep(90)
    while True:
        try:
            fetch()
        except Exception as exc:  # noqa: BLE001
            logger.warning("macro calendar fetch failed: %s", exc)
            with _lock:
                _state["lastError"] = f"{type(exc).__name__}: {exc}"[:300]
        time.sleep(POLL_SECONDS)


def start_macro_calendar_collector() -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
    threading.Thread(target=_loop, name="macro-calendar", daemon=True).start()
    return True


def collector_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)
