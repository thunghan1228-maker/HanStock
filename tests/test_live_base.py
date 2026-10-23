"""盤中版落底／回檔警示的基底（margin_call_lines.live_base）：用「昨天以前的 live_base ＋ 今天那根」重算，
要跟「把今天那根接上去、直接跑 stabilize／pullback」得到一模一樣的結果（前端 JS 照同一套公式算，盤中才跟收盤後的名單一致）。"""

from __future__ import annotations

import random
import unittest

import margin_call_lines as module


def live_eval(base, today):
    """前端 JS（mclLiveEval）的 Python 版：today＝(開, 高, 低, 收, 累計量)。回傳 (stab, pull)，格式同 stabilize／pullback。"""
    o, h, l, c, v = today
    stab = pull = None
    if base and base["avgVol20"] and base["prevChg"] is not None and base["burstPrev"] is not None and base["avgVol20"] > 0:
        ratio_t = v / base["avgVol20"]
        bp = base["burstPrev"]
        ratio, low, date = (ratio_t, l, "today") if ratio_t >= bp["ratio"] else (bp["ratio"], bp["low"], bp["date"])
        chg = (c / base["prevClose"] - 1) * 100
        stab = {"burst": ratio >= module.BURST_RATIO, "burstRatio": round(ratio, 1), "burstDate": date, "chg": round(chg, 2), "chgPrev": round(base["prevChg"], 2),
                "shrinking": base["prevChg"] < 0 and chg > base["prevChg"], "noNewLow": l >= low}
    if base and base["sum19"] is not None and base["avgVol20"] and o and o > 0 and base["avgVol20"] > 0 and base["prevClose"] > 0 and c > 0:
        ma20 = (base["sum19"] + c) / 20
        ret10, bias20, vr, chg = c / base["c10"] - 1, c / ma20 - 1, v / base["avgVol20"], c / base["prevClose"] - 1
        body, upper = abs(c - o), h - max(o, c)
        black = c < o
        shadow = upper >= 2 * max(body, 0.002 * c) and upper / c >= 0.025
        run = ret10 >= module.PULL_RUN_RET10 or bias20 >= module.PULL_RUN_BIAS20
        burst = vr >= module.PULL_BURST
        dump = black or shadow or chg < 0.01
        tier = 2 if (ret10 >= module.PULL_EXTREME_RET10 and burst and dump) else 1 if (run and burst and dump) else 0
        pull = {"tier": tier, "ret10": round(ret10 * 100, 1), "bias20": round(bias20 * 100, 1), "vr": round(vr, 1), "chg": round(chg * 100, 2),
                "black": black, "shadow": shadow, "run": run, "burst": burst, "dump": dump}
    return stab, pull


def bottom_series(rng: random.Random, n: int):
    """落底的樣子：前面平平、爆大量大跌一天，隔天再小跌，今天跌幅縮小、沒再破爆量日最低（stabilize 的三個條件都成立）。"""
    bars = []
    for i in range(n - 3):
        close = 100 + rng.uniform(-2, 2)
        bars.append((f"d{i:02d}", round(close + 1, 2), round(close - 1, 2), round(close, 2), float(rng.choice([900, 1000, 1100]))))
    burst_close = 92 + rng.uniform(-1, 1)
    bars.append((f"d{n - 3:02d}", 99.0, round(burst_close - 2, 2), round(burst_close, 2), 4000.0 + rng.choice([0, 500, 1000])))
    prev_close = burst_close - rng.uniform(2, 4)
    bars.append((f"d{n - 2:02d}", round(burst_close, 2), round(prev_close - 1, 2), round(prev_close, 2), 2000.0))
    today_close = prev_close + rng.uniform(-1.0, 1.5)
    bars.append((f"d{n - 1:02d}", round(today_close + 1.5, 2), round(min(burst_close - 1.5, today_close - 0.2), 2), round(today_close, 2), 1500.0))   # 最低價一定 ≤ 收盤（真實資料就是這樣）
    return bars


