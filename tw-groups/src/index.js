// tw-groups — 台股族群強弱排行
// 一個 Cloudflare Worker 同時負責：顯示網頁 + 代理呼叫台灣證交所即時報價 API

// 台股 43 個族群完整分類（使用者提供版本）
const GROUPS = [
  {
    "name": "被動元件",
    "stocks": [
      {
        "code": "6862",
        "name": "三集瑞"
      },
      {
        "code": "6155",
        "name": "鈞寶"
      },
      {
        "code": "3090",
        "name": "日電貿"
      },
      {
        "code": "4760",
        "name": "勤凱"
      },
      {
        "code": "6821",
        "name": "聯寶"
      },
      {
        "code": "1595",
        "name": "川寶"
      },
      {
        "code": "6449",
        "name": "鈺邦"
      },
      {
        "code": "2478",
        "name": "大毅"
      },
      {
        "code": "8043",
        "name": "蜜望實"
      },
      {
        "code": "6175",
        "name": "立敦"
      },
      {
        "code": "3236",
        "name": "千如"
      },
      {
        "code": "2472",
        "name": "立隆電"
      },
      {
        "code": "6834",
        "name": "天二科技"
      },
      {
        "code": "6127",
        "name": "九豪"
      },
      {
        "code": "8042",
        "name": "金山電"
      },
      {
        "code": "2327",
        "name": "國巨"
      },
      {
        "code": "2375",
        "name": "凱美"
      },
      {
        "code": "3026",
        "name": "禾伸堂"
      },
      {
        "code": "2492",
        "name": "華新科"
      },
      {
        "code": "5328",
        "name": "華容"
      },
      {
        "code": "6173",
        "name": "信昌電"
      },
      {
        "code": "3624",
        "name": "光頡"
      },
      {
        "code": "3357",
        "name": "臺慶科"
      },
      {
        "code": "3537",
        "name": "堡達"
      },
      {
        "code": "2428",
        "name": "興勤"
      }
    ]
  },
  {
    "name": "記憶體",
    "stocks": [
      {
        "code": "8271",
        "name": "宇瞻"
      },
      {
        "code": "2344",
        "name": "華邦電"
      },
      {
        "code": "4973",
        "name": "廣穎電通"
      },
      {
        "code": "3260",
        "name": "威剛"
      },
      {
        "code": "8088",
        "name": "品安"
      },
      {
        "code": "3135",
        "name": "凌航"
      },
      {
        "code": "4967",
        "name": "十銓"
      },
      {
        "code": "2337",
        "name": "旺宏"
      },
      {
        "code": "6265",
        "name": "方土昶"
      },
      {
        "code": "2451",
        "name": "創見"
      },
      {
        "code": "5289",
        "name": "宜鼎"
      },
      {
        "code": "8110",
        "name": "華東"
      },
      {
        "code": "5351",
        "name": "鈺創"
      },
      {
        "code": "3006",
        "name": "晶豪科"
      },
      {
        "code": "3060",
        "name": "銘異"
      },
      {
        "code": "8299",
        "name": "群聯"
      },
      {
        "code": "2408",
        "name": "南亞科"
      },
      {
        "code": "8131",
        "name": "福懋科"
      },
      {
        "code": "6770",
        "name": "力積電"
      },
      {
        "code": "4919",
        "name": "新唐"
      },
      {
        "code": "8112",
        "name": "至上"
      },
      {
        "code": "8150",
        "name": "南茂"
      }
    ]
  },
  {
    "name": "矽光子",
    "stocks": [
      {
        "code": "8111",
        "name": "立碁"
      },
      {
        "code": "4979",
        "name": "華星光"
      },
      {
        "code": "6218",
        "name": "豪勉"
      },
      {
        "code": "4977",
        "name": "眾達-KY"
      },
      {
        "code": "4903",
        "name": "聯光通"
      },
      {
        "code": "3234",
        "name": "光環"
      },
      {
        "code": "6530",
        "name": "創威"
      },
      {
        "code": "6715",
        "name": "嘉基"
      },
      {
        "code": "3363",
        "name": "上詮"
      },
      {
        "code": "8089",
        "name": "康全電訊"
      },
      {
        "code": "3025",
        "name": "星通"
      },
      {
        "code": "6451",
        "name": "訊芯-KY"
      },
      {
        "code": "4909",
        "name": "新復興"
      },
      {
        "code": "3447",
        "name": "展達"
      },
      {
        "code": "4908",
        "name": "前鼎"
      },
      {
        "code": "4971",
        "name": "IET-KY"
      },
      {
        "code": "6830",
        "name": "汎銓"
      },
      {
        "code": "3081",
        "name": "聯亞"
      },
      {
        "code": "4991",
        "name": "環宇-KY"
      },
      {
        "code": "6442",
        "name": "光聖"
      },
      {
        "code": "3163",
        "name": "波若威"
      },
      {
        "code": "3450",
        "name": "聯鈞"
      },
      {
        "code": "6426",
        "name": "統新"
      },
      {
        "code": "6197",
        "name": "佳必琪"
      },
      {
        "code": "4949",
        "name": "有成精密"
      }
    ]
  },
  {
    "name": "摺疊手機",
    "stocks": [
      {
        "code": "3548",
        "name": "兆利"
      },
      {
        "code": "1582",
        "name": "信錦"
      },
      {
        "code": "6805",
        "name": "富世達"
      },
      {
        "code": "3376",
        "name": "新日興"
      }
    ]
  },
  {
    "name": "矽晶圓",
    "stocks": [
      {
        "code": "5483",
        "name": "中美晶"
      },
      {
        "code": "2342",
        "name": "茂矽"
      },
      {
        "code": "3707",
        "name": "漢磊"
      },
      {
        "code": "3016",
        "name": "嘉晶"
      },
      {
        "code": "6488",
        "name": "環球晶"
      },
      {
        "code": "6182",
        "name": "合晶"
      },
      {
        "code": "3532",
        "name": "台勝科"
      }
    ]
  },
  {
    "name": "D電腦",
    "stocks": [
      {
        "code": "6166",
        "name": "凌華"
      },
      {
        "code": "6206",
        "name": "飛捷"
      },
      {
        "code": "3022",
        "name": "威強電"
      },
      {
        "code": "3479",
        "name": "安勤"
      },
      {
        "code": "4916",
        "name": "事欣科"
      },
      {
        "code": "6414",
        "name": "樺漢"
      },
      {
        "code": "3213",
        "name": "茂訊"
      },
      {
        "code": "2395",
        "name": "研華"
      },
      {
        "code": "3594",
        "name": "磐儀"
      },
      {
        "code": "2364",
        "name": "倫飛"
      }
    ]
  },
  {
    "name": "化學",
    "stocks": [
      {
        "code": "4716",
        "name": "大立"
      },
      {
        "code": "4711",
        "name": "永純"
      },
      {
        "code": "1708",
        "name": "東鹼"
      },
      {
        "code": "1735",
        "name": "日勝化"
      },
      {
        "code": "1717",
        "name": "長興"
      },
      {
        "code": "1727",
        "name": "中華化"
      },
      {
        "code": "1721",
        "name": "三晃"
      },
      {
        "code": "3430",
        "name": "奇鈦科"
      },
      {
        "code": "1711",
        "name": "永光"
      },
      {
        "code": "4755",
        "name": "三福化"
      },
      {
        "code": "4764",
        "name": "雙鍵"
      }
    ]
  },
  {
    "name": "軍工",
    "stocks": [
      {
        "code": "2634",
        "name": "漢翔"
      },
      {
        "code": "4541",
        "name": "晟田"
      },
      {
        "code": "8383",
        "name": "千附"
      },
      {
        "code": "6928",
        "name": "攸泰科技"
      },
      {
        "code": "8222",
        "name": "寶一"
      },
      {
        "code": "2630",
        "name": "亞航"
      },
      {
        "code": "6753",
        "name": "龍德造船"
      },
      {
        "code": "2231",
        "name": "為升"
      },
      {
        "code": "4572",
        "name": "駐龍"
      },
      {
        "code": "5371",
        "name": "中光電"
      },
      {
        "code": "7402",
        "name": "邑錡"
      },
      {
        "code": "4916",
        "name": "事欣科"
      },
      {
        "code": "6829",
        "name": "千附精密"
      },
      {
        "code": "2645",
        "name": "長榮航太"
      },
      {
        "code": "2429",
        "name": "銘旺科"
      },
      {
        "code": "1584",
        "name": "精剛"
      },
      {
        "code": "8033",
        "name": "雷虎"
      },
      {
        "code": "3230",
        "name": "錦明"
      },
      {
        "code": "1810",
        "name": "和成"
      },
      {
        "code": "6477",
        "name": "安集"
      }
    ]
  },
  {
    "name": "設備股",
    "stocks": [
      {
        "code": "8028",
        "name": "昇陽半導體"
      },
      {
        "code": "6438",
        "name": "迅得"
      },
      {
        "code": "1785",
        "name": "光洋科"
      },
      {
        "code": "5443",
        "name": "均豪"
      },
      {
        "code": "2467",
        "name": "志聖"
      },
      {
        "code": "6640",
        "name": "均華"
      },
      {
        "code": "3131",
        "name": "弘塑"
      },
      {
        "code": "3583",
        "name": "辛耘"
      },
      {
        "code": "3455",
        "name": "由田"
      },
      {
        "code": "8064",
        "name": "東捷"
      },
      {
        "code": "6187",
        "name": "萬潤"
      },
      {
        "code": "6207",
        "name": "雷科"
      }
    ]
  },
  {
    "name": "玻璃基板",
    "stocks": [
      {
        "code": "3149",
        "name": "正達"
      },
      {
        "code": "3673",
        "name": "TPK-KY"
      },
      {
        "code": "8027",
        "name": "鈦昇"
      },
      {
        "code": "8064",
        "name": "東捷"
      },
      {
        "code": "6207",
        "name": "雷科"
      }
    ]
  },
  {
    "name": "重電",
    "stocks": [
      {
        "code": "1519",
        "name": "華城"
      },
      {
        "code": "1513",
        "name": "中興電"
      },
      {
        "code": "1529",
        "name": "樂事綠能"
      },
      {
        "code": "1514",
        "name": "亞力"
      },
      {
        "code": "1503",
        "name": "士電"
      }
    ]
  },
  {
    "name": "神盾",
    "stocks": [
      {
        "code": "6243",
        "name": "迅杰"
      },
      {
        "code": "6462",
        "name": "神盾"
      },
      {
        "code": "8054",
        "name": "安國"
      },
      {
        "code": "6684",
        "name": "安格"
      },
      {
        "code": "6695",
        "name": "芯鼎"
      },
      {
        "code": "3041",
        "name": "揚智"
      }
    ]
  },
  {
    "name": "小電腦",
    "stocks": [
      {
        "code": "6558",
        "name": "興能高"
      },
      {
        "code": "3323",
        "name": "加百裕"
      },
      {
        "code": "1569",
        "name": "濱川"
      },
      {
        "code": "3211",
        "name": "順達"
      },
      {
        "code": "6672",
        "name": "騰輝電子-KY"
      },
      {
        "code": "6781",
        "name": "AES-KY"
      },
      {
        "code": "5309",
        "name": "系統電"
      },
      {
        "code": "4931",
        "name": "新盛力"
      }
    ]
  },
  {
    "name": "PCB",
    "stocks": [
      {
        "code": "4958",
        "name": "臻鼎-KY"
      },
      {
        "code": "3037",
        "name": "欣興"
      },
      {
        "code": "3189",
        "name": "景碩"
      },
      {
        "code": "8046",
        "name": "南電"
      },
      {
        "code": "8155",
        "name": "博智"
      }
    ]
  },
  {
    "name": "小電組",
    "stocks": [
      {
        "code": "6234",
        "name": "高僑"
      },
      {
        "code": "6191",
        "name": "精成科"
      },
      {
        "code": "1717",
        "name": "長興"
      },
      {
        "code": "2368",
        "name": "金像電"
      },
      {
        "code": "8074",
        "name": "鉅橡"
      },
      {
        "code": "3715",
        "name": "定穎投控"
      },
      {
        "code": "6290",
        "name": "良維"
      },
      {
        "code": "5340",
        "name": "建榮"
      },
      {
        "code": "2316",
        "name": "楠梓電"
      },
      {
        "code": "2313",
        "name": "華通"
      },
      {
        "code": "5498",
        "name": "凱崴"
      },
      {
        "code": "1802",
        "name": "台玻"
      },
      {
        "code": "1815",
        "name": "富喬"
      },
      {
        "code": "6274",
        "name": "台燿"
      },
      {
        "code": "2383",
        "name": "台光電"
      },
      {
        "code": "4989",
        "name": "榮科"
      },
      {
        "code": "8021",
        "name": "尖點"
      },
      {
        "code": "5475",
        "name": "德宏"
      },
      {
        "code": "8358",
        "name": "金居"
      },
      {
        "code": "6213",
        "name": "聯茂"
      },
      {
        "code": "5439",
        "name": "高技"
      }
    ]
  },
  {
    "name": "特化",
    "stocks": [
      {
        "code": "4763",
        "name": "材料-KY"
      },
      {
        "code": "4768",
        "name": "晶呈科技"
      },
      {
        "code": "4770",
        "name": "上品"
      },
      {
        "code": "4772",
        "name": "台特化"
      },
      {
        "code": "4722",
        "name": "國精化"
      }
    ]
  },
  {
    "name": "散熱",
    "stocks": [
      {
        "code": "2241",
        "name": "艾姆勒"
      },
      {
        "code": "3324",
        "name": "雙鴻"
      },
      {
        "code": "3483",
        "name": "力致"
      },
      {
        "code": "6230",
        "name": "尼得科超眾"
      },
      {
        "code": "6125",
        "name": "廣運"
      },
      {
        "code": "3017",
        "name": "奇鋐"
      },
      {
        "code": "2421",
        "name": "建準"
      },
      {
        "code": "3338",
        "name": "泰碩"
      },
      {
        "code": "8996",
        "name": "高力"
      },
      {
        "code": "2233",
        "name": "宇隆"
      }
    ]
  },
  {
    "name": "PA",
    "stocks": [
      {
        "code": "2455",
        "name": "全新"
      },
      {
        "code": "8086",
        "name": "宏捷科"
      },
      {
        "code": "3105",
        "name": "穩懋"
      }
    ]
  },
  {
    "name": "二極體",
    "stocks": [
      {
        "code": "5299",
        "name": "杰力"
      },
      {
        "code": "7712",
        "name": "博盛半導體"
      },
      {
        "code": "2481",
        "name": "強茂"
      },
      {
        "code": "8255",
        "name": "朋程"
      },
      {
        "code": "3317",
        "name": "尼克森"
      },
      {
        "code": "5425",
        "name": "台半"
      },
      {
        "code": "6435",
        "name": "大中"
      },
      {
        "code": "8261",
        "name": "富鼎"
      },
      {
        "code": "3675",
        "name": "德微"
      }
    ]
  },
  {
    "name": "石英",
    "stocks": [
      {
        "code": "3221",
        "name": "台嘉碩"
      },
      {
        "code": "2484",
        "name": "希華"
      },
      {
        "code": "3042",
        "name": "晶技"
      },
      {
        "code": "8289",
        "name": "泰藝"
      },
      {
        "code": "8182",
        "name": "加高"
      }
    ]
  },
  {
    "name": "探針卡",
    "stocks": [
      {
        "code": "7734",
        "name": "印能科技"
      },
      {
        "code": "6683",
        "name": "雍智科技"
      },
      {
        "code": "6515",
        "name": "穎崴"
      },
      {
        "code": "6510",
        "name": "精測"
      },
      {
        "code": "6223",
        "name": "旺矽"
      },
      {
        "code": "6217",
        "name": "中探針"
      }
    ]
  },
  {
    "name": "低軌衛星",
    "stocks": [
      {
        "code": "6485",
        "name": "點序"
      },
      {
        "code": "3138",
        "name": "耀登"
      },
      {
        "code": "2485",
        "name": "兆赫"
      },
      {
        "code": "6285",
        "name": "啟碁"
      },
      {
        "code": "2413",
        "name": "環科"
      },
      {
        "code": "2367",
        "name": "燿華"
      },
      {
        "code": "7717",
        "name": "萊德光電"
      },
      {
        "code": "2313",
        "name": "華通"
      },
      {
        "code": "3491",
        "name": "昇達科"
      }
    ]
  },
  {
    "name": "工具機",
    "stocks": [
      {
        "code": "4583",
        "name": "台灣精銳"
      },
      {
        "code": "4571",
        "name": "鈞興-KY"
      },
      {
        "code": "2049",
        "name": "上銀"
      },
      {
        "code": "1539",
        "name": "巨庭"
      },
      {
        "code": "4576",
        "name": "大銀微系統"
      },
      {
        "code": "1540",
        "name": "喬福"
      },
      {
        "code": "6609",
        "name": "瀧澤科"
      },
      {
        "code": "1597",
        "name": "直得"
      },
      {
        "code": "4561",
        "name": "健椿"
      },
      {
        "code": "4540",
        "name": "全球傳動"
      },
      {
        "code": "4510",
        "name": "高鋒"
      },
      {
        "code": "4533",
        "name": "協易機"
      },
      {
        "code": "2233",
        "name": "宇隆"
      },
      {
        "code": "4526",
        "name": "東台"
      }
    ]
  },
  {
    "name": "機器人",
    "stocks": [
      {
        "code": "2359",
        "name": "所羅門"
      },
      {
        "code": "2250",
        "name": "IKKA-KY"
      },
      {
        "code": "2365",
        "name": "昆盈"
      },
      {
        "code": "8374",
        "name": "羅昇"
      },
      {
        "code": "6215",
        "name": "和椿"
      },
      {
        "code": "6922",
        "name": "宸曜"
      },
      {
        "code": "2464",
        "name": "盟立"
      },
      {
        "code": "2453",
        "name": "凌群"
      },
      {
        "code": "1536",
        "name": "和大"
      },
      {
        "code": "5392",
        "name": "能率"
      },
      {
        "code": "5484",
        "name": "慧友"
      },
      {
        "code": "6188",
        "name": "廣明"
      },
      {
        "code": "4562",
        "name": "穎漢"
      },
      {
        "code": "8234",
        "name": "新漢"
      },
      {
        "code": "8071",
        "name": "能率網通"
      },
      {
        "code": "3048",
        "name": "益登"
      },
      {
        "code": "2374",
        "name": "佳能"
      },
      {
        "code": "3379",
        "name": "彬台"
      }
    ]
  },
  {
    "name": "光電",
    "stocks": [
      {
        "code": "3714",
        "name": "富采"
      },
      {
        "code": "2426",
        "name": "鼎元"
      },
      {
        "code": "6426",
        "name": "統新"
      },
      {
        "code": "5244",
        "name": "弘凱"
      },
      {
        "code": "6419",
        "name": "京晨科"
      },
      {
        "code": "3437",
        "name": "榮創"
      },
      {
        "code": "2393",
        "name": "億光"
      },
      {
        "code": "4956",
        "name": "光鋐"
      },
      {
        "code": "3031",
        "name": "佰鴻"
      },
      {
        "code": "8240",
        "name": "華宏"
      },
      {
        "code": "3673",
        "name": "TPK-KY"
      },
      {
        "code": "6706",
        "name": "惠特"
      },
      {
        "code": "5234",
        "name": "達興材料"
      },
      {
        "code": "3339",
        "name": "泰谷"
      },
      {
        "code": "4960",
        "name": "誠美材"
      },
      {
        "code": "6405",
        "name": "悅城"
      },
      {
        "code": "2489",
        "name": "瑞軒"
      },
      {
        "code": "4949",
        "name": "有成精密"
      },
      {
        "code": "2486",
        "name": "一詮"
      }
    ]
  },
  {
    "name": "功率半導體",
    "stocks": [
      {
        "code": "5425",
        "name": "台半"
      },
      {
        "code": "2481",
        "name": "強茂"
      },
      {
        "code": "6525",
        "name": "捷敏-KY"
      },
      {
        "code": "8261",
        "name": "富鼎"
      },
      {
        "code": "3016",
        "name": "嘉晶"
      },
      {
        "code": "3105",
        "name": "穩懋"
      },
      {
        "code": "3707",
        "name": "漢磊"
      }
    ]
  },
  {
    "name": "光學鏡頭",
    "stocks": [
      {
        "code": "6209",
        "name": "今國光"
      },
      {
        "code": "2374",
        "name": "佳能"
      },
      {
        "code": "6668",
        "name": "中揚光"
      },
      {
        "code": "3019",
        "name": "亞光"
      },
      {
        "code": "3630",
        "name": "新鉅科"
      },
      {
        "code": "3504",
        "name": "揚明光"
      },
      {
        "code": "3362",
        "name": "先進光"
      },
      {
        "code": "4974",
        "name": "亞泰"
      },
      {
        "code": "6278",
        "name": "台表科"
      },
      {
        "code": "3406",
        "name": "玉晶光"
      },
      {
        "code": "3441",
        "name": "聯一光"
      },
      {
        "code": "4976",
        "name": "佳凌"
      },
      {
        "code": "3008",
        "name": "大立光"
      }
    ]
  },
  {
    "name": "上曜",
    "stocks": [
      {
        "code": "1316",
        "name": "上曜"
      },
      {
        "code": "4303",
        "name": "信立"
      },
      {
        "code": "4714",
        "name": "永捷"
      },
      {
        "code": "5314",
        "name": "世紀"
      },
      {
        "code": "6418",
        "name": "詠昇"
      },
      {
        "code": "3313",
        "name": "斐成"
      }
    ]
  },
  {
    "name": "金融股",
    "stocks": [
      {
        "code": "2886",
        "name": "兆豐金"
      },
      {
        "code": "2884",
        "name": "玉山金"
      },
      {
        "code": "2885",
        "name": "元大金"
      },
      {
        "code": "2838",
        "name": "聯邦銀"
      },
      {
        "code": "2812",
        "name": "台中銀"
      },
      {
        "code": "2881",
        "name": "富邦金"
      },
      {
        "code": "2882",
        "name": "國泰金"
      },
      {
        "code": "2890",
        "name": "永豐金"
      },
      {
        "code": "2891",
        "name": "中信金"
      },
      {
        "code": "2892",
        "name": "第一金"
      },
      {
        "code": "2883",
        "name": "凱基金"
      },
      {
        "code": "2887",
        "name": "台新新光金"
      },
      {
        "code": "2889",
        "name": "國票金"
      },
      {
        "code": "6005",
        "name": "群益證"
      },
      {
        "code": "2801",
        "name": "彰銀"
      },
      {
        "code": "2816",
        "name": "旺旺保"
      },
      {
        "code": "2820",
        "name": "華票"
      },
      {
        "code": "2832",
        "name": "台產"
      },
      {
        "code": "2834",
        "name": "臺企銀"
      },
      {
        "code": "2836",
        "name": "高雄銀"
      },
      {
        "code": "2845",
        "name": "遠東銀"
      },
      {
        "code": "2849",
        "name": "安泰銀"
      },
      {
        "code": "2850",
        "name": "新產"
      },
      {
        "code": "2851",
        "name": "中再保"
      },
      {
        "code": "2852",
        "name": "第一保"
      },
      {
        "code": "2855",
        "name": "統一證"
      },
      {
        "code": "2867",
        "name": "三商壽"
      },
      {
        "code": "2880",
        "name": "華南金"
      },
      {
        "code": "2897",
        "name": "王道銀行"
      },
      {
        "code": "5880",
        "name": "合庫金"
      },
      {
        "code": "5876",
        "name": "上海商銀"
      },
      {
        "code": "6024",
        "name": "群益期"
      },
      {
        "code": "5864",
        "name": "致和證"
      },
      {
        "code": "5878",
        "name": "台名"
      },
      {
        "code": "6015",
        "name": "宏遠證"
      },
      {
        "code": "6016",
        "name": "康和證"
      },
      {
        "code": "6020",
        "name": "大展證"
      },
      {
        "code": "6021",
        "name": "美好證"
      },
      {
        "code": "6023",
        "name": "元大期"
      }
    ]
  },
  {
    "name": "航運",
    "stocks": [
      {
        "code": "2637",
        "name": "慧洋-KY"
      },
      {
        "code": "2615",
        "name": "萬海"
      },
      {
        "code": "2609",
        "name": "陽明"
      },
      {
        "code": "2612",
        "name": "中航"
      },
      {
        "code": "2606",
        "name": "裕民"
      },
      {
        "code": "2603",
        "name": "長榮"
      },
      {
        "code": "2641",
        "name": "正德"
      },
      {
        "code": "2605",
        "name": "新興"
      },
      {
        "code": "2613",
        "name": "中櫃"
      }
    ]
  },
  {
    "name": "空運",
    "stocks": [
      {
        "code": "6757",
        "name": "台灣虎航"
      },
      {
        "code": "2610",
        "name": "華航"
      },
      {
        "code": "2618",
        "name": "長榮航"
      }
    ]
  },
  {
    "name": "散裝",
    "stocks": [
      {
        "code": "2605",
        "name": "新興"
      },
      {
        "code": "2612",
        "name": "中航"
      },
      {
        "code": "2606",
        "name": "裕民"
      },
      {
        "code": "2641",
        "name": "正德"
      },
      {
        "code": "2637",
        "name": "慧洋-KY"
      }
    ]
  },
  {
    "name": "聯電股",
    "stocks": [
      {
        "code": "2363",
        "name": "矽統"
      },
      {
        "code": "2303",
        "name": "聯電"
      },
      {
        "code": "5347",
        "name": "世界"
      },
      {
        "code": "8039",
        "name": "台虹"
      }
    ]
  },
  {
    "name": "鴻家軍",
    "stocks": [
      {
        "code": "3062",
        "name": "建漢"
      },
      {
        "code": "3498",
        "name": "陽程"
      },
      {
        "code": "5243",
        "name": "乙盛-KY"
      },
      {
        "code": "3092",
        "name": "鴻碩"
      },
      {
        "code": "2328",
        "name": "廣宇"
      },
      {
        "code": "2354",
        "name": "鴻準"
      },
      {
        "code": "2317",
        "name": "鴻海"
      }
    ]
  },
  {
    "name": "台塑四寶",
    "stocks": [
      {
        "code": "6505",
        "name": "台塑化"
      },
      {
        "code": "1301",
        "name": "台塑"
      },
      {
        "code": "1303",
        "name": "南亞"
      },
      {
        "code": "1326",
        "name": "台化"
      }
    ]
  },
  {
    "name": "AI",
    "stocks": [
      {
        "code": "3231",
        "name": "緯創"
      },
      {
        "code": "2356",
        "name": "英業達"
      },
      {
        "code": "2376",
        "name": "技嘉"
      },
      {
        "code": "2382",
        "name": "廣達"
      },
      {
        "code": "2377",
        "name": "微星"
      },
      {
        "code": "2357",
        "name": "華碩"
      }
    ]
  },
  {
    "name": "彬彬",
    "stocks": [
      {
        "code": "3379",
        "name": "彬台"
      },
      {
        "code": "3022",
        "name": "威強電"
      },
      {
        "code": "1569",
        "name": "濱川"
      },
      {
        "code": "2328",
        "name": "廣宇"
      }
    ]
  },
  {
    "name": "IP",
    "stocks": [
      {
        "code": "3443",
        "name": "創意"
      },
      {
        "code": "3661",
        "name": "世芯-KY"
      },
      {
        "code": "6533",
        "name": "晶心科"
      },
      {
        "code": "3228",
        "name": "金麗科"
      },
      {
        "code": "3529",
        "name": "力旺"
      },
      {
        "code": "8227",
        "name": "巨有科技"
      },
      {
        "code": "6643",
        "name": "M31"
      },
      {
        "code": "6415",
        "name": "矽力-KY"
      }
    ]
  },
  {
    "name": "AI眼鏡",
    "stocks": [
      {
        "code": "6237",
        "name": "驊訊"
      },
      {
        "code": "6672",
        "name": "騰輝電子-KY"
      },
      {
        "code": "6742",
        "name": "澤米"
      },
      {
        "code": "3294",
        "name": "英濟"
      },
      {
        "code": "3645",
        "name": "達邁"
      },
      {
        "code": "6456",
        "name": "GIS-KY"
      }
    ]
  },
  {
    "name": "面板",
    "stocks": [
      {
        "code": "2409",
        "name": "友達"
      },
      {
        "code": "6116",
        "name": "彩晶"
      },
      {
        "code": "3481",
        "name": "群創"
      }
    ]
  },
  {
    "name": "扇形封裝",
    "stocks": [
      {
        "code": "3580",
        "name": "友威科"
      },
      {
        "code": "3535",
        "name": "晶彩科"
      },
      {
        "code": "3663",
        "name": "鑫科"
      },
      {
        "code": "8064",
        "name": "東捷"
      }
    ]
  },
  {
    "name": "千元",
    "stocks": [
      {
        "code": "3324",
        "name": "雙鴻"
      },
      {
        "code": "5289",
        "name": "宜鼎"
      },
      {
        "code": "1519",
        "name": "華城"
      },
      {
        "code": "3529",
        "name": "力旺"
      },
      {
        "code": "3017",
        "name": "奇鋐"
      },
      {
        "code": "6781",
        "name": "AES-KY"
      },
      {
        "code": "6805",
        "name": "富世達"
      },
      {
        "code": "2454",
        "name": "聯發科"
      },
      {
        "code": "3665",
        "name": "貿聯-KY"
      },
      {
        "code": "2404",
        "name": "漢唐"
      },
      {
        "code": "2368",
        "name": "金像電"
      },
      {
        "code": "6442",
        "name": "光聖"
      },
      {
        "code": "8299",
        "name": "群聯"
      },
      {
        "code": "2308",
        "name": "台達電"
      },
      {
        "code": "3008",
        "name": "大立光"
      },
      {
        "code": "6510",
        "name": "精測"
      },
      {
        "code": "2383",
        "name": "台光電"
      },
      {
        "code": "3443",
        "name": "創意"
      },
      {
        "code": "3163",
        "name": "波若威"
      },
      {
        "code": "3653",
        "name": "健策"
      },
      {
        "code": "6187",
        "name": "萬潤"
      },
      {
        "code": "3491",
        "name": "昇達科"
      },
      {
        "code": "6223",
        "name": "旺矽"
      }
    ]
  },
  {
    "name": "太陽能",
    "stocks": [
      {
        "code": "6477",
        "name": "安集"
      },
      {
        "code": "2406",
        "name": "國碩"
      },
      {
        "code": "3576",
        "name": "聯合再生"
      },
      {
        "code": "6443",
        "name": "元晶"
      },
      {
        "code": "3686",
        "name": "達能"
      },
      {
        "code": "6244",
        "name": "茂迪"
      },
      {
        "code": "3691",
        "name": "碩禾"
      }
    ]
  }
];

