"""K 棒型態道場：五個型態的偵測（用合成日K）、掃描間隔、出題。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import patch

import kbar_dojo as module


def make_dates(n: int) -> list[str]:
    d = date(2026, 1, 1)
    out = []
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return out


def flat_bars(n: int, price: float = 100.0, vol: int = 1000) -> list[tuple]:
    return [(d, price, price * 1.005, price * 0.995, price, vol) for d in make_dates(n)]


def with_tail(bars: list[tuple], extra: list[tuple[float, float, float, float, int]]) -> list[tuple]:
    """在 bars 後面接幾根（開、高、低、收、量），日期延續。"""
    dates = make_dates(len(bars) + len(extra))[len(bars):]
    return bars + [(d, *x) for d, x in zip(dates, extra)]


class DetectorTests(unittest.TestCase):
    def closes(self, bars):
        return [b[4] for b in bars]

    def test_sword(self) -> None:
        bars = with_tail(flat_bars(140), [(101, 108, 99.5, 103, 5000)])      # 昨收 100，最高 108（+8%）收 103，長上影、量 5 倍
        i = len(bars) - 1
        hit = module.detect_sword(bars, self.closes(bars), i)
        self.assertEqual((hit["pattern"], hit["box"]), ("sword", (i, i)))
        self.assertEqual(hit["defense"], 103)                                  # 劍柄（103）離劍低 99.5 超過 3% → 防守劍柄
        near = with_tail(flat_bars(140), [(101, 108, 100.5, 101.2, 5000)])
        self.assertEqual(module.detect_sword(near, self.closes(near), len(near) - 1)["defense"], 100.5)   # 收盤離劍低 3% 內 → 防守劍低
        # 沒爆量、沒長上影、已經漲一大段都不算
        quiet = with_tail(flat_bars(140), [(101, 108, 100.5, 103, 1500)])
        self.assertIsNone(module.detect_sword(quiet, self.closes(quiet), len(quiet) - 1))
        body = with_tail(flat_bars(140), [(101, 108, 100.5, 107.5, 5000)])
        self.assertIsNone(module.detect_sword(body, self.closes(body), len(body) - 1))
        ran = with_tail(flat_bars(136), [(101, 102, 100, 102, 1000), (102, 106, 101, 105, 1000), (105, 112, 104, 110, 1000), (110, 113, 109, 112, 1000), (113, 121, 112, 115, 6000)])
        self.assertIsNone(module.detect_sword(ran, self.closes(ran), len(ran) - 1))

    def test_panther(self) -> None:
        bars = with_tail(flat_bars(140), [(100, 110, 100, 110, 3000), (112, 113, 105, 106, 6000)])    # 前一天漲停，今天開高 +1.8% 收長黑 −5.4%、量 2 倍
        i = len(bars) - 1
        hit = module.detect_panther(bars, self.closes(bars), i)
        self.assertEqual((hit["pattern"], hit["box"], hit["defense"]), ("panther", (i - 1, i), 105))
        small = with_tail(flat_bars(140), [(100, 110, 100, 110, 3000), (112, 113, 109, 110, 6000)])  # 黑K太短
        self.assertIsNone(module.detect_panther(small, self.closes(small), len(small) - 1))
        calm = with_tail(flat_bars(140), [(100, 110, 100, 110, 3000), (112, 113, 105, 106, 3500)])   # 沒爆量
        self.assertIsNone(module.detect_panther(calm, self.closes(calm), len(calm) - 1))

    def test_dance(self) -> None:
        base = [(100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100 + i * 0.1, 1000) for i in range(140)]
        dates = make_dates(140)
        bars = [(d, *x) for d, x in zip(dates, base)]
        top = bars[-1][4]
        # 黑K：跳空開在昨高上、創新高、收黑；隔天量縮、拉回五日線附近
        bars = with_tail(bars, [(top + 3, top + 4, top + 1, top + 1.5, 4000), (top + 1.5, top + 2, top - 0.4, top + 0.3, 2000)])
        i = len(bars) - 1
        hit = module.detect_dance(bars, self.closes(bars), i)
        self.assertIsNotNone(hit)
        self.assertEqual((hit["pattern"], hit["box"]), ("dance", (i - 1, i)))
        loud = with_tail(bars[:-1], [(top + 1.5, top + 2, top - 0.4, top + 0.3, 3900)])               # 隔天沒縮量
        self.assertIsNone(module.detect_dance(loud, self.closes(loud), len(loud) - 1))

    def test_triangle(self) -> None:
        # 30 根收斂：高點每根降 0.25、低點每根升 0.25（三段的高點一段比一段低、低點一段比一段高），寬度從 20 縮到 5，然後長紅突破
        rows = []
        for k in range(30):
            hi = 120 - 0.25 * k
            lo = 100 + 0.25 * k
            rows.append((lo + 1, hi, lo, (hi + lo) / 2, 800))
        bars = with_tail(flat_bars(120, 110), rows)
        bars = with_tail(bars, [(113, 118.5, 112.5, 118, 3000)])           # 最後一段高點 ≈115，長紅 +4.4% 站上、量大
        i = len(bars) - 1
        hit = module.detect_triangle(bars, self.closes(bars), i)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["pattern"], "triangle")
        self.assertEqual(hit["box"][1], i)
        self.assertIn("lines", hit)
        self.assertTrue(hit["lines"]["top"][1] > hit["lines"]["top"][3] and hit["lines"]["bottom"][1] < hit["lines"]["bottom"][3])
        weak = with_tail(bars[:-1], [(113, 118, 112.5, 114, 3000)])         # 紅K不到 3.5%
        self.assertIsNone(module.detect_triangle(weak, self.closes(weak), len(weak) - 1))
        wide = with_tail(flat_bars(120, 110) + [(d, 110, 120, 100, 110, 800) for d in make_dates(150)[120:150]], [(113, 124, 112.5, 123, 3000)])   # 沒有收斂（寬度不變）
        self.assertIsNone(module.detect_triangle(wide, self.closes(wide), len(wide) - 1))

    def test_dragon(self) -> None:
        # 先漲 40%（月線向上）、回檔跌破月線，再站回
        closes = [100 + i * 0.5 for i in range(100)]                       # 100 → 149.5
        bars = [(d, c, c * 1.005, c * 0.995, c, 1500) for d, c in zip(make_dates(100), closes)]
        peak = closes[-1]
        tail = [(peak - 1, peak, peak - 3, peak - 2, 1500), (peak - 2, peak - 2, peak - 6, peak - 5, 1500), (peak - 5, peak - 5, peak - 9, peak - 8, 1500)]
        bars = with_tail(bars, tail * 2)
        ma20 = sum(b[4] for b in bars[-20:]) / 20
        bars = with_tail(bars, [(ma20 + 1, ma20 * 1.06, ma20, ma20 * 1.05, 4000)])
        bars = with_tail(flat_bars(30, 100), [])[:0] + bars                    # 前面不用補（已有 100 根 + 6 + 1）
        # 補到至少 130 根歷史：把前面再墊 40 根低價
        pre = [(d, 100, 100.5, 99.5, 100, 1500) for d in make_dates(40)]
        shifted = [(d, *b[1:]) for d, b in zip(make_dates(len(pre) + len(bars)), pre + bars)]
        i = len(shifted) - 1
        hit = module.detect_dragon(shifted, self.closes(shifted), i)
        self.assertIsNotNone(hit, [round(b[4], 1) for b in shifted[-9:]])
        self.assertEqual((hit["pattern"], hit["box"]), ("dragon", (i - 1, i)))
        no_vol = shifted[:-1] + [(*shifted[-1][:5], 1500)]
        self.assertIsNone(module.detect_dragon(no_vol, self.closes(no_vol), i))


class ScanQuizTests(unittest.TestCase):
    def sword_stock(self) -> list[tuple]:
        bars = flat_bars(140)
        for _ in range(2):                                                 # 同一檔連兩次紅劍，間隔 > SPACING 才會都算
            bars = with_tail(bars, [(101, 108, 100.5, 103, 5000)] + [(103, 103.5, 102.5, 103, 1000)] * 3)
            bars = with_tail(bars, [(100, 100.5, 99.5, 100, 1000)] * 30)
        return with_tail(bars, [(100, 100.5, 99.5, 100, 1000)] * 12)

    def test_scan_spacing_and_future(self) -> None:
        bars = self.sword_stock()
        hits = [h for h in module.scan(bars) if h["pattern"] == "sword"]
        self.assertEqual(len(hits), 2)
        self.assertTrue(all(len(bars) - h["end"] - 1 >= module.FUTURE for h in hits))
        close_pair = flat_bars(140)
        close_pair = with_tail(close_pair, [(101, 108, 100.5, 103, 5000)] + [(100, 100.5, 99.5, 100, 1000)] * 5 + [(101, 108, 100.5, 103, 9000)] + [(100, 100.5, 99.5, 100, 1000)] * 12)
        self.assertEqual(len([h for h in module.scan(close_pair) if h["pattern"] == "sword"]), 1)   # 隔不到 25 根只算第一個

    def test_case_payload(self) -> None:
        bars = self.sword_stock()
        hit = [h for h in module.scan(bars) if h["pattern"] == "sword"][0]
        c = module.case_payload("1234", "測試", bars, hit)
        self.assertEqual((c["pattern"], c["label"], c["code"], c["name"], c["date"]), ("sword", "紅劍", "1234", "測試", bars[hit["end"]][0]))
        self.assertEqual(len(c["bars"]), module.SHOW_BEFORE)
        self.assertEqual(len(c["future"]), module.FUTURE)
        self.assertEqual(c["box"], [len(c["bars"]) - 1, len(c["bars"]) - 1])
        self.assertEqual(c["bars"][-1][0], bars[hit["end"]][0])

    def test_quiz_balanced_and_unique(self) -> None:
        def case(p: str, code: str) -> dict:
            return {"id": f"{p}-{code}", "pattern": p, "code": code, "name": code, "future": [[1] * 6], "defense": 1, "date": "2026-01-01"}

        pool = {p: [case(p, f"{p[:2]}{k}") for k in range(5)] for p in module.PATTERNS}
        with patch.dict(module._state, {"cases": pool, "builtAt": 10 ** 12, "building": False, "error": None, "stocks": 9, "count": 25}):
            r = module.quiz(10, seed=7)
            self.assertEqual(r["status"], "ok")
            self.assertEqual(len(r["questions"]), 10)
            counts = {p: sum(1 for q in r["questions"] if q["pattern"] == p) for p in module.PATTERNS}
            self.assertEqual(set(counts.values()), {2})
            self.assertEqual(len({q["code"] for q in r["questions"]}), 10)
            self.assertEqual([q["id"] for q in r["questions"]], [q["id"] for q in module.quiz(10, seed=7)["questions"]])   # 同一個 seed 同一份
            lessons = module.lessons()
            self.assertEqual([e["pattern"] for e in lessons["examples"]], list(module.PATTERNS))
        with patch.dict(module._state, {"cases": {}, "builtAt": None, "building": True, "error": None, "stocks": 0, "count": 0}):
            self.assertEqual(module.quiz(10)["status"], "building")


if __name__ == "__main__":
    unittest.main()
