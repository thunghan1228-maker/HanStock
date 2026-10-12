"""123 黑龍戰法：創 20 日新高後第 1／2／3 個創高黑龍進場點、區 1／區 2、盤中用的狀態。"""

from __future__ import annotations

import time
import unittest

import dragon123 as module


def bar(day: int, o: float, h: float, l: float, c: float) -> tuple:
    return (f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}", o, h, l, c, 1000)


class ScanTests(unittest.TestCase):
    def build(self):
        bars, scores = [], []
        # 前 20 天平盤 100（第 19 天起每天都算「20 日最高」——平高也算創高）
        for d in range(20):
            bars.append(bar(d, 100, 100, 99, 100))
            scores.append(8)
        # 第 20 天：拉高創新高 110（紅K）
        bars.append(bar(20, 100, 110, 100, 109)); scores.append(14)
        # 第 21、22、23、24 天：四根黑K（漲跌都在範圍內），第 24 天那根不標（三次買滿）
        for d, c in ((21, 108), (22, 107), (23, 106), (24, 105)):
            bars.append(bar(d, c + 1, c + 1.5, c - 0.5, c)); scores.append(14 if d < 23 else 12)
        # 第 25～30 天：沒創高的紅K整理
        for d in range(25, 31):
            bars.append(bar(d, 104, 106, 103, 105)); scores.append(12)
        # 第 31 天：黑K但均線分數 9（不算進場點）；第 32 天：黑K、分數 13，但創高已過 12 天 → 區 2（還是這一波第 5 個以後，不標）
        bars.append(bar(31, 106, 106.5, 104, 104.5)); scores.append(9)
        bars.append(bar(32, 105, 105.5, 103, 104)); scores.append(13)
        # 第 33 天：創新高 112 的黑K（黑豹）→ 重新數，第 1 根、區 1
        bars.append(bar(33, 111.8, 112, 106, 107)); scores.append(15)
        return bars, scores

    def test_counts_regions_and_state(self) -> None:
        bars, scores = self.build()
        out = module.scan(bars, scores)
        got = [(m["date"], m["n"], m["region"], m["sinceHigh"]) for m in out["marks"]]
        self.assertEqual([g[1:] for g in got], [(1, 1, 1), (2, 1, 2), (3, 2, 3), (1, 1, 0)])
        # 第 23 天分數 12（<13）→ 區 2
        self.assertEqual(out["state"], {"count": 1, "sinceHigh": 0, "high19": 112})

    def test_entry_rules(self) -> None:
        self.assertFalse(module.is_entry(("d", 100, 101, 95, 101), 100, 15))     # 紅K
        self.assertFalse(module.is_entry(("d", 105, 106, 103, 104), 100, 9))     # 分數不夠
        self.assertFalse(module.is_entry(("d", 106, 106, 103, 104), 100, 12))    # 漲 4% 超過 +3%
        self.assertFalse(module.is_entry(("d", 95, 95, 85, 88), 100, 12))        # 跌 12% 超過 −10%
        self.assertTrue(module.is_entry(("d", 103, 104, 100, 101), 100, 10))
        self.assertEqual((module.region(13, 5), module.region(13, 6), module.region(12, 0), module.region(None, 0)), (1, 2, 2, 2))

    def test_for_bars_with_real_scores(self) -> None:
        # 300 天緩漲（均線分數會很高），每 7 天收一根小黑K：每次創高後的黑K依序標 1、2、3
        bars, price = [], 100.0
        for d in range(300):
            o = price
            price = price * 1.004 if d % 7 else price * 0.997
            bars.append((f"d{d:03d}", o, max(o, price) * 1.001, min(o, price) * 0.999, price, 1000))
        t0 = time.perf_counter()
        out = module.for_bars(bars, 25)
        self.assertLess(time.perf_counter() - t0, 1.0)
        self.assertTrue(out["marks"])
        self.assertTrue(all(m["date"] >= bars[-25][0] for m in out["marks"]))
        self.assertTrue(all(m["n"] == 1 and m["region"] == 1 for m in out["marks"]))   # 每次黑K前都剛創高
        self.assertIn("區 1", module.rules()["text"])


if __name__ == "__main__":
    unittest.main()