const ALL_CODES = [...new Set(GROUPS.flatMap((g) => g.stocks.map((s) => s.code)))];

const HTML_PAGE = `<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>台股族群強弱排行</title>
<style>
  :root{
    --bg:#12100f; --panel:#1c1815; --panel-2:#241f1a; --line:#3a322b;
    --text:#f1ece6; --muted:#a89c8f; --up:#e6675f; --down:#5fae6f; --accent:#c9a98c;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;}
  header{padding:20px 16px 8px;}
  h1{font-size:20px;margin:0 0 4px;}
  .sub{color:var(--muted);font-size:13px;}
  .updated{color:var(--muted);font-size:11px;text-align:center;padding:8px 0;}
  .loading{color:var(--muted);text-align:center;padding:40px 0;}

  .layout{display:flex;gap:16px;padding:12px 16px 40px;align-items:flex-start;flex-wrap:wrap;}
  .col-left{flex:1 1 320px;min-width:280px;}
  .col-right{flex:3 1 620px;min-width:320px;}
  .section-title{font-size:14px;font-weight:800;margin:4px 0 10px;color:var(--text);}

  /* 強勢／弱勢 分頁與統計 */
  .tabs{display:flex;gap:24px;padding:16px 16px 0;}
  .tab-btn{background:none;border:none;color:var(--muted);font-size:16px;font-weight:800;padding:0 0 10px;cursor:pointer;border-bottom:3px solid transparent;}
  .tab-btn.active.strong{color:var(--up);border-bottom-color:var(--up);}
  .tab-btn.active.weak{color:var(--down);border-bottom-color:var(--down);}
  .stat-bar{display:flex;gap:28px;padding:14px 16px;margin:0 16px 4px;background:var(--panel);border:1px solid var(--line);border-radius:10px;}
  .stat-item{display:flex;flex-direction:column;align-items:center;flex:1;}
  .stat-num{font-size:26px;font-weight:800;}
  .stat-label{font-size:12px;color:var(--muted);margin-top:2px;}

  /* 左側：前六大族群 */
  .top6-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;}
  .top6-card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px;}
  .top6-head{display:flex;align-items:center;gap:8px;margin-bottom:6px;}
  .badge{width:20px;height:20px;border-radius:50%;background:var(--accent);color:#241f1a;font-size:11px;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0;}
  .top6-name{font-weight:800;font-size:14px;}
  .top6-chg{font-size:15px;font-weight:800;}

  /* 右側：各族群前三強／前三弱個股 */
  .top-groups-row{display:grid;grid-template-columns:repeat(3, 1fr);gap:10px;}
  .top-group-col{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;}
  .top-group-head{display:flex;justify-content:space-between;align-items:center;padding:10px 12px;background:#4a3d34;}
  .top-group-rank{font-size:12px;font-weight:800;color:#241f1a;background:var(--accent);border-radius:6px;padding:2px 6px;margin-right:6px;}
  .top-group-name{font-weight:800;font-size:14px;color:var(--text);}
  .top-group-chg{font-size:14px;font-weight:800;}
  .stock-row{display:flex;justify-content:space-between;align-items:center;padding:9px 12px;border-top:1px solid var(--line);}
  .stock-row .sname{font-weight:700;font-size:13px;}
  .stock-row .scode{color:var(--muted);font-size:11px;margin-left:4px;}
  .stock-row .schg{font-size:13px;font-weight:700;}

  .up{color:var(--up);}
  .down{color:var(--down);}
  .flat{color:var(--muted);}

  @media (max-width: 900px){
    .top-groups-row{grid-template-columns:repeat(2, 1fr);}
  }
  @media (max-width: 640px){
    .top6-grid{grid-template-columns:1fr;}
    .top-groups-row{grid-template-columns:1fr;}
    .stat-bar{gap:12px;}
  }
</style>
</head>
<body>
<header>
  <h1>台股族群強弱排行</h1>
  <div class="sub">依族群平均漲跌幅排序，每 15 秒自動更新一次</div>
</header>
<div class="tabs">
  <button class="tab-btn strong active" id="tabStrong">強勢</button>
  <button class="tab-btn weak" id="tabWeak">弱勢</button>
</div>
<div id="app"><div class="loading">資料讀取中…</div></div>
<div class="updated" id="updatedAt"></div>

<script>
const GROUPS = ${JSON.stringify(GROUPS)};

function fmt(n){ return (n>0?'+':'') + n.toFixed(2); }
function dirClass(n){ return n>0?'up':(n<0?'down':'flat'); }

let currentTab = 'strong'; // 'strong' | 'weak'
let lastData = null;

function render(){
  const app = document.getElementById('app');
  if (!lastData) return;

  const groups = lastData.groups;
  const total = groups.length;
  const strongCount = groups.filter(g => g.avgChange > 0).length;
  const weakCount = total - strongCount;

  const rankedDesc = groups.slice().sort((a,b) => b.avgChange - a.avgChange); // 強→弱
  const rankedAsc = groups.slice().sort((a,b) => a.avgChange - b.avgChange);  // 弱→強

  const isStrong = currentTab === 'strong';
  const side6 = isStrong ? rankedDesc.slice(0, 6) : rankedAsc.slice(0, 6);
  const leftTitle = isStrong ? '漲幅前六大族群' : '跌幅前六大族群';
  const rankLabel = isStrong ? '強' : '弱';

  // 左側：前六大族群宮格
  const side6Html = side6.map((g, idx) =>
    '<div class="top6-card">' +
      '<div class="top6-head"><span class="badge">' + (idx+1) + '</span><span class="top6-name">' + g.name + '</span></div>' +
      '<div class="top6-chg ' + dirClass(g.avgChange) + '">' + fmt(g.avgChange) + '%</div>' +
    '</div>'
  ).join('');

  // 右側：前六大族群各自最具代表性的 3 檔個股
  // 強勢分頁看該族群裡漲最多的 3 檔；弱勢分頁看該族群裡跌最多的 3 檔
  const sideGroupsHtml = side6.map((g, idx) => {
    const sortedStocks = g.stocks
      .filter(s => s.price !== null)
      .slice()
      .sort((a,b) => isStrong ? (b.changePercent - a.changePercent) : (a.changePercent - b.changePercent));
    const topStocks = sortedStocks.slice(0, 3);
    const stocksHtml = topStocks.length
      ? topStocks.map(s =>
          '<div class="stock-row"><div><span class="sname">' + s.name + '</span><span class="scode">' + s.code + '</span></div>' +
          '<div class="schg ' + dirClass(s.changePercent) + '">' + fmt(s.changePercent) + '%</div></div>'
        ).join('')
      : '<div class="stock-row"><span class="flat">目前無資料</span></div>';

    return '<div class="top-group-col">' +
      '<div class="top-group-head"><div><span class="top-group-rank">第' + (idx+1) + rankLabel + '</span>' +
      '<span class="top-group-name">' + g.name + '</span></div>' +
      '<div class="top-group-chg ' + dirClass(g.avgChange) + '">' + fmt(g.avgChange) + '%</div></div>' +
      stocksHtml +
    '</div>';
  }).join('');

  app.innerHTML =
    '<div class="stat-bar">' +
      '<div class="stat-item"><div class="stat-num up">' + strongCount + ' / ' + total + '</div><div class="stat-label">強勢族群</div></div>' +
      '<div class="stat-item"><div class="stat-num down">' + weakCount + ' / ' + total + '</div><div class="stat-label">弱勢族群</div></div>' +
    '</div>' +
    '<div class="layout">' +
      '<div class="col-left">' +
        '<div class="section-title">' + leftTitle + '</div>' +
        '<div class="top6-grid">' + side6Html + '</div>' +
      '</div>' +
      '<div class="col-right">' +
        '<div class="section-title">各族群前三' + rankLabel + '個股</div>' +
        '<div class="top-groups-row">' + sideGroupsHtml + '</div>' +
      '</div>' +
    '</div>';

  document.getElementById('updatedAt').textContent = '更新於 ' + new Date().toLocaleTimeString('zh-TW');
}

async function refresh(){
  const app = document.getElementById('app');
  try {
    const res = await fetch('/api/groups');
    lastData = await res.json();
  } catch(e){
    if (!lastData) app.innerHTML = '<div class="loading">資料抓取失敗，稍後會自動重試</div>';
    return;
  }
  render();
}

document.getElementById('tabStrong').addEventListener('click', () => {
  currentTab = 'strong';
  document.getElementById('tabStrong').classList.add('active');
  document.getElementById('tabWeak').classList.remove('active');
  render();
});
document.getElementById('tabWeak').addEventListener('click', () => {
  currentTab = 'weak';
  document.getElementById('tabWeak').classList.add('active');
  document.getElementById('tabStrong').classList.remove('active');
  render();
});

refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>`;

