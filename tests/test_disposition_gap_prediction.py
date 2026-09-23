"""disposition_gap_prediction.py：連續2天(不多不少)命中第一款才算「還差1次」、明天
收盤價門檻反推(32%/25%+價差兩個子條件挑較容易達成的、正確處理漲跌方向)、差幅門檻
過濾掉不會用到的子條件、easy標籤跟人類可讀說明。第九/十款的成交量門檻：不依賴當天
資料(所以下一個交易日整天有效)、差幅在門檻值本身上檢查、第十款兩個子條件(AND)取
較嚴格的那個。第十一款：創6日新高/新低兩條路徑各自反推、級距門檻逐級距掃描收斂
(不能簡單套不動點迭代，"創新低"方向會在級距邊界震盪)、超過台股單日漲跌幅限制的
門檻不列入結果。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import database
from database import get_connection, initialize_database
from disposition_fundamentals_store import save_fundamentals_rows
from disposition_gap_prediction import (
    _build_clause_11_prediction,
    _build_prediction,
    _clause_1_threshold,
    _solve_clause_11_down,
    _solve_clause_11_up,
    build_clause_11_gap_predictions,
    build_gap_predictions,
    build_volume_gap_predictions,
    clause_9_threshold_volume,
    clause_10_threshold_volume,
    clause_11_threshold_close,
    find_path1_near_miss,
)
from disposition_prediction import save_clause_results
from disposition_rules import CHECKERS, ClauseResult


def _business_days_ending(end: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _all_clauses_result(fired_clauses: set[str]) -> list[ClauseResult]:
    return [ClauseResult(clause, clause in fired_clauses, "測試假資料") for clause in CHECKERS]


class Clause1ThresholdTests(unittest.TestCase):
    def test_up_direction_small_price_picks_subcondition_1(self):
        closes = [100.0, 100.0, 100.0, 100.0, 100.0]  # closes[-5]=ref=100
        result = _clause_1_threshold(closes, today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        self.assertIsNotNone(result)
        threshold, direction, ref = result
        self.assertEqual(direction, "up")
        self.assertAlmostEqual(ref, 100.0)
        self.assertAlmostEqual(threshold, 132.0)  # 100*1.32 < max(100*1.25, 100+50)=150

    def test_up_direction_large_price_picks_subcondition_2(self):
        closes = [1000.0, 1000.0, 1000.0, 1000.0, 1000.0]
        result = _clause_1_threshold(closes, today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        threshold, direction, _ref = result
        self.assertAlmostEqual(threshold, 1250.0)  # max(1000*1.25, 1000+50)=1250 < 1000*1.32=1320

    def test_down_direction_mirrors_up(self):
        closes = [100.0, 100.0, 100.0, 100.0, 100.0]
        result = _clause_1_threshold(closes, today_change_6d_pct=-10.0, peer_avg_6d_pct=None)
        threshold, direction, ref = result
        self.assertEqual(direction, "down")
        self.assertAlmostEqual(threshold, 68.0)  # max(68.0, min(75,50)=50) = 68.0，跌得少的那個較容易達成

    def test_returns_none_with_fewer_than_5_closes(self):
        result = _clause_1_threshold([100.0, 100.0, 100.0], today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        self.assertIsNone(result)

    def test_diff_filter_excludes_subcondition_1_when_peer_avg_too_close(self):
        """peer_avg=45時，32%門檻跟peer_avg差幅只有13(<20，子條件一不適用)，
        但25%門檻差幅剛好20(子條件二仍適用)——確認只用子條件二算出來的門檻。"""
        closes = [100.0, 100.0, 100.0, 100.0, 100.0]
        result = _clause_1_threshold(closes, today_change_6d_pct=10.0, peer_avg_6d_pct=45.0)
        threshold, _direction, _ref = result
        self.assertAlmostEqual(threshold, 150.0)  # 只剩子條件二：max(125, 150)=150

    def test_returns_none_when_both_subconditions_filtered_out(self):
        closes = [100.0, 100.0, 100.0, 100.0, 100.0]
        result = _clause_1_threshold(closes, today_change_6d_pct=10.0, peer_avg_6d_pct=30.0)
        self.assertIsNone(result)

    def test_zero_reference_close_returns_none(self):
        result = _clause_1_threshold([0.0, 0.0, 0.0, 0.0, 0.0], today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        self.assertIsNone(result)


class BuildPredictionTests(unittest.TestCase):
    def test_easy_label_when_threshold_at_or_below_today_close(self):
        closes = [100.0] * 5
        prediction = _build_prediction("2330", closes, today_close=140.0, today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        self.assertIsNotNone(prediction)
        self.assertTrue(prediction.easy)
        self.assertIn("收平盤或收紅", prediction.detail)
        self.assertEqual(prediction.direction, "up")

    def test_specific_pct_label_when_real_move_needed(self):
        closes = [100.0] * 5
        prediction = _build_prediction("2330", closes, today_close=100.0, today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        self.assertFalse(prediction.easy)
        self.assertIn("漲幅", prediction.detail)
        self.assertAlmostEqual(prediction.change_pct_from_today, 32.0)  # threshold132 vs today_close100

    def test_none_when_threshold_computation_fails(self):
        prediction = _build_prediction("2330", [100.0, 100.0], today_close=100.0, today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        self.assertIsNone(prediction)


class FindPath1NearMissTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.today = date(2026, 9, 23)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed(self, code: str, date_to_clauses: dict[date, set[str]]) -> None:
        for trade_date, clauses in date_to_clauses.items():
            save_clause_results(trade_date.isoformat(), code, _all_clauses_result(clauses))

    def test_finds_stock_with_exactly_2_consecutive_hits(self):
        days = _business_days_ending(self.today, 2)
        self._seed("2330", {d: {"一"} for d in days})
        result = find_path1_near_miss({"2330"}, self.today.isoformat())
        self.assertEqual(result, {"2330"})

    def test_excludes_stock_already_at_3_consecutive_hits(self):
        """已經連續3天，路徑一已經真的觸發了，不是「還差1次」。"""
        days = _business_days_ending(self.today, 3)
        self._seed("2330", {d: {"一"} for d in days})
        result = find_path1_near_miss({"2330"}, self.today.isoformat())
        self.assertEqual(result, set())

    def test_excludes_stock_with_only_1_hit(self):
        days = _business_days_ending(self.today, 2)
        self._seed("2330", {days[0]: set(), days[1]: {"一"}})
        result = find_path1_near_miss({"2330"}, self.today.isoformat())
        self.assertEqual(result, set())

    def test_excludes_stock_where_today_did_not_fire(self):
        """昨天中、今天沒中——不是「連續到今天」，序列已經斷了。"""
        days = _business_days_ending(self.today, 2)
        self._seed("2330", {days[0]: {"一"}, days[1]: set()})
        result = find_path1_near_miss({"2330"}, self.today.isoformat())
        self.assertEqual(result, set())


class BuildGapPredictionsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.today = date(2026, 9, 23)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed_bars(self, code: str, closes_by_date: dict[date, float]) -> None:
        with get_connection() as connection:
            rows = [
                (code, d.isoformat() + "T00:00:00+00:00", c, c, c, c, 1000.0)
                for d, c in closes_by_date.items()
            ]
            connection.executemany(
                "INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(stock_code, bar_time) DO UPDATE SET close = excluded.close",
                rows,
            )

    def test_empty_when_no_near_miss_stocks(self):
        result = build_gap_predictions(self.today.isoformat(), {"2330"})
        self.assertEqual(result, [])

    def test_end_to_end_produces_prediction_for_near_miss_stock(self):
        days = _business_days_ending(self.today, 2)
        save_clause_results(days[0].isoformat(), "2330", _all_clauses_result({"一"}))
        save_clause_results(days[1].isoformat(), "2330", _all_clauses_result({"一"}))
        # 需要至少6天的bars_1d歷史讓change_6d_pct算得出來，加上明天窗口起點要有closes[-5]。
        history_days = _business_days_ending(self.today, 8)
        closes_by_date = {d: 100.0 + i for i, d in enumerate(history_days)}
        self._seed_bars("2330", closes_by_date)
        result = build_gap_predictions(self.today.isoformat(), {"2330"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "2330")
        self.assertEqual(result[0].clause, "一")

    def test_stock_missing_from_market_snapshot_is_skipped_not_erroring(self):
        days = _business_days_ending(self.today, 2)
        save_clause_results(days[0].isoformat(), "9999", _all_clauses_result({"一"}))
        save_clause_results(days[1].isoformat(), "9999", _all_clauses_result({"一"}))
        # 沒有補bars_1d，snapshot裡不會有這檔的metrics。
        result = build_gap_predictions(self.today.isoformat(), {"9999"})
        self.assertEqual(result, [])


class Clause9ThresholdVolumeTests(unittest.TestCase):
    def test_computes_5x_avg60(self):
        self.assertAlmostEqual(clause_9_threshold_volume(1000.0, None), 5000.0)

    def test_diff_ok_peer_avg_keeps_5x(self):
        # |5.0-0.5|=4.5>=4，5倍這個點差幅就過了，門檻維持5倍。
        self.assertAlmostEqual(clause_9_threshold_volume(1000.0, 0.5), 5000.0)

    def test_diff_dead_zone_raises_threshold_instead_of_none(self):
        # |5.0-2.0|=3.0<4，5倍這個點差幅不夠，但不代表無解——把倍數推高到
        # peer_avg+4=6.0倍，差幅就變成|6-2|=4>=4，同時滿足「>=5倍」跟差幅門檻。
        self.assertAlmostEqual(clause_9_threshold_volume(1000.0, 2.0), 6000.0)

    def test_zero_or_none_avg_returns_none(self):
        self.assertIsNone(clause_9_threshold_volume(0.0, None))
        self.assertIsNone(clause_9_threshold_volume(None, None))

    def test_subcondition_2_can_be_easier_than_subcondition_1(self):
        # 子條件二(明天6日均量/60日均量>=5倍)：前5天已經累積28000張，60日均量
        # 1000張，明天6日均量要達到5倍(5000張總量)只需要再6*5*1000-28000=2000張，
        # 比子條件一的5*1000=5000張容易，取兩者較小值。
        threshold = clause_9_threshold_volume(
            1000.0, None, cum_volume_prior_5d_lots=28000.0, peer_avg_ratio_6d_60d=None,
        )
        self.assertAlmostEqual(threshold, 2000.0)

    def test_subcondition_2_dead_zone_also_raises_threshold(self):
        # 子條件二peer_avg=2.0一樣卡死區，門檻倍數墊高到6.0：
        # 6*6.0*1000-28000=8000，比子條件一(5000)嚴格，改取子條件一。
        threshold = clause_9_threshold_volume(
            1000.0, None, cum_volume_prior_5d_lots=28000.0, peer_avg_ratio_6d_60d=2.0,
        )
        self.assertAlmostEqual(threshold, 5000.0)


class Clause10ThresholdVolumeTests(unittest.TestCase):
    def test_cum_turnover_subcondition_is_binding(self):
        # 發行股數10000張，前5日已經有3000張量，今日週轉率10%只需要1000張，
        # 但6日累積週轉率50%還需要(10000*0.5-3000)=2000張，2000>1000所以以它為準。
        result = clause_10_threshold_volume(10000.0, 3000.0, None, None)
        self.assertIsNotNone(result)
        threshold, binding = result
        self.assertAlmostEqual(threshold, 2000.0)
        self.assertEqual(binding, "6日累積週轉率")

    def test_today_turnover_subcondition_is_binding_when_cum_already_satisfied(self):
        # 前5日已經有5500張(超過股本的50%)，累積子條件早就滿足，門檻降回只看當日週轉率。
        result = clause_10_threshold_volume(10000.0, 5500.0, None, None)
        threshold, binding = result
        self.assertAlmostEqual(threshold, 1000.0)
        self.assertEqual(binding, "當日週轉率")

    def test_diff_dead_zone_raises_threshold_instead_of_none(self):
        # |10-8|=2<5，當日週轉率門檻墊高到8+5=13%(1300張)；|50-45|=5<40，累積
        # 週轉率門檻墊高到45+40=85%(8500張，扣掉前5天3000張還需要5500張)。
        # 5500>1300，累積週轉率仍是較嚴格的子條件——不是像修正前那樣直接判定無解。
        result = clause_10_threshold_volume(10000.0, 3000.0, 8.0, 45.0)
        self.assertIsNotNone(result)
        threshold, binding = result
        self.assertAlmostEqual(threshold, 5500.0)
        self.assertEqual(binding, "6日累積週轉率")

    def test_zero_or_none_shares_outstanding_returns_none(self):
        self.assertIsNone(clause_10_threshold_volume(0.0, 3000.0, None, None))
        self.assertIsNone(clause_10_threshold_volume(None, 3000.0, None, None))


class BuildVolumeGapPredictionsTests(unittest.TestCase):
    """端到端測試都刻意讓每個測試只放1檔股票進codes——peer_avg是跨全部bars_1d(第九款)
    或跨呼叫時codes集合(第十款)算的橫斷面平均，放2檔會互相稀釋拉走差幅門檻(稀釋只會把
    自我參照的ratio拉向1.0，數學上永遠到不了通過門檻需要的<=1.0，見clause_9_threshold_
    volume的docstring)，所以用「只有自己1檔」讓peer_avg自我參照、刻意取極端的量能讓
    自我參照的差幅也能通過門檻，藉此獨立驗證每個情境。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.today = date(2026, 9, 23)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed_volumes(self, code: str, volumes_by_date: dict[date, float], close: float = 100.0) -> None:
        with get_connection() as connection:
            rows = [
                (code, d.isoformat() + "T00:00:00+00:00", close, close, close, close, v)
                for d, v in volumes_by_date.items()
            ]
            connection.executemany(
                "INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(stock_code, bar_time) DO UPDATE SET volume = excluded.volume",
                rows,
            )

    def test_clause_9_qualifying_stock_appears_with_correct_threshold(self):
        # 60天中前59天量100張、今天暴增到1100張：ratio約9.43，自我參照的peer_avg也是
        # 9.43(這檔是全庫唯一有60天資料的)，|5.0-9.43|=4.43>=4.0通過差幅門檻；量本身也
        # 遠超過門檻一半，兩個獨立的過濾條件都通過才會出現在結果裡。
        days = _business_days_ending(self.today, 60)
        volumes_by_date = {d: 100.0 for d in days[:-1]}
        volumes_by_date[days[-1]] = 1100.0
        self._seed_volumes("2330", volumes_by_date)

        result = build_volume_gap_predictions(self.today.isoformat(), {"2330"})

        avg60 = (59 * 100.0 + 1100.0) / 60
        expected_threshold = 5.0 * avg60
        self.assertEqual(len(result), 1)
        prediction = result[0]
        self.assertEqual(prediction.code, "2330")
        self.assertEqual(prediction.clause, "九")
        self.assertAlmostEqual(prediction.threshold_volume, round(expected_threshold, 0))
        self.assertAlmostEqual(prediction.reference_volume, 1100.0)

    def test_clause_9_quiet_stock_is_excluded(self):
        # 60天量完全平穩(ratio=1.0)：自我參照差幅門檻剛好卡在邊界(|5-1|=4.0)算過，
        # 門檻仍然算得出來，但量本身離門檻一半還很遠，靠獨立的量能過濾條件排除，
        # 確認兩層過濾不會互相蓋過。
        days = _business_days_ending(self.today, 60)
        volumes_by_date = {d: 100.0 for d in days}
        self._seed_volumes("2317", volumes_by_date)

        result = build_volume_gap_predictions(self.today.isoformat(), {"2317"})

        self.assertEqual(result, [])

    def test_clause_10_qualifying_stock_appears_with_correct_threshold(self):
        # 只seed5天(不足6天)，cum_turnover_6d_pct算不出來，第二個差幅門檻(累積週轉率)
        # 自動略過；發行股數10,000張、當日週轉率16%，自我參照peer_avg也是16%，
        # |10-16|=6>=5通過第一個差幅門檻。累積量8000張已經超過股本一半，累積子條件
        # 不再卡關，門檻回到只看當日週轉率：10000*0.10=1000張。
        days = _business_days_ending(self.today, 5)
        volumes_by_date = {d: 1600.0 for d in days}
        self._seed_volumes("3450", volumes_by_date)
        save_fundamentals_rows([
            {"code": "3450", "tradeDate": self.today.isoformat(), "marketValue": 1_000_000_000.0},
        ])

        result = build_volume_gap_predictions(self.today.isoformat(), {"3450"})

        self.assertEqual(len(result), 1)
        prediction = result[0]
        self.assertEqual(prediction.code, "3450")
        self.assertEqual(prediction.clause, "十")
        self.assertAlmostEqual(prediction.threshold_volume, 1000.0)
        self.assertAlmostEqual(prediction.reference_volume, 1600.0)

    def test_clause_10_low_turnover_stock_is_excluded(self):
        # 當日週轉率只有1%，遠低於10%門檻——不是差幅死區邊界，是真的量太少，
        # 就算死區把門檻墊高，量本身也遠遠不到門檻一半，仍然被排除。
        days = _business_days_ending(self.today, 5)
        volumes_by_date = {d: 100.0 for d in days}
        self._seed_volumes("1101", volumes_by_date)
        save_fundamentals_rows([
            {"code": "1101", "tradeDate": self.today.isoformat(), "marketValue": 1_000_000_000.0},
        ])

        result = build_volume_gap_predictions(self.today.isoformat(), {"1101"})

        self.assertEqual(result, [])

    def test_clause_10_dead_zone_gate_still_includes_stock_with_raised_threshold(self):
        # 當日週轉率8%，自我參照peer_avg也是8%，差幅死區把門檻墊高到13%(1300張)，
        # 不是直接判定無解排除掉——800張量已經超過門檻一半，應該出現在結果裡。
        days = _business_days_ending(self.today, 5)
        volumes_by_date = {d: 800.0 for d in days}
        self._seed_volumes("1101", volumes_by_date)
        save_fundamentals_rows([
            {"code": "1101", "tradeDate": self.today.isoformat(), "marketValue": 1_000_000_000.0},
        ])

        result = build_volume_gap_predictions(self.today.isoformat(), {"1101"})

        self.assertEqual(len(result), 1)
        prediction = result[0]
        self.assertEqual(prediction.clause, "十")
        self.assertAlmostEqual(prediction.threshold_volume, 1300.0)
        self.assertAlmostEqual(prediction.reference_volume, 800.0)


