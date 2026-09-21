from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from otc_index import (
    aggregate_1m_to_5m,
    index_name_score,
    is_regular_otc_session,
    normalize_kbars_1m,
    shioaji_kbar_close_to_start_ms,
    timestamp_to_ms,
)
from otc_index_hub import OtcIndexHub

TW = timezone(timedelta(hours=8))


def tpe_ms(hour: int, minute: int, *, day: date = date(2026, 8, 7)) -> int:
    return int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=TW).timestamp() * 1000)


def shioaji_wall_ns(hour: int, minute: int, *, day: date = date(2026, 8, 7)) -> int:
    """建立 Shioaji KBars.ts 類型的「無時區本地牆鐘」ns 值。"""
    wall = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
    return int(wall.timestamp() * 1_000_000_000)


class OtcIndexHelperTests(unittest.TestCase):
    def test_index_name_score_prefers_main_otc_index(self) -> None:
        self.assertGreater(
            index_name_score("櫃檯買賣發行量加權股價指數", "OTC"),
            9000,
        )
        self.assertGreater(index_name_score("櫃買指數", "OTC"), 8000)
        self.assertGreater(index_name_score("上櫃指數", "OTC"), 8000)
        self.assertEqual(index_name_score("櫃買薪酬指數", "OTC"), 0)
        self.assertLess(index_name_score("發行量加權股價指數", "TSE"), 0)

    def test_general_timestamp_to_ms_handles_epoch_and_datetimes(self) -> None:
        ms = tpe_ms(9, 0)
        self.assertEqual(timestamp_to_ms(ms), ms)
        self.assertEqual(
            timestamp_to_ms(datetime(2026, 8, 7, 9, 0, tzinfo=TW)),
            ms,
        )

    def test_official_shioaji_kbar_example_is_taipei_close_time(self) -> None:
        # Shioaji 1.7 官方文件：1779094860000000000 -> 2026-05-18 09:01:00。
        # HanStock 必須轉成 09:00~09:01 這根 K 的 start timestamp。
        official_raw_ns = 1779094860000000000
        expected = int(datetime(2026, 5, 18, 9, 0, tzinfo=TW).timestamp() * 1000)
        self.assertEqual(shioaji_kbar_close_to_start_ms(official_raw_ns), expected)

    def test_regular_session_excludes_preopen_and_1330_bucket(self) -> None:
        self.assertFalse(is_regular_otc_session(tpe_ms(8, 59)))
        self.assertTrue(is_regular_otc_session(tpe_ms(9, 0)))
        self.assertTrue(is_regular_otc_session(tpe_ms(13, 29)))
        self.assertFalse(is_regular_otc_session(tpe_ms(13, 30)))

    def test_normalize_kbars_converts_close_time_to_bar_start_and_filters_preopen(self) -> None:
        # raw 09:00 close -> 08:59 start（排除）
        # raw 09:01 close -> 09:00 start（保留）
        # raw 09:02 close -> 09:01 start（保留）
        kbars = {
            "ts": [
                shioaji_wall_ns(9, 0),
                shioaji_wall_ns(9, 1),
                shioaji_wall_ns(9, 2),
            ],
            "Open": [99.0, 100.0, 101.0],
            "High": [100.0, 102.0, 103.0],
            "Low": [98.0, 99.0, 100.0],
            "Close": [99.5, 101.0, 102.0],
            "Volume": [1, 2, 3],
        }
        rows = normalize_kbars_1m(
            kbars,
            trade_date="2026-08-07",
            include_current=False,
            now_ms=tpe_ms(10, 0),
        )
        self.assertEqual([row["ts"] for row in rows], [tpe_ms(9, 0), tpe_ms(9, 1)])

    def test_third_five_minute_bucket_starts_at_0910(self) -> None:
        # Shioaji 09:01~09:15 close labels 應還原成 09:00~09:14 bar starts，
        # 聚合後前三根必須是 09:00、09:05、09:10。
        kbars = {
            "ts": [shioaji_wall_ns(9, minute) for minute in range(1, 16)],
            "Open": [100.0 + i for i in range(15)],
            "High": [101.0 + i for i in range(15)],
            "Low": [99.0 + i for i in range(15)],
            "Close": [100.5 + i for i in range(15)],
            "Volume": [1] * 15,
        }
        one_minute = normalize_kbars_1m(
            kbars,
            trade_date="2026-08-07",
            include_current=False,
            now_ms=tpe_ms(10, 0),
        )
        five_minute = aggregate_1m_to_5m(
            one_minute,
            include_current=False,
            now_ms=tpe_ms(10, 0),
        )
        self.assertEqual(
            [row["ts"] for row in five_minute[:3]],
            [tpe_ms(9, 0), tpe_ms(9, 5), tpe_ms(9, 10)],
        )
        self.assertEqual(five_minute[2]["low"], 109.0)

    def test_aggregate_1m_to_5m_preserves_ohlc(self) -> None:
        bars = []
        closes = [101.0, 100.5, 102.0, 103.0, 102.5]
        highs = [101.5, 102.0, 102.5, 104.0, 103.5]
        lows = [99.0, 99.5, 100.0, 101.0, 101.5]
        for index in range(5):
            bars.append({
                "ts": tpe_ms(9, index),
                "open": 100.0 + index,
                "high": highs[index],
                "low": lows[index],
                "close": closes[index],
                "volume": index + 1,
                "tick_count": 1,
            })
        rows = aggregate_1m_to_5m(bars, include_current=False, now_ms=tpe_ms(9, 10))
        self.assertEqual(len(rows), 1)
        bar = rows[0]
        self.assertEqual(bar["ts"], tpe_ms(9, 0))
        self.assertEqual(bar["open"], 100.0)
        self.assertEqual(bar["high"], 104.0)
        self.assertEqual(bar["low"], 99.0)
        self.assertEqual(bar["close"], 102.5)
        self.assertEqual(bar["volume"], 15)

    def test_aggregate_1m_to_5m_carries_total_amount_as_latest_snapshot_not_sum(self) -> None:
        # total_amount是「累計到當下」的金額快照(來自個股歷史逐筆回補)，
        # 5分K要取這5根1分K裡最新一根的值；如果誤加總會把大戶力%的分母
        # 灌水成5倍，百分比就會被錯誤地稀釋。
        bars = []
        for index in range(5):
            bars.append({
                "ts": tpe_ms(9, index),
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
                "volume": 10, "tick_count": 1,
                "main_net_volume": 0, "main_buy_volume": 0, "main_sell_volume": 0,
                "main_buy_amount": 0, "main_sell_amount": 0, "main_tick_count": 0,
                "main_force_available": True,
                "total_amount": (index + 1) * 10_000_000,
            })
        rows = aggregate_1m_to_5m(bars, include_current=False, now_ms=tpe_ms(9, 10))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total_amount"], 50_000_000)

    def test_aggregate_1m_to_5m_omits_total_amount_without_main_force_fields(self) -> None:
        # 一般Kbars/櫃買指數沒有主力欄位時，不能無中生有出一個total_amount。
        bars = [{
            "ts": tpe_ms(9, 0), "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.5, "volume": 10, "tick_count": 1,
        }]
        rows = aggregate_1m_to_5m(bars, include_current=False, now_ms=tpe_ms(9, 10))
        self.assertEqual(len(rows), 1)
        self.assertNotIn("total_amount", rows[0])

    def test_aggregate_1m_to_5m_falls_back_to_last_bar_that_actually_has_total_amount(self) -> None:
        # bucket最後一根1分K的ticks涵蓋範圍不一定剛好到；如果只看rows[-1]，
        # 沒對到ticks的那一分鐘會把前面明明有的累計金額蓋回0，重現「熱門股
        # 卡在資料累積中」的原始bug。這裡bucket裡有5根，只有前3根有
        # total_amount(來自逐筆回補)，後2根只是單純的Kbars OHLC(沒有ticks
        # 覆蓋到)，預期要取第3根(最新有值的)的3000萬，不是0。
        bars = []
        for index in range(5):
            bar = {
                "ts": tpe_ms(9, index),
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
                "volume": 10, "tick_count": 1,
            }
            if index < 3:
                bar.update({
                    "main_net_volume": 0, "main_buy_volume": 0, "main_sell_volume": 0,
                    "main_buy_amount": 0, "main_sell_amount": 0, "main_tick_count": 0,
                    "main_force_available": True,
                    "total_amount": (index + 1) * 10_000_000,
                })
            bars.append(bar)
        rows = aggregate_1m_to_5m(bars, include_current=False, now_ms=tpe_ms(9, 10))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total_amount"], 30_000_000)


class OtcIndexHubTests(unittest.TestCase):
    def test_seed_and_live_quote_continue_same_day(self) -> None:
        today = datetime.now(TW).date()
        hub = OtcIndexHub()
        seeded_1m = [
            {"ts": tpe_ms(9, 0, day=today), "open": 300.0, "high": 301.0, "low": 299.0, "close": 300.5, "volume": 0, "tick_count": 1},
        ]
        seeded_5m = [
            {"ts": tpe_ms(9, 0, day=today), "open": 300.0, "high": 302.0, "low": 299.0, "close": 301.0, "volume": 0, "tick_count": 5},
        ]
        trade_date = today.isoformat()
        hub.seed_today(seeded_1m, seeded_5m, trade_date)
        hub.on_quote({
            "code": "TEST_OTC",
            "close": 305.0,
            "volume": 0,
            "datetime": datetime(today.year, today.month, today.day, 9, 5, 10, tzinfo=TW).isoformat(),
        })
        bars = hub.get_bars_5m(include_current=True)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["ts"], tpe_ms(9, 0, day=today))
        self.assertEqual(bars[1]["ts"], tpe_ms(9, 5, day=today))
        self.assertEqual(bars[1]["close"], 305.0)


if __name__ == "__main__":
    unittest.main()
