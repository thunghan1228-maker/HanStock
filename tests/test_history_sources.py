from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import history_sources as module
from history_sources import (
    fetch_minute_bars_chain,
    fetch_yahoo_minute_bars,
    parse_finmind_minute_rows,
    parse_yahoo_chart,
    probe_history_sources,
)

TW = timezone(timedelta(hours=8))


def ts(day: str, hour: int, minute: int) -> int:
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=TW).timestamp() * 1000)


def finmind_rows(volume: int, day: str = "2026-09-22") -> list[dict]:
    return [{"date": day, "minute": "09:01:00", "open": 100, "max": 101, "min": 99, "close": 100.5, "volume": volume}]


def yahoo_payload(volume_shares: int, day: str = "2026-09-22") -> dict:
    return {"chart": {"result": [{
        "timestamp": [ts(day, 9, 0) // 1000],
        "indicators": {"quote": [{"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [volume_shares]}]},
    }], "error": None}}


# 正式環境看到的 FinMind 422：FastAPI enum 錯誤，msg 與 ctx.expected 各列一份允許的資料集。
PERMITTED = "'CnnFearGreedIndex', 'TaiwanFutOptTickInfo', 'TaiwanStockInfo', 'TaiwanStockKBar', 'TaiwanStockPrice' or 'USStockPriceMinute'"
FINMIND_422_BODY = json.dumps({"detail": [{
    "type": "enum", "loc": ["query", "dataset"], "msg": f"Input should be {PERMITTED}",
    "input": "TaiwanStockPriceMinute", "ctx": {"expected": PERMITTED},
}]})


class FinMindParserTests(unittest.TestCase):
    def test_close_time_labels_shift_back_one_minute_and_shares_become_lots(self) -> None:
        rows = [
            {"date": "2026-09-22", "minute": "09:01:00", "open": 100, "max": 101, "min": 99, "close": 100.5, "volume": 12000},
            {"date": "2026-09-22", "minute": "09:02:00", "open": 100.5, "max": 102, "min": 100, "close": 101, "volume": 3000},
            {"date": "2026-09-22", "minute": "13:31:00", "open": 101, "max": 101, "min": 101, "close": 101, "volume": 1000},
            {"date": "2026-09-21", "minute": "09:01:00", "open": 90, "max": 91, "min": 89, "close": 90.5, "volume": 1000},
        ]

        bars = parse_finmind_minute_rows(rows, "2026-09-22", "2026-09-22", volume_unit="shares")

        self.assertEqual([b["ts"] for b in bars], [ts("2026-09-22", 9, 0), ts("2026-09-22", 9, 1)])
        self.assertEqual(bars[0]["volume"], 12)
        self.assertEqual(bars[0]["high"], 101.0)
        self.assertEqual(bars[1]["close"], 101.0)

    def test_labels_that_include_0900_are_bar_start_times(self) -> None:
        rows = [
            {"date": "2026-09-22", "minute": "09:00:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
            {"date": "2026-09-22", "minute": "09:01:00", "open": 100.5, "high": 102, "low": 100, "close": 101, "volume": 1000},
        ]

        bars = parse_finmind_minute_rows(rows, "2026-09-22", "2026-09-22")

        self.assertEqual([b["ts"] for b in bars], [ts("2026-09-22", 9, 0), ts("2026-09-22", 9, 1)])

    def test_garbage_rows_are_ignored(self) -> None:
        rows = [{"date": "2026-09-22", "minute": "bad"}, "nope", {"date": "2026-09-22", "minute": "09:01:00", "open": 0, "max": 0, "min": 0, "close": 0}]
        self.assertEqual(parse_finmind_minute_rows(rows, "2026-09-22", "2026-09-22"), [])
        self.assertEqual(parse_finmind_minute_rows(None, "2026-09-22", "2026-09-22"), [])


class YahooParserTests(unittest.TestCase):
    def test_chart_payload_becomes_session_bars_with_lots(self) -> None:
        stamps = [ts("2026-09-22", 9, 0) // 1000, ts("2026-09-22", 9, 1) // 1000, ts("2026-09-22", 13, 30) // 1000]
        payload = {"chart": {"result": [{
            "timestamp": stamps,
            "indicators": {"quote": [{
                "open": [100.0, None, 101.0], "high": [101.0, None, 101.0], "low": [99.0, None, 101.0],
                "close": [100.5, None, 101.0], "volume": [25000, None, 500000],
            }]},
        }], "error": None}}

        bars = parse_yahoo_chart(payload, "2026-09-22", "2026-09-22")

        self.assertEqual([b["ts"] for b in bars], [ts("2026-09-22", 9, 0)])  # None 那根跳過，13:30 不在正式盤
        self.assertEqual(bars[0]["volume"], 25)
        self.assertEqual(bars[0]["close"], 100.5)

    def test_chart_error_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_yahoo_chart({"chart": {"result": None, "error": {"code": "Not Found"}}}, "2026-09-22", "2026-09-22")

    def test_unknown_market_tries_tw_then_two(self) -> None:
        calls: list[str] = []

        def fetcher(url, params):
            calls.append(url)
            if url.endswith("3532.TW"):
                raise RuntimeError("404")
            return {"chart": {"result": [{
                "timestamp": [ts("2026-09-22", 9, 0) // 1000],
                "indicators": {"quote": [{"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1000]}]},
            }], "error": None}}

        bars = fetch_yahoo_minute_bars("3532", "2026-09-22", "2026-09-22", fetcher=fetcher)

        self.assertEqual(len(bars), 1)
        self.assertEqual([url.rsplit("/", 1)[1] for url in calls], ["3532.TW", "3532.TWO"])

    def test_known_otc_market_only_tries_two(self) -> None:
        calls: list[str] = []

        def fetcher(url, params):
            calls.append(url)
            return {"chart": {"result": [], "error": None}}

        self.assertEqual(fetch_yahoo_minute_bars("6488", "2026-09-22", "2026-09-22", market="OTC", fetcher=fetcher), [])
        self.assertEqual([url.rsplit("/", 1)[1] for url in calls], ["6488.TWO"])


class ChainTests(unittest.TestCase):
    def setUp(self) -> None:
        module._reset_runtime_state()

    def tearDown(self) -> None:
        module._reset_runtime_state()

    def test_finmind_failure_falls_through_to_yahoo(self) -> None:
        def fetcher(url, params):
            if url.startswith(module.FINMIND_DATA_URL):
                raise RuntimeError("finmind down")
            return {"chart": {"result": [{
                "timestamp": [ts("2026-09-22", 9, 0) // 1000],
                "indicators": {"quote": [{"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1000]}]},
            }], "error": None}}

        with patch.dict(os.environ, {"FINMIND_TOKEN": "dummy"}):
            bars, source = fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)

        self.assertEqual(source, "yahoo")
        self.assertEqual(len(bars), 1)

    def test_finmind_wins_when_it_has_data(self) -> None:
        def fetcher(url, params):
            if url.startswith(module.FINMIND_DATA_URL):
                self.assertEqual(params["data_id"], "2330")
                return {"status": 200, "data": [
                    {"date": "2026-09-22", "minute": "09:01:00", "open": 100, "max": 101, "min": 99, "close": 100.5, "volume": 1000},
                ]}
            raise AssertionError("FinMind 有資料時不該再打 Yahoo")

        # 成交量單位指定了就不需要拿 Yahoo 校準，FinMind 有資料時 Yahoo 一次都不該打。
        with patch.dict(os.environ, {"FINMIND_TOKEN": "dummy"}), patch.object(module, "FINMIND_MINUTE_VOLUME_UNIT", "lots"):
            bars, source = fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", fetcher=fetcher)

        self.assertEqual(source, "finmind")
        self.assertEqual(bars[0]["ts"], ts("2026-09-22", 9, 0))

    def test_no_token_skips_finmind_and_empty_everywhere_returns_none(self) -> None:
        calls: list[str] = []

        def fetcher(url, params):
            calls.append(url)
            return {"chart": {"result": [], "error": None}}

        with patch.dict(os.environ, {"FINMIND_TOKEN": ""}):
            bars, source = fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)

        self.assertEqual((bars, source), ([], None))
        self.assertTrue(all(url.startswith(module.YAHOO_CHART_URL) for url in calls))

    def test_finmind_4xx_opens_circuit_breaker_but_probe_still_calls(self) -> None:
        # 正式環境實測：TaiwanStockPriceMinute 全部回 422，幾百檔每檔都白打一次。
        # 被拒後 10 分鐘內鏈就直接跳過 FinMind；probe 是人在看，照打才看得到錯誤內容。
        calls: list[str] = []

        def fetcher(url, params):
            if url.startswith(module.FINMIND_DATA_URL):
                calls.append("finmind")
                raise module.SourceHttpError(422, "Unprocessable Entity", '{"detail":[{"msg":"permitted: TaiwanStockPrice"}]}')
            calls.append("yahoo")
            return {"chart": {"result": [], "error": None}}

        with patch.dict(os.environ, {"FINMIND_TOKEN": "dummy"}):
            fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)
            fetch_minute_bars_chain("2317", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)
            status = module.history_sources_status()
            probe = probe_history_sources("2330", "2026-09-22", market="TSE", fetcher=fetcher)

        self.assertEqual(calls.count("finmind"), 2)  # 第二檔被斷路器擋掉，probe 照打
        self.assertGreater(status["finmind"]["blockedForSeconds"], 0)
        self.assertIn("permitted: TaiwanStockPrice", status["finmind"]["lastError"])
        self.assertIn("HTTP 422", probe["finmind"]["error"])

    def test_yahoo_remembers_which_suffix_worked(self) -> None:
        calls: list[str] = []

        def fetcher(url, params):
            calls.append(url.rsplit("/", 1)[1])
            if url.endswith(".TW"):
                raise RuntimeError("404")
            return {"chart": {"result": [{
                "timestamp": [ts("2026-09-22", 9, 0) // 1000],
                "indicators": {"quote": [{"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1000]}]},
            }], "error": None}}

        fetch_yahoo_minute_bars("6197", "2026-09-22", "2026-09-22", fetcher=fetcher)
        fetch_yahoo_minute_bars("6197", "2026-09-22", "2026-09-22", fetcher=fetcher)

        self.assertEqual(calls, ["6197.TW", "6197.TWO", "6197.TWO"])

    def test_probe_reports_each_source(self) -> None:
        def fetcher(url, params):
            if url.startswith(module.FINMIND_DATA_URL):
                return {"status": 200, "data": []}
            raise RuntimeError("yahoo 429")

        with patch.dict(os.environ, {"FINMIND_TOKEN": "dummy"}):
            probe = probe_history_sources("2330", "2026-09-22", market="TSE", fetcher=fetcher)

        self.assertFalse(probe["finmind"]["ok"])
        self.assertEqual(probe["finmind"]["bars"], 0)
        self.assertIn("yahoo 429", probe["yahoo"]["error"])
        status = module.history_sources_status()
        self.assertEqual(status["order"], ["finmind", "yahoo"])
        self.assertIn("yahoo 429", status["yahoo"]["lastError"])
        self.assertEqual(probe["finmind"]["dataset"], module.FINMIND_MINUTE_DATASET)


class FinMindDatasetAutoSelectTests(unittest.TestCase):
    """正式環境實測：資料集名稱猜錯被 422 拒絕，但回應把允許的名稱全列出來了，
    程式自己對出分 K 資料集重打，不用再改設定重新部署一次。"""

    def setUp(self) -> None:
        module._reset_runtime_state()
        self.env = patch.dict(os.environ, {"FINMIND_TOKEN": "dummy"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        module._reset_runtime_state()

    def test_permitted_list_is_parsed_from_msg_and_ctx(self) -> None:
        error = module.SourceHttpError(422, "Unprocessable Entity", FINMIND_422_BODY)
        names = module.permitted_datasets_from_error(error)
        self.assertEqual(names[:2], ["CnnFearGreedIndex", "TaiwanFutOptTickInfo"])
        self.assertIn("TaiwanStockKBar", names)
        self.assertEqual(len(names), len(set(names)))  # msg 與 ctx.expected 重複的只留一份
        # 非 enum 錯誤、沒提到 dataset 的內容不會被當成清單。
        self.assertEqual(module.permitted_datasets_from_error(module.SourceHttpError(402, "Payment", '{"msg":"level not allowed"}')), [])
        self.assertEqual(module.permitted_datasets_from_error(RuntimeError("timeout")), [])

    def test_truncated_body_still_yields_the_names_before_the_cut(self) -> None:
        cut = FINMIND_422_BODY[: FINMIND_422_BODY.index("'TaiwanStockPrice'") + 8]  # JSON 已經不完整
        names = module.permitted_datasets_from_error(module.SourceHttpError(422, "Unprocessable Entity", cut))
        self.assertEqual(names, ["CnnFearGreedIndex", "TaiwanFutOptTickInfo", "TaiwanStockInfo", "TaiwanStockKBar"])

    def test_rejected_dataset_is_replaced_by_a_permitted_minute_dataset_in_the_same_call(self) -> None:
        datasets: list[str] = []

        def fetcher(url, params):
            if url.startswith(module.FINMIND_DATA_URL):
                datasets.append(params["dataset"])
                if params["dataset"] != "TaiwanStockKBar":
                    raise module.SourceHttpError(422, "Unprocessable Entity", FINMIND_422_BODY)
                return {"status": 200, "data": finmind_rows(25)}
            return yahoo_payload(25000)

        with patch.object(module, "FINMIND_MINUTE_DATASET", "TaiwanStockPriceMinute"):
            bars, source = fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)
            again, _ = fetch_minute_bars_chain("2317", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)
            status = module.history_sources_status()
            probe = probe_history_sources("2330", "2026-09-22", market="TSE", fetcher=fetcher)

        self.assertEqual(source, "finmind")
        self.assertEqual(len(bars), 1)
        self.assertEqual(len(again), 1)
        # 第一檔：猜錯 → 換名重打；之後每檔都直接用對的名稱。
        self.assertEqual(datasets, ["TaiwanStockPriceMinute", "TaiwanStockKBar", "TaiwanStockKBar", "TaiwanStockKBar"])
        self.assertEqual(status["finmind"]["dataset"], "TaiwanStockKBar")
        self.assertEqual(status["finmind"]["configuredDataset"], "TaiwanStockPriceMinute")
        self.assertTrue(status["finmind"]["datasetAutoSelected"])
        self.assertEqual(status["finmind"]["blockedForSeconds"], 0)  # 換名成功就不開斷路器
        self.assertEqual(status["finmind"]["permittedDatasetCount"], 6)
        self.assertEqual(status["finmind"]["permittedStockDatasets"], ["TaiwanStockInfo", "TaiwanStockKBar", "TaiwanStockPrice"])
        self.assertEqual(probe["finmind"]["dataset"], "TaiwanStockKBar")
        self.assertTrue(probe["finmind"]["ok"])

    def test_unknown_minute_dataset_name_is_picked_by_pattern(self) -> None:
        body = json.dumps({"detail": [{"type": "enum", "loc": ["query", "dataset"],
                                       "msg": "Input should be 'TaiwanStockPrice', 'TaiwanStockMinuteKBar' or 'USStockPriceMinute'"}]})
        datasets: list[str] = []

        def fetcher(url, params):
            datasets.append(params["dataset"])
            if params["dataset"] == "TaiwanStockKBar":
                raise module.SourceHttpError(422, "Unprocessable Entity", body)
            return {"status": 200, "data": finmind_rows(25)}

        with patch.object(module, "FINMIND_MINUTE_VOLUME_UNIT", "lots"):
            bars = module.fetch_finmind_minute_bars("2330", "2026-09-22", "2026-09-22", fetcher=fetcher)

        self.assertEqual(len(bars), 1)
        self.assertEqual(datasets, ["TaiwanStockKBar", "TaiwanStockMinuteKBar"])  # 美股分 K 不會被挑到

    def test_rejection_without_a_minute_dataset_blocks_and_exposes_the_permitted_names(self) -> None:
        body = json.dumps({"detail": [{"type": "enum", "loc": ["query", "dataset"],
                                       "msg": "Input should be 'TaiwanStockInfo', 'TaiwanStockPrice' or 'USStockPriceMinute'"}]})
        datasets: list[str] = []

        def fetcher(url, params):
            if url.startswith(module.FINMIND_DATA_URL):
                datasets.append(params["dataset"])
                raise module.SourceHttpError(422, "Unprocessable Entity", body)
            return {"chart": {"result": [], "error": None}}

        bars, source = fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)
        status = module.history_sources_status()

        self.assertEqual((bars, source), ([], None))
        self.assertEqual(datasets, [module.FINMIND_MINUTE_DATASET])  # 沒有可換的名稱就不重打
        self.assertFalse(status["finmind"]["datasetAutoSelected"])
        self.assertGreater(status["finmind"]["blockedForSeconds"], 0)
        self.assertEqual(status["finmind"]["permittedStockDatasets"], ["TaiwanStockInfo", "TaiwanStockPrice"])
        self.assertIn("HTTP 422", status["finmind"]["lastError"])


class FinMindOneDayPerRequestTests(unittest.TestCase):
    """正式環境實測：分 K 資料表帶 end_date 跨日就回 400「we only send one day data」，多日要逐日打。"""

    def setUp(self) -> None:
        module._reset_runtime_state()
        self.env = patch.dict(os.environ, {"FINMIND_TOKEN": "dummy"})
        self.env.start()
        self.unit = patch.object(module, "FINMIND_MINUTE_VOLUME_UNIT", "lots")
        self.unit.start()

    def tearDown(self) -> None:
        self.unit.stop()
        self.env.stop()
        module._reset_runtime_state()

    def test_request_days_skip_weekends_and_keep_the_most_recent_ones(self) -> None:
        # 2026-09-11（五）到 2026-09-22（二）：8 個交易日，只留最近 7 天。
        days = module.finmind_request_days("2026-09-11", "2026-09-22")
        self.assertEqual(days, ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22"])
        self.assertEqual(module.finmind_request_days("2026-09-19", "2026-09-20"), [])  # 純週末
        self.assertEqual(module.finmind_request_days("2026-09-11", "2026-09-22", max_days=2), ["2026-09-21", "2026-09-22"])

    def test_multi_day_range_is_fetched_one_day_at_a_time_without_end_date(self) -> None:
        requests: list[dict] = []

        def fetcher(url, params):
            requests.append(dict(params))
            if params["start_date"] == "2026-09-21":
                return {"status": 200, "data": []}  # 當天沒資料（例如假日）不影響其他天
            return {"status": 200, "data": finmind_rows(25, day=params["start_date"])}

        bars = module.fetch_finmind_minute_bars("2330", "2026-09-18", "2026-09-22", fetcher=fetcher)

        self.assertEqual([r["start_date"] for r in requests], ["2026-09-18", "2026-09-21", "2026-09-22"])
        self.assertTrue(all("end_date" not in r for r in requests))
        self.assertTrue(all(r["dataset"] == "TaiwanStockKBar" and r["data_id"] == "2330" for r in requests))
        self.assertEqual([b["ts"] for b in bars], [ts("2026-09-18", 9, 0), ts("2026-09-22", 9, 0)])
        self.assertEqual(module.history_sources_status()["finmind"]["lastBars"], 2)

    def test_failure_on_a_later_day_gives_up_so_the_chain_moves_to_yahoo(self) -> None:
        requests: list[str] = []

        def fetcher(url, params):
            if url.startswith(module.FINMIND_DATA_URL):
                requests.append(params["start_date"])
                if params["start_date"] == "2026-09-22":
                    raise module.SourceHttpError(429, "Too Many Requests", '{"msg":"Requests reach the upper limit"}')
                return {"status": 200, "data": finmind_rows(25, day=params["start_date"])}
            return yahoo_payload(25000)

        bars, source = fetch_minute_bars_chain("2330", "2026-09-21", "2026-09-22", market="TSE", fetcher=fetcher)
        status = module.history_sources_status()

        self.assertEqual(source, "yahoo")
        self.assertEqual(len(bars), 1)
        self.assertEqual(requests, ["2026-09-21", "2026-09-22"])
        self.assertGreater(status["finmind"]["blockedForSeconds"], 0)  # 429 → 限流冷卻
        self.assertIn("HTTP 429", status["finmind"]["lastError"])


class FinMindVolumeUnitTests(unittest.TestCase):
    """FinMind 分 K 的成交量是「張」還是「股」文件沒說死：第一次拿到資料時跟 Yahoo 同一天的量對一次。"""

    def setUp(self) -> None:
        module._reset_runtime_state()
        self.env = patch.dict(os.environ, {"FINMIND_TOKEN": "dummy"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        module._reset_runtime_state()

    def _fetcher(self, finmind_volume: int, yahoo_volume_shares=25000, yahoo_calls: list | None = None):
        def fetcher(url, params):
            if url.startswith(module.FINMIND_DATA_URL):
                return {"status": 200, "data": finmind_rows(finmind_volume)}
            if yahoo_calls is not None:
                yahoo_calls.append(url)
            if yahoo_volume_shares is None:
                raise RuntimeError("yahoo down")
            return yahoo_payload(yahoo_volume_shares)
        return fetcher

    def test_volumes_a_thousand_times_yahoo_are_shares_and_the_answer_is_cached(self) -> None:
        yahoo_calls: list[str] = []
        fetcher = self._fetcher(25000, 25000, yahoo_calls)

        first, _ = fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)
        second, _ = fetch_minute_bars_chain("2317", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)
        status = module.history_sources_status()

        self.assertEqual(first[0]["volume"], 25)
        self.assertEqual(second[0]["volume"], 25)
        self.assertEqual(len(yahoo_calls), 1)  # 整個程序只校準一次
        self.assertEqual(status["finmind"]["volumeUnit"], "auto")
        self.assertEqual(status["finmind"]["volumeUnitDetected"], "shares")
        self.assertEqual(status["finmind"]["volumeCalibration"]["ratio"], 1000.0)
        self.assertEqual(status["finmind"]["volumeCalibration"]["dates"], ["2026-09-22"])

    def test_volumes_matching_yahoo_are_lots_and_left_alone(self) -> None:
        bars, _ = fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", market="TSE", fetcher=self._fetcher(25, 25000))

        self.assertEqual(bars[0]["volume"], 25)
        self.assertEqual(module.history_sources_status()["finmind"]["volumeUnitDetected"], "lots")

    def test_calibration_is_retried_while_yahoo_is_unavailable(self) -> None:
        yahoo_calls: list[str] = []
        fetcher = self._fetcher(25, None, yahoo_calls)

        bars, _ = fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)
        fetch_minute_bars_chain("2317", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)

        self.assertEqual(bars[0]["volume"], 25)  # 校準不了先當張數
        self.assertEqual(len(yahoo_calls), 2)  # 每次都再試著校準
        self.assertIsNone(module.history_sources_status()["finmind"]["volumeUnitDetected"])

    def test_env_unit_skips_calibration(self) -> None:
        yahoo_calls: list[str] = []
        with patch.object(module, "FINMIND_MINUTE_VOLUME_UNIT", "shares"):
            bars, _ = fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", market="TSE", fetcher=self._fetcher(25000, 25000, yahoo_calls))

        self.assertEqual(bars[0]["volume"], 25)
        self.assertEqual(yahoo_calls, [])


class IndexSourceTests(unittest.TestCase):
    """櫃買指數（OTC_INDEX）：Yahoo 用 ^TWOII、可指定 5m；FinMind 用 TPEx 且被拒不開斷路器。"""

    def setUp(self) -> None:
        module._reset_runtime_state()
        self.env = patch.dict(os.environ, {"FINMIND_TOKEN": "dummy"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        module._reset_runtime_state()

    def test_yahoo_index_alias_and_interval(self) -> None:
        calls: list[tuple[str, str]] = []

        def fetcher(url, params):
            calls.append((url.rsplit("/", 1)[1], params["interval"]))
            return yahoo_payload(0)

        bars = fetch_yahoo_minute_bars("OTC_INDEX", "2026-09-22", "2026-09-22", fetcher=fetcher, interval="5m")

        self.assertEqual(calls, [("%5ETWOII", "5m")])
        self.assertEqual(len(bars), 1)  # 指數沒有成交量也要留下 K 棒

    def test_finmind_index_uses_tpex_id_and_never_opens_the_breaker(self) -> None:
        seen: list[str] = []

        def fetcher(url, params):
            seen.append(params["data_id"])
            raise module.SourceHttpError(400, "Bad Request", '{"msg":"no index data"}')

        with self.assertRaises(module.SourceHttpError):
            module.fetch_finmind_minute_bars("OTC_INDEX", "2026-09-22", "2026-09-22", fetcher=fetcher, block_on_error=False)

        self.assertEqual(seen, ["TPEx"])
        status = module.history_sources_status()["finmind"]
        self.assertEqual(status["blockedForSeconds"], 0)
        self.assertIn("no index data", status["lastError"])

    def test_index_probe_reports_each_source(self) -> None:
        def fetcher(url, params):
            if url.startswith(module.FINMIND_DATA_URL):
                return {"status": 200, "data": []}
            if params["interval"] == "1m":
                raise RuntimeError("no 1m for index")
            return yahoo_payload(0)

        probe = probe_history_sources("^TWOII", "2026-09-22", fetcher=fetcher)

        self.assertEqual(probe["code"], "OTC_INDEX")
        self.assertFalse(probe["yahoo1m"]["ok"])
        self.assertIn("no 1m for index", probe["yahoo1m"]["error"])
        self.assertTrue(probe["yahoo5m"]["ok"])
        self.assertEqual(probe["finmind"]["dataId"], "TPEx")
        self.assertFalse(probe["finmind"]["ok"])
        self.assertEqual(module.history_sources_status()["finmind"]["blockedForSeconds"], 0)


if __name__ == "__main__":
    unittest.main()