class Clause11SolveExtremeThresholdTests(unittest.TestCase):
    def test_up_within_same_tier(self):
        # tier(1550)=1，threshold=300；candidate=max(1550,1550+300)=1850，
        # 1850仍在同一級距(1000~2000)內，不用跨級距。
        self.assertAlmostEqual(_solve_clause_11_up(1550.0, 1550.0), 1850.0)

    def test_up_crosses_tier_boundary(self):
        # 第一次猜candidate=1900+300=2200(超出tier 1上界2000)，換成tier 2
        # (threshold=450)重算：1900+450=2350，落在tier 2(2000~3000)內，收斂。
        self.assertAlmostEqual(_solve_clause_11_up(1900.0, 1900.0), 2350.0)

    def test_up_known_max_dominates_price_diff(self):
        # 某天大漲讓known_max=2500遠高於ref(1100)+threshold，門檻由known_max
        # 決定，不是由價差反推。
        self.assertAlmostEqual(_solve_clause_11_up(1100.0, 2500.0), 2500.0)

    def test_up_below_1000_returns_none(self):
        self.assertIsNone(_solve_clause_11_up(100.0, 100.0))

    def test_down_within_same_tier(self):
        self.assertAlmostEqual(_solve_clause_11_down(2500.0, 2500.0), 2050.0)

    def test_down_crosses_tier_boundary(self):
        """這是用不動點迭代(candidate=ref-threshold(candidate))會在1900跟2050
        之間無限震盪、永遠收斂不了的情境——因為級距往下跳時門檻反而變小，
        推著候選價格往回跳。改成逐級距掃描、候選價格clamp在該級距範圍內，
        才能正確收斂到2000。"""
        self.assertAlmostEqual(_solve_clause_11_down(2350.0, 2350.0), 2000.0)

    def test_down_known_min_dominates_price_diff(self):
        self.assertAlmostEqual(_solve_clause_11_down(3000.0, 1200.0), 1200.0)

    def test_down_to_1000_or_below_returns_none(self):
        self.assertIsNone(_solve_clause_11_down(1000.0, 800.0))


