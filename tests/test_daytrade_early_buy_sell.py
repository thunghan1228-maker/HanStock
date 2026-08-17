import unittest
from datetime import datetime
from unittest.mock import patch

from otc_index import TW_TZ
import daytrade_early_sell as signals


class DaytradeEarlyBuySellTests(unittest.TestCase):
    def test_collects_red_buy_and_green_sell_signals_from_same_candidate_pool(self):
        now = datetime(2026, 8, 17, 10, 1, tzinfo=TW_TZ)
        bars = [{
            "ts": int(datetime(2026, 8, 17, 10, 0, tzinfo=TW_TZ).timestamp() * 1000),
            "close": 100,
            "main_buy_amount": 60,
            "main_sell_amount": 55,
        }]

        class Service:
            def ensure_stock_subscriptions(self, codes):
                return {"active_count": len(codes), "failed": {}}

        class Hub:
            def get_live_bars_1m(self, ticker):
                return bars

        candidate = {
            "ticker": "2330", "name": "台積電", "trade_date": "2026-08-14",
            "previous_estimated_sell_pressure": 100.0,
        }
        with patch.object(signals, "monitored_candidates", return_value=("2026-08-14", [candidate])), patch.object(
            signals, "save_intraday_signals", side_effect=lambda rows: rows
        ):
            result = signals.collect_early_sell_signals(Service(), Hub(), now=now)

        by_kind = {row["kind"]: row for row in result["inserted"]}
        self.assertEqual(set(by_kind), {signals.BUY_SIGNAL_KIND, signals.SIGNAL_KIND})
        self.assertIn("盤中大單買進", by_kind[signals.BUY_SIGNAL_KIND]["note"])
        self.assertIn("比例 60.0%", by_kind[signals.BUY_SIGNAL_KIND]["note"])
        self.assertIn("盤中大單賣出", by_kind[signals.SIGNAL_KIND]["note"])
        self.assertIn("比例 55.0%", by_kind[signals.SIGNAL_KIND]["note"])

    def test_excludes_short_selling_suspended_or_non_day_trade_candidates(self):
        class Info:
            def __init__(self, day_trade="Yes", short_selling_suspended=False, disposition_level=0):
                self.day_trade = day_trade
                self.short_selling_suspended = short_selling_suspended
                self.trading_suspended = False
                self.disposition_level = disposition_level

        class Contracts:
            def __init__(self, values):
                self.values = values

            def info(self, contract):
                return self.values[contract]

        class Api:
            def __init__(self):
                self.contracts = Contracts({
                    "2330": Info(),
                    "1101": Info(short_selling_suspended=True),
                    "2603": Info(day_trade="No"),
                    "2344": Info(disposition_level=1),
                })

        class Service:
            api = Api()

            def _resolve_stock_contract(self, code):
                return code

        rows = [{"ticker": code} for code in ("2330", "1101", "2603", "2344")]
        filtered = signals._filter_trade_eligible(Service(), rows, "2026-08-17")
        self.assertEqual([row["ticker"] for row in filtered], ["2330"])
        self.assertEqual(rows[1]["signal_excluded_reason"], "short_selling_suspended")
        self.assertEqual(rows[2]["signal_excluded_reason"], "day_trade_not_allowed")
        self.assertEqual(rows[3]["signal_excluded_reason"], "disposition_stock")


if __name__ == "__main__":
    unittest.main()
