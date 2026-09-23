"""disposition_rules.py 的每一款判定：邊界值(超過/在...以上的>vs≥區別)、除外情形、
缺資料(None)不誤判為觸發。門檻數字對照官方FID=FL007226原文，見disposition_rules.py
docstring。"""

from __future__ import annotations

import unittest

from disposition_rules import (
    CHECKERS,
    ClauseInputs,
    check_all_clauses,
    check_clause_1,
    check_clause_2,
    check_clause_3,
    check_clause_4,
    check_clause_6,
    check_clause_7,
    check_clause_9,
    check_clause_10,
    check_clause_11,
    check_clause_12,
    check_clause_13,
)


def _base(**overrides) -> ClauseInputs:
    return ClauseInputs(code="2330", close=overrides.pop("close", 100.0), **overrides)


class Clause1Tests(unittest.TestCase):
    def test_fires_above_32pct_with_peer_and_industry_diff(self):
        i = _base(change_6d_pct=33.0, change_6d_peer_avg_pct=10.0, change_6d_industry_avg_pct=10.0)
        result = check_clause_1(i)
        self.assertTrue(result.fired)

    def test_does_not_fire_at_exactly_32pct(self):
        """「超過32%」是嚴格大於，剛好32%不算。"""
        i = _base(change_6d_pct=32.0, change_6d_peer_avg_pct=5.0, change_6d_industry_avg_pct=5.0)
        self.assertFalse(check_clause_1(i).fired)

    def test_fires_above_25pct_with_price_diff_50(self):
        i = _base(
            change_6d_pct=26.0, change_6d_peer_avg_pct=5.0, change_6d_industry_avg_pct=5.0,
            price_diff_6d=50.0,
        )
        self.assertTrue(check_clause_1(i).fired)

    def test_25pct_path_needs_price_diff_at_least_50(self):
        i = _base(
            change_6d_pct=26.0, change_6d_peer_avg_pct=5.0, change_6d_industry_avg_pct=5.0,
            price_diff_6d=49.9,
        )
        self.assertFalse(check_clause_1(i).fired)

    def test_diff_threshold_is_20_inclusive(self):
        """「差幅在百分之二十以上」是≥20，剛好20要算。"""
        i = _base(change_6d_pct=33.0, change_6d_peer_avg_pct=13.0, change_6d_industry_avg_pct=13.0)
        self.assertTrue(check_clause_1(i).fired)

    def test_does_not_fire_when_close_below_5(self):
        i = _base(close=4.9, change_6d_pct=50.0, change_6d_peer_avg_pct=0.0, change_6d_industry_avg_pct=0.0)
        self.assertFalse(check_clause_1(i).fired)

    def test_does_not_fire_when_change_missing(self):
        self.assertFalse(check_clause_1(_base()).fired)

    def test_negative_change_uses_absolute_value(self):
        i = _base(change_6d_pct=-40.0, change_6d_peer_avg_pct=-5.0, change_6d_industry_avg_pct=-5.0)
        self.assertTrue(check_clause_1(i).fired)

    def test_missing_industry_average_does_not_block_peer_trigger(self):
        i = _base(change_6d_pct=40.0, change_6d_peer_avg_pct=5.0, change_6d_industry_avg_pct=None)
        self.assertTrue(check_clause_1(i).fired)