def blowoff_series(rng: random.Random, n: int):
    """漲多＋爆量的樣子：最後 10 天每天漲 3% 左右、今天爆量（留意回檔的前兩個條件成立，出貨K 看今天開高低收）。"""
    bars, close = [], 100.0
    for i in range(n):
        if i >= n - 10:
            close *= 1.03 + rng.uniform(-0.005, 0.01)
        else:
            close = close * (1 + rng.uniform(-0.01, 0.01))
        vol = float(rng.choice([900, 1000, 1100])) * (4 if i == n - 1 else 1)
        bars.append((f"d{i:02d}", round(close * 1.02, 2), round(close * 0.98, 2), round(close, 2), vol))
    return bars


def random_series(rng: random.Random, n: int, shape: str):
    if shape == "bottom":
        return bottom_series(rng, n)
    if shape == "blowoff":
        return blowoff_series(rng, n)
    bars, close = [], 100.0
    for i in range(n):
        drift = {"run": 0.012, "drop": -0.012, "flat": 0.0}[shape] if i > n - 15 else 0.0
        close = max(5.0, close * (1 + drift + rng.uniform(-0.03, 0.03)))
        high = close * (1 + rng.uniform(0, 0.04)); low = close * (1 - rng.uniform(0, 0.04))
        vol = rng.choice([800, 1000, 1200, 1500, 3000, 4500]) * (3 if i >= n - 3 and rng.random() < 0.4 else 1)
        bars.append((f"2026-09-{i + 1:02d}" if i < 30 else f"2026-10-{i - 29:02d}", round(high, 2), round(low, 2), round(close, 2), float(vol)))
    return bars


class LiveBaseTests(unittest.TestCase):
    def test_live_eval_matches_close_of_day_computation(self) -> None:
        rng = random.Random(20261023)
        checked = hit_pull = hit_stab = 0
        for case in range(600):
            shape = ("run", "drop", "flat", "bottom", "blowoff")[case % 5]
            bars = random_series(rng, rng.choice([32, 40, 55, 60]), shape)
            # 今天那根：開盤價落在高低之間（黑K、長上影都有機會）
            d, h, l, c, v = bars[-1]
            o = round(rng.uniform(l, h), 2)
            full = bars
            expected_stab = module.stabilize(full)
            expected_pull = module.pullback(full, o)
            base = module.live_base(full[:-1])
            stab, pull = live_eval(base, (o, h, l, c, v))
            self.assertEqual(stab, {**expected_stab, "burstDate": stab["burstDate"]} if expected_stab else None, f"stab case {case}")
            if expected_stab and stab:
                same_day = expected_stab["burstDate"] == d
                self.assertEqual(stab["burstDate"] == "today", same_day, f"burstDate case {case}")
            self.assertEqual(pull, expected_pull, f"pull case {case}")
            checked += 1
            hit_pull += 1 if expected_pull and expected_pull["tier"] else 0
            hit_stab += 1 if expected_stab and expected_stab["burst"] and expected_stab["shrinking"] and expected_stab["noNewLow"] else 0
        self.assertEqual(checked, 600)
        self.assertGreater(hit_pull, 10)     # 隨機資料裡兩種訊號都要有出現過，不然上面的比對沒測到「有訊號」的情況
        self.assertGreater(hit_stab, 10)

    def test_live_base_shape_and_short_history(self) -> None:
        rng = random.Random(1)
        bars = random_series(rng, 60, "flat")
        base = module.live_base(bars)
        self.assertEqual(base["prevClose"], bars[-1][3])
        self.assertEqual(base["c10"], bars[-10][3])
        self.assertAlmostEqual(base["sum19"], sum(b[3] for b in bars[-19:]), places=6)
        self.assertAlmostEqual(base["avgVol20"], sum(b[4] for b in bars[-20:]) / 20, places=6)
        self.assertIsNone(module.live_base(bars[:9]))
        short = module.live_base(bars[:15])
        self.assertIsNone(short["sum19"])
        self.assertIsNone(short["avgVol20"])
        self.assertIsNone(short["burstPrev"])


if __name__ == "__main__":
    unittest.main()
