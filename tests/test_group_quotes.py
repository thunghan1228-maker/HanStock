"""證交所報價代抓（tw-groups 首頁的報價中心）：分段同時抓、5 秒共用、30 秒內先給舊的背景再抓、每檔留最新一筆
（舊主機回的舊資料不蓋掉新的）、部分失敗照回、全部失敗丟例外、端點。"""

from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch_history as module
import database
import persistent_app


def mis_fetcher(fail_codes: set[str] | None = None, calls: list[str] | None = None, price: str = "101", clock: str = "09:44:55"):
    """假的證交所：每檔上市、昨收 100、成交 101（2330 沒成交只剩委買 = 漲停價 110）；fail_codes 裡的代號那一段整段失敗。"""
    lock = threading.Lock()

    def fetch(url: str):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        channels = query["ex_ch"][0].split("|")
        codes = [c.split("_")[1].replace(".tw", "") for c in channels if c.startswith("tse_")]
        with lock:
            if calls is not None:
                calls.append(",".join(codes))
        if fail_codes and fail_codes & set(codes):
            raise TimeoutError("timed out")
        items = []
        for code in codes:
            if code == "2330":
                items.append({"c": code, "n": "台積電", "z": "-", "b": "110_109.5_", "a": "-", "y": "100", "o": "102", "v": "5000",
                              "u": "110", "w": "90", "d": "20261006", "t": "09:45:00"})
            else:
                items.append({"c": code, "n": "股" + code, "z": price, "y": "100", "o": "100.5", "v": "800", "u": "110", "w": "90",
                              "d": "20261006", "t": clock})
        return {"msgArray": items}

    return fetch


class GroupQuotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._reset_group_quotes()
        self.open_patch = patch.object(module, "_market_open", return_value=True)   # 測盤中的 5 秒／30 秒
        self.open_patch.start()

    def tearDown(self) -> None:
        self.open_patch.stop()
        module._reset_group_quotes()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_quotes_fields_and_cache(self) -> None:
        calls: list[str] = []
        codes = ",".join(str(1000 + i) for i in range(170)) + ",2330, 2330 ,bad!,12"
        payload = module.group_quotes_payload(codes, fetcher=mis_fetcher(calls=calls))
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(calls), 3)                                   # 171 檔 → 80／80／11 三段
        self.assertEqual((payload["missing"], payload["failedChunks"]), (0, 0))
        self.assertEqual((payload["quoteDate"], payload["quoteTime"]), ("2026-10-06", "09:45:00"))
        q = payload["quotes"]["1000"]
        self.assertEqual((q["price"], q["prevClose"], q["change"], q["open"], q["volume"], q["name"]), (101.0, 100.0, 1.0, 100.5, 800, "股1000"))
        self.assertAlmostEqual(q["changePercent"], 1.0)
        tsmc = payload["quotes"]["2330"]
        self.assertEqual((tsmc["price"], tsmc["limitUp"], tsmc["name"]), (110.0, True, "台積電"))   # 沒成交：用委買（漲停鎖死）
        again = module.group_quotes_payload(codes, fetcher=mis_fetcher(calls=calls))
        self.assertIs(again, payload)                                     # 5 秒內同一批：不再打證交所
        self.assertEqual(len(calls), 3)

    def test_stale_while_refresh_and_keep_newest(self) -> None:
        codes = "1000,1001"
        first = module.group_quotes_payload(codes, fetcher=mis_fetcher(price="101", clock="09:45:10"))
        key = "1000,1001"
        at, payload = module._group_quotes_cache["payloads"][key]
        module._group_quotes_cache["payloads"][key] = (at - 10, payload)     # 10 秒前的：先給舊的、背景抓
        calls: list[str] = []
        got = module.group_quotes_payload(codes, fetcher=mis_fetcher(calls=calls, price="105", clock="09:45:30"))
        self.assertIs(got, first)
        module._group_quotes_cache["threads"].get(key) and module._group_quotes_cache["threads"][key].join(5)
        for _ in range(50):
            if module._group_quotes_cache["payloads"][key][1] is not first:
                break
            threading.Event().wait(0.05)
        fresh = module.group_quotes_payload(codes, fetcher=mis_fetcher())
        self.assertEqual((fresh["quotes"]["1000"]["price"], fresh["quoteTime"]), (105.0, "09:45:30"))
        self.assertEqual(len(calls), 1)
        # 舊主機回比較舊的資料（09:44:00、價格 99）：不蓋掉 09:45:30 那筆
        at, payload = module._group_quotes_cache["payloads"][key]
        module._group_quotes_cache["payloads"][key] = (at - 60, payload)     # 超過 30 秒：等這次抓完
        older = module.group_quotes_payload(codes, fetcher=mis_fetcher(price="99", clock="09:44:00"))
        self.assertEqual((older["quotes"]["1000"]["price"], older["quoteTime"]), (105.0, "09:45:30"))
        self.assertIsNot(older, fresh)
        # 跨日：1001 那段今天沒抓到，不拿昨天那筆冒充今天
        def next_day(url: str):
            data = mis_fetcher(price="120", clock="09:01:00")(url)
            data["msgArray"] = [dict(i, d="20261007") for i in data["msgArray"] if i["c"] == "1000"]
            return data
        module._group_quotes_cache["payloads"].clear()
        nd = module.group_quotes_payload(codes, fetcher=next_day)
        self.assertEqual((sorted(nd["quotes"]), nd["quoteDate"], nd["missing"]), (["1000"], "2026-10-07", 1))

    def test_no_trade_no_book_does_not_overwrite_limit_up_price(self) -> None:
        # 2026-10-13 廣寰科：漲停鎖死（委買＝漲停價 110），之後某一輪證交所回「沒成交、也沒有委買委賣」，
        # 只能退回開盤價 102——不能蓋掉漲停價，量照樣更新。
        def locked(url: str):
            return {"msgArray": [{"c": "3287", "n": "廣寰科", "z": "-", "b": "110_", "a": "-", "y": "100", "o": "102", "v": "4000",
                                  "u": "110", "w": "90", "d": "20261013", "t": "10:31:00"}]}

        def empty_book(url: str):
            return {"msgArray": [{"c": "3287", "n": "廣寰科", "z": "-", "b": "-", "a": "-", "y": "100", "o": "102", "v": "4145",
                                  "u": "110", "w": "90", "d": "20261013", "t": "10:51:00"}]}

        first = module.group_quotes_payload("3287", fetcher=locked)["quotes"]["3287"]
        self.assertEqual((first["price"], first["limitUp"]), (110.0, True))
        module._group_quotes_cache["payloads"].clear()
        later = module.group_quotes_payload("3287", fetcher=empty_book)["quotes"]["3287"]
        self.assertEqual((later["price"], later["limitUp"], later["volume"]), (110.0, True, 4145))
        # 一開始就只拿得到開盤價（沒有更早的資料）：照舊用開盤價，之後有委買委賣就換掉
        module._reset_group_quotes()
        only = module.group_quotes_payload("3287", fetcher=empty_book)["quotes"]["3287"]
        self.assertEqual((only["price"], only["limitUp"]), (102.0, False))
        module._group_quotes_cache["payloads"].clear()

        def locked_later(url: str):
            data = locked(url)
            data["msgArray"][0]["t"] = "10:52:00"
            return data

        fixed = module.group_quotes_payload("3287", fetcher=locked_later)["quotes"]["3287"]
        self.assertEqual((fixed["price"], fixed["limitUp"]), (110.0, True))

    def test_off_hours_windows(self) -> None:
        # 收盤後：快取 2 分鐘內直接給、12 小時內先給舊的再背景抓（不讓每次都等 10 秒重抓）
        from datetime import datetime as dt

        self.open_patch.stop()
        try:
            self.assertFalse(module._market_open(dt(2026, 10, 12, 23, 30, tzinfo=module.TW_TZ)))
            self.assertTrue(module._market_open(dt(2026, 10, 12, 10, 0, tzinfo=module.TW_TZ)))
            self.assertFalse(module._market_open(dt(2026, 10, 11, 10, 0, tzinfo=module.TW_TZ)))   # 週日
            with patch.object(module, "_market_open", return_value=False):
                codes = "1000,1001"
                first = module.group_quotes_payload(codes, fetcher=mis_fetcher())
                key = "1000,1001"
                at, payload = module._group_quotes_cache["payloads"][key]
                module._group_quotes_cache["payloads"][key] = (at - 90, payload)   # 90 秒前：收盤後還算新
                calls: list[str] = []
                self.assertIs(module.group_quotes_payload(codes, fetcher=mis_fetcher(calls=calls)), first)
                self.assertEqual(calls, [])
                module._group_quotes_cache["payloads"][key] = (at - 3600, payload)   # 一小時前：先給舊的、背景抓
                self.assertIs(module.group_quotes_payload(codes, fetcher=mis_fetcher(calls=calls)), first)
                th = module._group_quotes_cache["threads"].get(key)
                th and th.join(5)
                self.assertEqual(len(calls), 1)
        finally:
            self.open_patch.start()

    def test_partial_and_total_failure(self) -> None:
        codes = ",".join(str(1000 + i) for i in range(160))
        partial = module.group_quotes_payload(codes, fetcher=mis_fetcher(fail_codes={"1000"}))
        self.assertEqual((partial["failedChunks"], partial["missing"], len(partial["quotes"])), (1, 80, 80))
        module._reset_group_quotes()
        with self.assertRaises(RuntimeError):
            module.group_quotes_payload(codes, fetcher=mis_fetcher(fail_codes={"1000", "1080"}))
        with self.assertRaises(ValueError):
            module.group_quotes_payload(" ,bad!")

    def test_endpoint(self) -> None:
        client = TestClient(persistent_app.app)
        self.assertEqual(client.get("/api/hub/group-quotes?codes=").status_code, 400)
        with patch.object(module, "_default_fetcher", mis_fetcher()):
            ok = client.get("/api/hub/group-quotes?codes=2330,2317")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(sorted(ok.json()["quotes"]), ["2317", "2330"])
        module._reset_group_quotes()
        with patch.object(module, "_default_fetcher", mis_fetcher(fail_codes={"2330"})):
            down = client.get("/api/hub/group-quotes?codes=2330,2317")
        self.assertEqual(down.status_code, 502)
        self.assertIn("證交所報價抓不到", down.json()["error"])


    # ---- 逐筆成交蓋過證交所那份（2026-10-21 使用者：新鉅科漲回去了，刀劍空一兩分鐘後才消掉）----
    @staticmethod
    def _tick(code: str, close: float, total_volume: int, clock: str = "09:46:10", day: str = "2026-10-06", **extra):
        return {"code": code, "close": close, "open": 100.5, "total_volume": total_volume, "tick_time": f"{day}T{clock}.123456+08:00",
                "simtrade": False, "intraday_odd": False, "suspend": False, **extra}

    def test_overlay_newer_tick_wins(self) -> None:
        payload = module.group_quotes_payload("1000,1001,1002,1003", fetcher=mis_fetcher())   # 昨收 100、成交 101、量 800、09:44:55
        before = {c: dict(q) for c, q in payload["quotes"].items()}
        ticks = {
            "1000": self._tick("1000", 103.5, 830),                    # 量比較多＝比較新：蓋上去
            "1001": self._tick("1001", 99.0, 800),                     # 量一樣＝同一盤，維持證交所
            "1002": self._tick("1002", 99.0, 790),                     # 量比較少＝比較舊
            "1003": self._tick("1003", 99.0, 900, day="2026-10-05"),   # 昨天的成交（盤前還留著）不蓋
        }
        got = module.overlay_realtime_ticks(payload, ticks)
        self.assertEqual(payload["quotes"], before)                    # 快取裡那份不能被改
        one = got["quotes"]["1000"]
        self.assertEqual((one["price"], one["change"], one["volume"]), (103.5, 3.5, 830))
        self.assertAlmostEqual(one["changePercent"], 3.5)
        self.assertEqual(one["name"], "股1000")
        for code in ("1001", "1002", "1003"):
            self.assertEqual(got["quotes"][code], before[code])
        self.assertEqual((got["realtimeTicks"], got["quoteTime"]), (1, "09:46:10"))

    def test_overlay_skips_auction_odd_lot_and_off_hours(self) -> None:
        payload = module.group_quotes_payload("1000,1001,1002", fetcher=mis_fetcher())
        ticks = {
            "1000": self._tick("1000", 103.5, 830, simtrade=True),
            "1001": self._tick("1001", 103.5, 830, intraday_odd=True),
            "1002": self._tick("1002", 103.5, 830, suspend=True),
        }
        self.assertIs(module.overlay_realtime_ticks(payload, ticks), payload)
        self.assertIs(module.overlay_realtime_ticks(payload, None), payload)
        with patch.object(module, "_market_open", return_value=False):
            good = {"1000": self._tick("1000", 103.5, 830)}
            self.assertIs(module.overlay_realtime_ticks(payload, good), payload)

    def test_overlay_limit_flags(self) -> None:
        payload = module.group_quotes_payload("2330,1000", fetcher=mis_fetcher())   # 2330 鎖在漲停 110、1000 成交 101
        ticks = {
            "2330": self._tick("2330", 109.5, 5100),    # 漲停打開：旗標要拿掉
            "1000": self._tick("1000", 110.0, 830),     # 剛鎖上漲停：要補上旗標
        }
        got = module.overlay_realtime_ticks(payload, ticks)["quotes"]
        self.assertFalse(got["2330"]["limitUp"])
        self.assertTrue(got["1000"]["limitUp"])
        self.assertFalse(got["1000"]["limitDown"])

    def test_endpoint_overlays_ticks(self) -> None:
        class FakeService:
            def get_stock_quotes(self, codes):
                return {"2317": GroupQuotesTests._tick("2317", 104.0, 850)}

        client = TestClient(persistent_app.app)
        with patch.object(module, "_default_fetcher", mis_fetcher()), patch.object(persistent_app, "get_quote_service", return_value=FakeService()):
            got = client.get("/api/hub/group-quotes?codes=2330,2317").json()
        self.assertEqual(got["quotes"]["2317"]["price"], 104.0)
        self.assertEqual(got["quotes"]["2330"]["price"], 110.0)
        module._reset_group_quotes()

        def boom():
            raise RuntimeError("Shioaji 沒登入")

        with patch.object(module, "_default_fetcher", mis_fetcher()), patch.object(persistent_app, "get_quote_service", side_effect=boom):
            fallback = client.get("/api/hub/group-quotes?codes=2330,2317").json()
        self.assertEqual(fallback["quotes"]["2317"]["price"], 101.0)   # 逐筆拿不到就維持證交所那份


if __name__ == "__main__":
    unittest.main()