class Clause2Tests(unittest.TestCase):
    def test_fires_30day_window_up(self):
        i = _base(
            change_2d_30d_pct=101.0, change_2d_30d_peer_avg_pct=10.0, close_above_open_ref=True,
        )
        self.assertTrue(check_clause_2(i).fired)

    def test_does_not_fire_at_exactly_100pct(self):
        i = _base(change_2d_30d_pct=100.0, change_2d_30d_peer_avg_pct=10.0, close_above_open_ref=True)
        self.assertFalse(check_clause_2(i).fired)

    def test_up_move_requires_close_above_open_ref(self):
        i = _base(change_2d_30d_pct=101.0, change_2d_30d_peer_avg_pct=10.0, close_above_open_ref=False)
        self.assertFalse(check_clause_2(i).fired)

    def test_down_move_requires_close_below_open_ref(self):
        i = _base(change_2d_30d_pct=-101.0, change_2d_30d_peer_avg_pct=-10.0, close_above_open_ref=False)
        self.assertTrue(check_clause_2(i).fired)
        i2 = _base(change_2d_30d_pct=-101.0, change_2d_30d_peer_avg_pct=-10.0, close_above_open_ref=True)
        self.assertFalse(check_clause_2(i2).fired)

    def test_fires_60day_window(self):
        i = _base(change_2d_60d_pct=131.0, change_2d_60d_peer_avg_pct=15.0, close_above_open_ref=True)
        self.assertTrue(check_clause_2(i).fired)

    def test_fires_90day_window(self):
        i = _base(change_2d_90d_pct=161.0, change_2d_90d_peer_avg_pct=20.0, close_above_open_ref=True)
        self.assertTrue(check_clause_2(i).fired)

    def test_no_window_met_does_not_fire(self):
        i = _base(change_2d_30d_pct=50.0, change_2d_60d_pct=50.0, change_2d_90d_pct=50.0)
        self.assertFalse(check_clause_2(i).fired)


class Clause3Tests(unittest.TestCase):
    def test_fires_with_volume_spike(self):
        i = _base(
            change_6d_pct=26.0, change_6d_peer_avg_pct=5.0,
            volume_ratio_60d=6.0, volume_ratio_60d_peer_avg=1.0,
        )
        self.assertTrue(check_clause_3(i).fired)

    def test_does_not_fire_without_volume_spike(self):
        i = _base(change_6d_pct=26.0, change_6d_peer_avg_pct=5.0, volume_ratio_60d=3.0)
        self.assertFalse(check_clause_3(i).fired)

    def test_excluded_when_volume_below_500(self):
        i = _base(
            change_6d_pct=26.0, change_6d_peer_avg_pct=5.0,
            volume_ratio_60d=6.0, volume_ratio_60d_peer_avg=1.0, volume=499,
        )
        self.assertFalse(check_clause_3(i).fired)

    def test_excluded_when_turnover_below_0_1pct(self):
        i = _base(
            change_6d_pct=26.0, change_6d_peer_avg_pct=5.0,
            volume_ratio_60d=6.0, volume_ratio_60d_peer_avg=1.0, turnover_pct=0.05,
        )
        self.assertFalse(check_clause_3(i).fired)

    def test_does_not_fire_when_change_25pct_or_below(self):
        i = _base(change_6d_pct=25.0, change_6d_peer_avg_pct=5.0, volume_ratio_60d=10.0, volume_ratio_60d_peer_avg=1.0)
        self.assertFalse(check_clause_3(i).fired)


class Clause4Tests(unittest.TestCase):
    def test_fires(self):
        i = _base(change_6d_pct=26.0, change_6d_peer_avg_pct=5.0, turnover_pct=10.0, turnover_pct_peer_avg=4.0)
        self.assertTrue(check_clause_4(i).fired)

    def test_turnover_exactly_10_fires(self):
        i = _base(change_6d_pct=26.0, change_6d_peer_avg_pct=5.0, turnover_pct=10.0, turnover_pct_peer_avg=5.0)
        self.assertTrue(check_clause_4(i).fired)

    def test_turnover_below_10_does_not_fire(self):
        i = _base(change_6d_pct=26.0, change_6d_peer_avg_pct=5.0, turnover_pct=9.9, turnover_pct_peer_avg=4.0)
        self.assertFalse(check_clause_4(i).fired)


