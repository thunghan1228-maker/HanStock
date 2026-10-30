"""期交所 MIS 臺指期報價代打：日盤／夜盤參數、短時間內快取、讀不到時往上丟。"""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

import txf_mis as module


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TxfMisTests(unittest.TestCase):
    def setUp(self) -> None:
        module._cache.clear()

    def test_market_param_and_cache(self) -> None:
        calls: list[dict] = []

        def fake_urlopen(request, timeout=0):
            calls.append(json.loads(request.data.decode("utf-8")))
            return FakeResponse(json.dumps({"RtCode": "0", "RtData": {"QuoteList": [{"SymbolID": "TXFK6-F", "CLastPrice": "44995.00"}]}}).encode("utf-8"))

        with patch.object(module.urllib.request, "urlopen", fake_urlopen):
            day = module.quote_list("0")
            self.assertEqual(day["RtData"]["QuoteList"][0]["CLastPrice"], "44995.00")
            module.quote_list("0")                                   # 4 秒內再問不再打期交所
            self.assertEqual(len(calls), 1)
            self.assertEqual((calls[0]["MarketType"], calls[0]["CID"], calls[0]["KindID"]), ("0", "TXF", "1"))
            module.quote_list("1")                                   # 夜盤另外一份
            self.assertEqual(calls[1]["MarketType"], "1")
            module.quote_list("abc")                                 # 亂給的當日盤，用到日盤那份快取
            self.assertEqual(len(calls), 2)

    def test_error_propagates(self) -> None:
        def boom(request, timeout=0):
            raise OSError("連不上")

        with patch.object(module.urllib.request, "urlopen", boom):
            with self.assertRaises(OSError):
                module.quote_list("0")


if __name__ == "__main__":
    unittest.main()
