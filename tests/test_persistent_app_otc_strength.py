from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

import persistent_app

TW = timezone(timedelta(hours=8))


def tpe_ms(hour: int, minute: int, *, day) -> int:
    return int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=TW).timestamp() * 1000)


def bar_at(minutes_after_open: int, *, day, low: float, close: float) -> dict:
    """從09:00算起，過minutes_after_open分鐘的那根5分K bar。"""
    hour, minute = divmod(9 * 60 + minutes_after_open, 60)
    ts = tpe_ms(hour, minute, day=day)
    return {"ts": ts, "open": close, "high": close, "low": low, "close": close, "volume": 0, "tick_count": 1}


def bar(hour: int, minute: int, *, day, low: float, close: float) -> dict:
    return {"ts": tpe_ms(hour, minute, day=day), "open": close, "high": close, "low": low, "close": close, "volume": 0, "tick_count": 1}


class FakeHub:
    def __init__(self, bars: list[dict], quote_close: float | None):
        self._bars = bars
        self._quote_close = quote_close

    def get_bars_5m(self, include_current: bool = True) -> list[dict]:
        return list(self._bars)

    def get_latest_quote(self):
        return {"close": self._quote_close} if self._quote_close is not None else None

    def get_status(self) -> dict:
        return {"trade_date": datetime.now(TW).strftime("%Y-%m-%d")}


class OtcIndexStrengthEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(persistent_app.app)
        self.today = datetime.now(TW).date()
        self.yesterday = self.today - timedelta(days=1)

    def test_holds_previous_session_until_next_open(self) -> None:
        # 使用者 2026-09-25：過午夜不能變成「資料蒐集中」，上一個交易日收盤時的判斷要留到下一個交易日 08:45。
        yesterday_bars = [bar_at(i * 5, day=self.yesterday, low=90.0, close=100.0) for i in range(20)]
        yesterday_bars[2]["low"] = 95.0
        yesterday_bars[-1]["close"] = 101.0
        hub = FakeHub(yesterday_bars, quote_close=None)
        with patch("persistent_app.get_otc_index_hub", return_value=hub), \
                patch("persistent_app._should_hold_previous_ranking", return_value=True):
            data = self.client.get("/api/hub/index/otc/strength").json()
        self.assertTrue(data["ready"])
        self.assertEqual(data["tradeDate"], self.yesterday.isoformat())
        self.assertEqual(data["heldFrom"], self.today.isoformat())
        self.assertEqual(data["price"], 101.0)                       # 那天最後一根 5 分 K 的收盤
        self.assertEqual(data["refLow"], 95.0)                       # 那天第 3 根的低點
        self.assertEqual(data["label"], "強多")
        self.assertEqual(data["priceSource"], "lastBar")
        with patch("persistent_app.get_otc_index_hub", return_value=hub), \
                patch("persistent_app._should_hold_previous_ranking", return_value=False):
            data = self.client.get("/api/hub/index/otc/strength").json()
        self.assertFalse(data["ready"])                              # 08:45 以後就等今天的資料

    def test_ma20_uses_multi_day_bars_when_today_alone_has_fewer_than_20(self) -> None:
        # 使用者回報的核心情境：今天只走了2根5分K，靠歷史(昨天)補齊到20根，
        # 應該要能直接ready，不用等今天自己累積滿20根。
        yesterday_bars = [bar_at(i * 5, day=self.yesterday, low=90.0, close=100.0) for i in range(18)]
        today_bars = [
            bar(9, 0, day=self.today, low=95.0, close=101.0),
            bar(9, 5, day=self.today, low=96.0, close=102.0),
        ]
        hub = FakeHub(yesterday_bars + today_bars, quote_close=110.0)
        with patch("persistent_app.get_otc_index_hub", return_value=hub):
            resp = self.client.get("/api/hub/index/otc/strength")
        data = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["ready"], data)
        # 18根昨天(close=100.0)+2根今天(close=101.0/102.0)=20根，MA20要跨日算。
        self.assertAlmostEqual(data["ma20"], (18 * 100.0 + 101.0 + 102.0) / 20, places=2)
        # 櫃買今天的漲跌幅：昨收＝昨天最後一根 5 分 K 收盤 100，即時 110 → +10%（盤中打 333 用）
        self.assertEqual(data["prevClose"], 100.0)
        self.assertEqual(data["changePct"], 10.0)

    def test_ref_bar_is_todays_bar_not_index_2_of_the_full_multi_day_list(self) -> None:
        # 「第3根5K低點」語意上是今天自己的第3根bar，不能因為歷史K棒被
        # 接在前面，就被昨天index=2那根頂替掉。
        yesterday_bars = [bar_at(i * 5, day=self.yesterday, low=1.0, close=50.0) for i in range(18)]  # 18根，low全部是1.0
        today_bars = [
            bar(9, 0, day=self.today, low=200.0, close=250.0),
            bar(9, 5, day=self.today, low=201.0, close=251.0),
            bar(9, 10, day=self.today, low=202.5, close=252.0),  # 今天第3根：這根的low才是ref_low
            bar(9, 15, day=self.today, low=203.0, close=253.0),
        ]
        hub = FakeHub(yesterday_bars + today_bars, quote_close=300.0)
        with patch("persistent_app.get_otc_index_hub", return_value=hub):
            resp = self.client.get("/api/hub/index/otc/strength")
        data = resp.json()
        self.assertTrue(data["ready"], data)
        self.assertEqual(data["refLow"], 202.5)
        self.assertNotEqual(data["refLow"], 1.0)

    def test_not_ready_when_fewer_than_20_bars_total(self) -> None:
        hub = FakeHub([bar(9, 0, day=self.today, low=95.0, close=100.0)], quote_close=100.0)
        with patch("persistent_app.get_otc_index_hub", return_value=hub):
            resp = self.client.get("/api/hub/index/otc/strength")
        data = resp.json()
        self.assertFalse(data["ready"])

    def test_without_live_quote_uses_todays_last_bar_close(self) -> None:
        # 收盤後、或剛重啟還沒收到第一筆報價：今天已經有 5 分 K 就用最後一根的收盤價判定，
        # 不要整個晚上都顯示「資料蒐集中」。
        bars = [bar_at(i * 5, day=self.today, low=90.0, close=100.0) for i in range(19)]
        bars.append(bar_at(95, day=self.today, low=99.0, close=110.0))
        hub = FakeHub(bars, quote_close=None)
        with patch("persistent_app.get_otc_index_hub", return_value=hub):
            resp = self.client.get("/api/hub/index/otc/strength")
        data = resp.json()
        self.assertTrue(data["ready"], data)
        self.assertEqual(data["priceSource"], "lastBar")
        self.assertEqual(data["price"], 110.0)
        self.assertEqual(data["label"], "強多")

    def test_not_ready_reason_shows_why_the_backfill_failed(self) -> None:
        # 使用者只看得到小工具上的一行字：補齊失敗的原因要直接寫在 reason 裡。
        class BrokenHub(FakeHub):
            def get_status(self) -> dict:
                return {
                    "trade_date": datetime.now(TW).strftime("%Y-%m-%d"), "bootstrap_ok": False,
                    "bootstrap_error": "櫃買指數歷史 Kbars 補齊失敗: quota；備援 yahoo: HTTP 404、yahoo5m: 0 根、finmind: 0 根",
                }

        bars = [bar_at(i * 5, day=self.today, low=90.0, close=100.0) for i in range(10)]
        hub = BrokenHub(bars, quote_close=None)
        with patch("persistent_app.get_otc_index_hub", return_value=hub), \
                patch("persistent_app._kick_otc_index_bootstrap", lambda hub: None):
            resp = self.client.get("/api/hub/index/otc/strength")
        data = resp.json()
        self.assertFalse(data["ready"])
        self.assertIn("5分K 10/20 根", data["reason"])
        self.assertIn("補齊失敗", data["reason"])
        self.assertIn("yahoo: HTTP 404", data["reason"])

    def test_not_ready_when_bars_are_all_historical_with_none_from_today(self) -> None:
        # 有20根以上的歷史bar、也有quote，但今天自己一根bar都還沒有——
        # 這種狀態下沒有today_bars可以當ref_bar，不該假裝ready。
        # （2026-09-25 起：08:45 之前會沿用上一個交易日，所以這裡明確指定「已經過 08:45」的情境。）
        bars = [bar_at(i * 5, day=self.yesterday, low=90.0, close=100.0) for i in range(20)]
        hub = FakeHub(bars, quote_close=100.0)
        with patch("persistent_app.get_otc_index_hub", return_value=hub), \
                patch("persistent_app._should_hold_previous_ranking", return_value=False):
            resp = self.client.get("/api/hub/index/otc/strength")
        data = resp.json()
        self.assertFalse(data["ready"])


if __name__ == "__main__":
    unittest.main()