class Clause6Tests(unittest.TestCase):
    def test_fires_with_negative_pe(self):
        i = _base(
            pe_ratio=-5.0, pbr=7.0, pbr_peer_avg=3.0,
            turnover_pct=5.0, volume=3000, pbr_industry_avg=1.5,
        )
        self.assertTrue(check_clause_6(i).fired)

    def test_fires_with_high_pe_2x_avg(self):
        i = _base(
            pe_ratio=61.0, pe_ratio_peer_avg=25.0, pbr=7.0, pbr_peer_avg=3.0,
            turnover_pct=5.0, volume=3000, pbr_industry_avg=1.5,
        )
        self.assertTrue(check_clause_6(i).fired)

    def test_high_pe_under_60_does_not_qualify(self):
        i = _base(
            pe_ratio=55.0, pe_ratio_peer_avg=1.0, pbr=7.0, pbr_peer_avg=3.0,
            turnover_pct=5.0, volume=3000, pbr_industry_avg=1.5,
        )
        self.assertFalse(check_clause_6(i).fired)

    def test_pbr_below_6_does_not_fire(self):
        i = _base(pe_ratio=-5.0, pbr=5.9, pbr_peer_avg=1.0, turnover_pct=5.0, volume=3000, pbr_industry_avg=1.0)
        self.assertFalse(check_clause_6(i).fired)

    def test_volume_below_3000_does_not_fire(self):
        i = _base(pe_ratio=-5.0, pbr=7.0, pbr_peer_avg=3.0, turnover_pct=5.0, volume=2999, pbr_industry_avg=1.0)
        self.assertFalse(check_clause_6(i).fired)

    def test_industry_pbr_condition_required(self):
        i = _base(pe_ratio=-5.0, pbr=7.0, pbr_peer_avg=3.0, turnover_pct=5.0, volume=3000, pbr_industry_avg=2.0)
        self.assertFalse(check_clause_6(i).fired)

    def test_missing_pe_or_pbr_does_not_fire(self):
        self.assertFalse(check_clause_6(_base()).fired)


class Clause7Tests(unittest.TestCase):
    def test_fires_via_usage_rates_path(self):
        i = _base(
            change_6d_pct=26.0, change_6d_peer_avg_pct=5.0,
            short_margin_ratio_pct=20.0, margin_usage_pct=25.0, short_usage_pct=15.0,
        )
        self.assertTrue(check_clause_7(i).fired)

    def test_fires_via_4x_min_6d_path(self):
        i = _base(
            change_6d_pct=26.0, change_6d_peer_avg_pct=5.0,
            short_margin_ratio_pct=8.0, short_margin_ratio_min_6d_pct=2.0,
        )
        self.assertTrue(check_clause_7(i).fired)

    def test_does_not_fire_below_both_paths(self):
        i = _base(
            change_6d_pct=26.0, change_6d_peer_avg_pct=5.0,
            short_margin_ratio_pct=10.0, margin_usage_pct=10.0, short_usage_pct=10.0,
            short_margin_ratio_min_6d_pct=5.0,
        )
        self.assertFalse(check_clause_7(i).fired)


class Clause9Tests(unittest.TestCase):
    def test_fires_via_6d_avg_path(self):
        i = _base(avg_volume_ratio_6d_60d=6.0, avg_volume_ratio_6d_60d_peer_avg=1.0)
        self.assertTrue(check_clause_9(i).fired)

    def test_fires_via_single_day_path(self):
        i = _base(volume_ratio_60d=5.5, volume_ratio_60d_peer_avg=0.5)
        self.assertTrue(check_clause_9(i).fired)

    def test_no_price_condition_needed(self):
        """第九款完全不看漲跌幅，只要量到就觸發——確認沒有意外用到change欄位。"""
        i = _base(volume_ratio_60d=5.5, volume_ratio_60d_peer_avg=0.5, change_6d_pct=0.0)
        self.assertTrue(check_clause_9(i).fired)

    def test_excluded_when_amount_below_30m(self):
        i = _base(
            volume_ratio_60d=6.0, volume_ratio_60d_peer_avg=1.0, turnover_amount=29_999_999,
        )
        self.assertFalse(check_clause_9(i).fired)

    def test_ratio_below_5_does_not_fire(self):
        i = _base(volume_ratio_60d=4.9, volume_ratio_60d_peer_avg=0.5)
        self.assertFalse(check_clause_9(i).fired)