class Clause11ThresholdCloseTests(unittest.TestCase):
    def test_returns_none_with_fewer_than_6_closes(self):
        self.assertIsNone(clause_11_threshold_close([100.0] * 5))

    def test_picks_up_when_closer_to_today(self):
        # ref=1550(closes[-5])，今天已經是known_max(1900)，"創新高"門檻剛好等於
        # 今天收盤價(距離0)；"創新低"門檻(1250)離今天(1900)遠得多，選較近的up。
        closes = [1000.0, 1550.0, 1550.0, 1550.0, 1550.0, 1900.0]
        threshold, direction = clause_11_threshold_close(closes)
        self.assertEqual(direction, "up")
        self.assertAlmostEqual(threshold, 1900.0)

    def test_picks_down_when_closer_to_today(self):
        closes = [1000.0, 2500.0, 2500.0, 2500.0, 2500.0, 2050.0]
        threshold, direction = clause_11_threshold_close(closes)
        self.assertEqual(direction, "down")
        self.assertAlmostEqual(threshold, 2050.0)


class BuildClause11PredictionTests(unittest.TestCase):
    def test_easy_label_when_threshold_at_todays_close(self):
        closes = [1000.0, 1550.0, 1550.0, 1550.0, 1550.0, 1900.0]
        prediction = _build_clause_11_prediction("2330", closes)
        self.assertIsNotNone(prediction)
        self.assertTrue(prediction.easy)
        self.assertEqual(prediction.direction, "up")
        self.assertAlmostEqual(prediction.change_pct_from_today, 0.0)
        self.assertIn("創6日新高", prediction.detail)

    def test_specific_pct_label_when_real_move_needed(self):
        closes = [1000.0, 1550.0, 1550.0, 1550.0, 1550.0, 1700.0]
        prediction = _build_clause_11_prediction("2330", closes)
        self.assertFalse(prediction.easy)
        self.assertAlmostEqual(prediction.threshold_close, 1850.0)
        self.assertAlmostEqual(prediction.change_pct_from_today, 8.82, places=2)
        self.assertIn("漲幅", prediction.detail)

    def test_none_when_move_exceeds_daily_limit(self):
        # up跟down都需要約19.35%的單日變動，超過台股±10%單日漲跌幅限制，
        # 明天一天到不了，不列入結果。
        closes = [1000.0, 1550.0, 1550.0, 1550.0, 1550.0, 1550.0]
        self.assertIsNone(_build_clause_11_prediction("2330", closes))

    def test_none_when_insufficient_history(self):
        self.assertIsNone(_build_clause_11_prediction("2330", [100.0] * 5))