const CHUNK_SIZE = 80; // 每批查詢的股票數（每檔會展開成 tse_/otc_ 兩個查詢項，太多會讓網址過長）

function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

async function fetchQuoteChunk(codes) {
  const exCh = codes.flatMap((c) => [`tse_${c}.tw`, `otc_${c}.tw`]).join('|');
  const url = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=${exCh}&json=1&delay=0`;

  const resp = await fetch(url, {
    headers: {
      Referer: 'https://mis.twse.com.tw/stock/index.jsp',
      'User-Agent': 'Mozilla/5.0 (compatible; tw-groups/1.0)',
    },
  });
  if (!resp.ok) throw new Error(`TWSE API 回應錯誤: ${resp.status}`);
  const data = await resp.json();
  return data.msgArray || [];
}

async function fetchQuotes(codes) {
  const quotes = {};
  for (const code of codes) quotes[code] = { price: null, change: 0, changePercent: 0 };

  const chunks = chunk(codes, CHUNK_SIZE);
  // 批次之間平行查詢，加快整體速度
  const results = await Promise.all(chunks.map((c) => fetchQuoteChunk(c)));

  for (const msgArray of results) {
    for (const item of msgArray) {
      const code = item.c;
      if (!code || !(code in quotes)) continue;
      const price = parseFloat(item.z);
      const prevClose = parseFloat(item.y);
      const fallbackPrice = parseFloat(item.o) || parseFloat(item.h) || parseFloat(item.l);
      const finalPrice = Number.isFinite(price) ? price : fallbackPrice;
      if (Number.isFinite(finalPrice) && Number.isFinite(prevClose) && prevClose > 0) {
        quotes[code] = {
          price: finalPrice,
          change: finalPrice - prevClose,
          changePercent: ((finalPrice - prevClose) / prevClose) * 100,
        };
      }
    }
  }
  return quotes;
}

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (url.pathname === '/api/groups') {
      try {
        const quotes = await fetchQuotes(ALL_CODES);

        const groups = GROUPS.map((g) => {
          const stocks = g.stocks.map((s) => ({
            code: s.code,
            name: s.name,
            price: quotes[s.code]?.price ?? null,
            changePercent: quotes[s.code]?.changePercent ?? 0,
          }));
          const valid = stocks.filter((s) => s.price !== null);
          const avgChange = valid.length
            ? valid.reduce((sum, s) => sum + s.changePercent, 0) / valid.length
            : 0;
          return { name: g.name, avgChange, stocks };
        });

        return Response.json({ groups });
      } catch (err) {
        return Response.json({ error: String(err) }, { status: 502 });
      }
    }

    return new Response(HTML_PAGE, {
      headers: { 'content-type': 'text/html; charset=UTF-8' },
    });
  },
};
