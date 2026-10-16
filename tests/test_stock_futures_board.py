"""股票期貨價格板（首頁股票期貨排名(155) 用）。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import stock_futures_board as module


class FakeFutures:
    def __init__(self, tx):
        self.TXF = SimpleNamespace(TXFR1=tx)


class FakeApi:
    def __init__(self, snaps, tx=None):
        self._snaps = snaps
        self.calls = []
        self.Contracts = SimpleNamespace(Futures=FakeFutures(tx))

    def snapshots(self, contracts):
        self.calls.append([c.code for c in contracts])
        return [self._snaps[c.code] for c in contracts if c.code in self._snaps]


def contract(code, target):
    return SimpleNamespace(code=code, target_code=target)


class StockFuturesBoardTests(unittest.TestCase):
    def setUp(self) -> None:
        module._contracts.clear()
        module._contract_failures.clear()
        module._cache.update({"at": 0.0, "value": None})

    def test_snapshot_prices_and_tx(self) -> None:
        contracts = {"2330": contract("CDFR1", "CDFK6"), "2317": contract("DHFR1", "DHFK6")}
        tx = contract("TXFR1", "TXFK6")
        snaps = {
            "CDFR1": SimpleNamespace(code="CDFR1", close=1515.0, change_price=15.0, change_rate=1.0, total_volume=1200, ts=1792119000000000000),
            "DHFR1": SimpleNamespace(code="DHFR1", close=200.0, change_price=-4.0, change_rate=None, total_volume=50, ts=0),
            "TXFR1": SimpleNamespace(code="TXFR1", close=23000.0, change_price=-230.0, change_rate=-0.99, total_volume=1, ts=0),
        }
        api = FakeApi(snaps, tx=tx)

        def resolver(_api, code, _mode):
            if code not in contracts:
                raise ValueError("無股期")
            return contracts[code]

        out = module.compute_board(api, ["2330", "2317", "1101"], resolver=resolver, now=1000.0)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["requested"], 3)
        self.assertEqual(out["resolved"], 2)
        self.assertEqual(out["quotes"]["2330"]["changePct"], 1.0)
        self.assertEqual(out["quotes"]["2330"]["futuresCode"], "CDFK6")
        self.assertAlmostEqual(out["quotes"]["2317"]["changePct"], -1.961, places=3)   # 沒給漲跌幅就用參考價算
        self.assertEqual(out["tx"]["changePct"], -0.99)
        self.assertNotIn("1101", out["quotes"])
        self.assertEqual(len(api.calls), 1)                                            # 一批抓完

    def test_contract_cache_and_failure_backoff(self) -> None:
        calls = []

        def resolver(_api, code, _mode):
            calls.append(code)
            if code == "9999":
                raise ValueError("無股期")
            return contract(code + "R1", code + "K6")

        api = FakeApi({})
        module.compute_board(api, ["2330", "9999"], resolver=resolver, now=1000.0)
        module.compute_board(api, ["2330", "9999"], resolver=resolver, now=1100.0)
        self.assertEqual(calls, ["2330", "9999"])                                       # 近月合約、找不到的都先記住
        module.compute_board(api, ["2330", "9999"], resolver=resolver, now=1000.0 + module.CONTRACT_RECHECK_SECONDS + 1)
        self.assertEqual(calls, ["2330", "9999", "2330", "9999"])

    def test_all_snapshots_fail_raises(self) -> None:
        class BadApi(FakeApi):
            def snapshots(self, contracts):
                raise RuntimeError("down")

        with self.assertRaises(RuntimeError):
            module.compute_board(BadApi({}), ["2330"], resolver=lambda a, c, m: contract("CDFR1", "CDFK6"), now=1.0)

    def test_underlyings_only_ranked_groups_with_futures(self) -> None:
        groups = {"半導體": [("2330", "台積電"), ("1101", "台泥")], "金融股": [("2886", "兆豐金")], "股期標的": [("2454", "聯發科")]}
        with patch("stock_groups.STOCK_GROUPS", groups), patch("stock_trading_eligibility.has_stock_futures", lambda c: c in {"2330", "2886", "2454"}):
            self.assertEqual(module.board_underlyings(), ["2330"])

    def test_endpoint_returns_503_on_error(self) -> None:
        from fastapi.testclient import TestClient

        import persistent_app

        with patch.object(module, "get_stock_futures_board", side_effect=RuntimeError("股期共享連線還沒準備好")):
            resp = TestClient(persistent_app.app).get("/api/hub/stock-futures-board")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["status"], "error")



class SnapshotApiTests(unittest.TestCase):
    def test_snapshot_api_works_when_stock_futures_subscriptions_disabled(self) -> None:
        # 正式環境關掉股期即時訂閱（名額留給現貨逐筆），Snapshot 不訂閱，照樣要拿得到共享連線
        import stock_futures_service as sfs

        service = sfs.StockFuturesQuoteService(api_factory=lambda: object())
        service._stock_futures_enabled = False
        pool_api = object()
        service._pools = [SimpleNamespace(healthy=False, api=object()), SimpleNamespace(healthy=True, api=pool_api)]
        with patch.object(service, "_ensure_pools", lambda: None):
            self.assertIs(service.snapshot_api(), pool_api)
            service._pools = [SimpleNamespace(healthy=False, api=object())]
            with self.assertRaises(RuntimeError):
                service.snapshot_api()


if __name__ == "__main__":
    unittest.main()
