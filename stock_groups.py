STOCK_GROUPS = {
    "記憶體": [
        ("8271", "宇瞻"),
        ("2344", "華邦電"),
        ("4973", "廣穎電通"),
        ("3260", "威剛"),
        ("8088", "品安"),
        ("3135", "凌航"),
        ("4967", "十銓"),
        ("2337", "旺宏"),
        ("6265", "方土昶"),
        ("2451", "創見"),
        ("5289", "宜鼎"),
        ("8110", "華東"),
        ("5351", "鈺創"),
        ("3006", "晶豪科"),
        ("3060", "銘異"),
        ("8299", "群聯"),
        ("2408", "南亞科"),
        ("8131", "福懋科"),
        ("6770", "力積電"),
    ],

    "D電腦": [
        ("6166", "凌華"),
        ("6206", "飛捷"),
        ("3022", "威強電"),
        ("3479", "安勤"),
        ("4916", "事欣科"),
        ("6414", "樺漢"),
        ("3213", "茂訊"),
        ("2395", "研華"),
        ("3594", "磐儀"),
    ],

    "AI": [
        ("3231", "緯創"),
        ("2356", "英業達"),
        ("2376", "技嘉"),
        ("2382", "廣達"),
        ("2377", "微星"),
    ],

    "PCB": [
        ("4958", "臻鼎-KY"),
        ("3037", "欣興"),
        ("3189", "景碩"),
        ("8046", "南電"),
    ],

    "航運": [
        ("2637", "慧洋-KY"),
        ("2615", "萬海"),
        ("2609", "陽明"),
        ("2612", "中航"),
        ("2606", "裕民"),
        ("2603", "長榮"),
        ("5608", "四維航"),
        ("2641", "正德"),
        ("2605", "新興"),
        ("2613", "中櫃"),
    ],
}


def get_group(group_name: str) -> list[tuple[str, str]]:
    """依照族群名稱取得股票清單。"""
    if group_name not in STOCK_GROUPS:
        raise ValueError(f"找不到股票族群：{group_name}")

    return STOCK_GROUPS[group_name]


def find_groups_by_stock_code(stock_code: str) -> list[str]:
    """找出股票代號所屬的全部族群。"""
    matched_groups = []

    for group_name, stocks in STOCK_GROUPS.items():
        for code, _stock_name in stocks:
            if code == stock_code:
                matched_groups.append(group_name)
                break

    return matched_groups


def resolve_group_names(keyword: str) -> list[str]:
    """輸入族群名稱或股票代號，回傳符合的族群名稱。"""
    keyword = keyword.strip()

    if keyword in STOCK_GROUPS:
        return [keyword]

    matched_groups = find_groups_by_stock_code(keyword)

    if not matched_groups:
        raise ValueError(
            f"找不到族群名稱或股票代號：{keyword}"
        )

    return matched_groups