class Clause10Tests(unittest.TestCase):
    def test_fires(self):
        i = _base(
            cum_turnover_6d_pct=51.0, cum_turnover_6d_peer_avg_pct=5.0,
            turnover_pct=10.0, turnover_pct_peer_avg=4.0,
        )
        self.assertTrue(check_clause_10(i).fired)

    def test_does_not_fire_at_exactly_50pct(self):
        i = _base(
            cum_turnover_6d_pct=50.0, cum_turnover_6d_peer_avg_pct=5.0,
            turnover_pct=10.0, turnover_pct_peer_avg=4.0,
        )
        self.assertFalse(check_clause_10(i).fired)

    def test_excluded_when_amount_below_500m(self):
        i = _base(
            cum_turnover_6d_pct=51.0, cum_turnover_6d_peer_avg_pct=5.0,
            turnover_pct=10.0, turnover_pct_peer_avg=4.0, turnover_amount=499_999_999,
        )
        self.assertFalse(check_clause_10(i).fired)

    def test_no_price_condition_needed(self):
        i = _base(
            cum_turnover_6d_pct=51.0, cum_turnover_6d_peer_avg_pct=5.0,
            turnover_pct=10.0, turnover_pct_peer_avg=4.0, change_6d_pct=0.0,
        )
        self.assertTrue(check_clause_10(i).fired)


class Clause11Tests(unittest.TestCase):
    def test_fires_at_1000_to_2000_tier(self):
        i = _base(close=1500.0, price_diff_6d=300.0, is_6d_high_or_low=True)
        self.assertTrue(check_clause_11(i).fired)

    def test_below_threshold_does_not_fire(self):
        i = _base(close=1500.0, price_diff_6d=299.9, is_6d_high_or_low=True)
        self.assertFalse(check_clause_11(i).fired)

    def test_next_tier_adds_150(self):
        i = _base(close=2500.0, price_diff_6d=450.0, is_6d_high_or_low=True)
        self.assertTrue(check_clause_11(i).fired)
        i2 = _base(close=2500.0, price_diff_6d=449.9, is_6d_high_or_low=True)
        self.assertFalse(check_clause_11(i2).fired)

    def test_boundary_just_above_2000_is_next_tier(self):
        """2000.01元剛超過2000元邊界，門檻要跳到下一級距450元，不是還留在300元
        （用(close-1)//1000的舊算法在這個邊界會算錯，改用ceil(close/1000)-1）。"""
        i = _base(close=2000.01, price_diff_6d=449.99, is_6d_high_or_low=True)
        self.assertFalse(check_clause_11(i).fired)
        i2 = _base(close=2000.01, price_diff_6d=450.0, is_6d_high_or_low=True)
        self.assertTrue(check_clause_11(i2).fired)

    def test_exactly_2000_stays_in_first_tier(self):
        """close剛好2000元，門檻應該還是第一級距的300元，不是誤算成第二級距的450元
        ——用一個介於300~450之間的價差(440)驗證：門檻若誤算成450會不觸發，正確的
        300門檻則會觸發。"""
        i = _base(close=2000.0, price_diff_6d=300.0, is_6d_high_or_low=True)
        self.assertTrue(check_clause_11(i).fired)
        i2 = _base(close=2000.0, price_diff_6d=440.0, is_6d_high_or_low=True)
        self.assertTrue(check_clause_11(i2).fired)
        i3 = _base(close=2000.0, price_diff_6d=299.0, is_6d_high_or_low=True)
        self.assertFalse(check_clause_11(i3).fired)

    def test_below_1000_does_not_apply(self):
        i = _base(close=999.0, price_diff_6d=1000.0, is_6d_high_or_low=True)
        self.assertFalse(check_clause_11(i).fired)

    def test_missing_high_or_low_data_does_not_fire(self):
        i = _base(close=1500.0, price_diff_6d=300.0, is_6d_high_or_low=None)
        self.assertFalse(check_clause_11(i).fired)

    def test_price_diff_big_enough_but_not_actually_6d_extreme_does_not_fire(self):
        """核心情境：6日內價格震盪出夠大的價差，但今天收盤價不是這6天的最高或最低
        （例如6天內先大漲又回落到中間值）——不該觸發，這是修正前遺漏的檢查。"""
        i = _base(close=1500.0, price_diff_6d=300.0, is_6d_high_or_low=False)
        self.assertFalse(check_clause_11(i).fired)