class BuildClause11GapPredictionsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.today = date(2026, 9, 23)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed_bars(self, code: str, closes_by_date: dict[date, float]) -> None:
        with get_connection() as connection:
            rows = [
                (code, d.isoformat() + "T00:00:00+00:00", c, c, c, c, 1000.0)
                for d, c in closes_by_date.items()
            ]
            connection.executemany(
                "INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(stock_code, bar_time) DO UPDATE SET close = excluded.close",
                rows,
            )

    def test_qualifying_stock_appears(self):
        days = _business_days_ending(self.today, 6)
        closes = [1000.0, 1550.0, 1550.0, 1550.0, 1550.0, 1900.0]
        self._seed_bars("2330", dict(zip(days, closes)))

        result = build_clause_11_gap_predictions(self.today.isoformat(), {"2330"})

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "2330")
        self.assertEqual(result[0].clause, "十一")
        self.assertAlmostEqual(result[0].threshold_close, 1900.0)

    def test_stock_needing_over_10pct_move_is_excluded(self):
        days = _business_days_ending(self.today, 6)
        closes = [1000.0, 1550.0, 1550.0, 1550.0, 1550.0, 1550.0]
        self._seed_bars("2317", dict(zip(days, closes)))

        result = build_clause_11_gap_predictions(self.today.isoformat(), {"2317"})

        self.assertEqual(result, [])

    def test_stock_missing_from_series_is_skipped_not_erroring(self):
        result = build_clause_11_gap_predictions(self.today.isoformat(), {"9999"})
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