class Clause12Tests(unittest.TestCase):
    def test_fires_via_cum_6d_ratio_path(self):
        i = _base(sbl_short_sale_cum_6d_ratio_pct=12.0)
        self.assertTrue(check_clause_12(i).fired)

    def test_does_not_fire_below_12pct_cum_ratio(self):
        i = _base(sbl_short_sale_cum_6d_ratio_pct=11.9)
        self.assertFalse(check_clause_12(i).fired)

    def test_fires_via_5x_spike_path(self):
        i = _base(sbl_short_sale_prev_day_volume=500.0, sbl_short_sale_avg_60d_volume=100.0)
        self.assertTrue(check_clause_12(i).fired)

    def test_does_not_fire_below_5x_spike(self):
        i = _base(sbl_short_sale_prev_day_volume=499.0, sbl_short_sale_avg_60d_volume=100.0)
        self.assertFalse(check_clause_12(i).fired)

    def test_no_price_condition_needed(self):
        i = _base(sbl_short_sale_cum_6d_ratio_pct=12.0, change_6d_pct=0.0)
        self.assertTrue(check_clause_12(i).fired)

    def test_excluded_when_prev_day_sbl_volume_below_100(self):
        i = _base(
            sbl_short_sale_cum_6d_ratio_pct=50.0, sbl_short_sale_prev_day_volume=99.0,
            sbl_short_sale_avg_60d_volume=1.0,
        )
        self.assertFalse(check_clause_12(i).fired)

    def test_excluded_when_turnover_below_0_3pct(self):
        i = _base(sbl_short_sale_cum_6d_ratio_pct=50.0, turnover_pct=0.29)
        self.assertFalse(check_clause_12(i).fired)

    def test_excluded_when_volume_below_500(self):
        i = _base(sbl_short_sale_cum_6d_ratio_pct=50.0, volume=499.0)
        self.assertFalse(check_clause_12(i).fired)


class Clause13Tests(unittest.TestCase):
    def test_fires_via_cum_6d_ratio_path(self):
        i = _base(day_trading_cum_6d_ratio_pct=60.1)
        self.assertTrue(check_clause_13(i).fired)

    def test_does_not_fire_at_exactly_60pct(self):
        i = _base(day_trading_cum_6d_ratio_pct=60.0)
        self.assertFalse(check_clause_13(i).fired)

    def test_fires_via_prev_day_ratio_path(self):
        i = _base(day_trading_prev_day_ratio_pct=60.1)
        self.assertTrue(check_clause_13(i).fired)

    def test_no_price_condition_needed(self):
        i = _base(day_trading_cum_6d_ratio_pct=60.1, change_6d_pct=0.0)
        self.assertTrue(check_clause_13(i).fired)

    def test_excluded_when_turnover_at_or_below_5pct(self):
        i = _base(day_trading_cum_6d_ratio_pct=60.1, turnover_pct=5.0)
        self.assertFalse(check_clause_13(i).fired)

    def test_excluded_when_turnover_amount_at_or_below_5e8(self):
        i = _base(day_trading_cum_6d_ratio_pct=60.1, turnover_amount=500_000_000)
        self.assertFalse(check_clause_13(i).fired)

    def test_excluded_when_prev_day_volume_at_or_below_5000(self):
        i = _base(day_trading_cum_6d_ratio_pct=60.1, day_trading_prev_day_volume=5000.0)
        self.assertFalse(check_clause_13(i).fired)


class CheckAllClausesTests(unittest.TestCase):
    def test_returns_one_result_per_checker(self):
        results = check_all_clauses(_base())
        self.assertEqual(len(results), len(CHECKERS))
        self.assertEqual({r.clause for r in results}, set(CHECKERS))

    def test_all_false_on_empty_inputs(self):
        results = check_all_clauses(_base())
        self.assertTrue(all(not r.fired for r in results))


if __name__ == "__main__":
    unittest.main()
