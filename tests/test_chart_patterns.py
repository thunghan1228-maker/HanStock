"""形態選股（chart_patterns）：用人造的 W 底、頭肩底、C 不破 A 的低、VCP 收斂驗證偵測、狀態（等突破／剛突破）、觸發價；以及不該誤判的情況。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import chart_patterns as module


def series(points: list[tuple[float, int]], start_price: float, vols: list[float] | None = None, wiggle: float = 0.004) -> list[module.Bar]:
    """從 start_price 出發，依序往每個 (目標價, 幾根) 線性走過去；高低各在收盤上下 wiggle。"""
    closes = [start_price]
    for price, n in points:
        base = closes[-1]
        for i in range(1, n + 1):
            closes.append(base + (price - base) * i / n)
    d0 = date(2026, 1, 5)
    bars = []
    for i, c in enumerate(closes):
        v = (vols[i] if vols else 1000.0)
        bars.append(((d0 + timedelta(days=i)).isoformat(), c * (1 + wiggle), c * (1 - wiggle), c, v))
    return bars


class ZigzagTests(unittest.TestCase):
    def test_pivots_alternate_and_last_is_provisional(self) -> None:
        bars = series([(80, 20), (100, 20), (85, 15)], 100.0)
        p = module.zigzag(bars, 0.07)
        self.assertEqual([x["kind"] for x in p], ["H", "L", "H", "L"])          # 起點 100 本身就是第一個高點
        self.assertEqual([x["confirmed"] for x in p], [True, True, True, False])   # 最後那個低點還沒被反向走勢確認
        self.assertAlmostEqual(p[1]["price"], 80 * 0.996, delta=0.5)
        self.assertEqual(module.zigzag(bars[:1], 0.07), [])


class DetectTests(unittest.TestCase):
    pre = [(100.0, 60)]          # 前面平平 60 根，湊足 MIN_BARS、也讓均線有東西算

    def test_w_bottom_candidate_and_breakout(self) -> None:
        bars = series(self.pre + [(80, 40), (92, 15), (80.8, 15), (88, 8)], 100.0)
        w = module.detect_w(bars)
        self.assertIsNotNone(w)
        self.assertEqual((w["name"], w["state"]), ("W底", "candidate"))
        self.assertAlmostEqual(w["trigger"], 92 * 1.004, delta=0.6)            # 頸線＝中間反彈的高點
        self.assertLess(w["stop"], w["low2"]["price"])
        self.assertGreater(w["target"], w["trigger"])
        self.assertEqual(module.detect_all(bars).get("w", {}).get("name"), "W底")
        broke = series(self.pre + [(80, 40), (92, 15), (80.8, 15), (88, 8), (95, 4)], 100.0)       # 最後 4 根站上頸線
        w2 = module.detect_w(broke)
        self.assertEqual(w2["state"], "breakout")

    def test_w_rejects_when_second_low_breaks_or_no_prior_drop(self) -> None:
        deeper = series(self.pre + [(80, 40), (92, 15), (74, 15), (80, 8)], 100.0)               # 第二個低點比第一個低 7.5%
        self.assertIsNone(module.detect_w(deeper))
        no_drop = series([(100.0, 60), (88, 5), (96, 10), (88.5, 10), (92, 6)], 100.0)            # 前面只跌 12% 以內且沒有前高轉折
        self.assertIsNone(module.detect_w(no_drop[:70]))
        self.assertIsNone(module._state(series([(70, 5)], 100.0), 100.0))                        # 現價離觸發價超過 15%：不列候選
        self.assertEqual(module._state(series([(90, 5)], 100.0), 100.0), "candidate")

    def test_head_and_shoulders_bottom(self) -> None:
        bars = series(self.pre + [(85, 15), (95, 10), (75, 15), (95, 10), (86, 10), (90, 3)], 100.0)
        hs = module.detect_hs(bars)
        self.assertIsNotNone(hs)
        self.assertEqual((hs["name"], hs["state"]), ("頭肩底", "candidate"))
        self.assertLess(hs["head"]["price"], hs["left"]["price"])
        self.assertLess(hs["head"]["price"], hs["right"]["price"])
        self.assertAlmostEqual(hs["trigger"], 95 * 1.004, delta=0.6)
        self.assertGreater(hs["target"], hs["trigger"] + 15)                                      # 頸線 + 頭到頸線的高度
        flat_head = series(self.pre + [(85, 15), (95, 10), (84, 15), (95, 10), (86, 10), (90, 3)], 100.0)   # 頭只比左肩低 1%：不算
        self.assertIsNone(module.detect_hs(flat_head))

    def test_higher_low_c_does_not_break_a(self) -> None:
        bars = series([(90, 60), (70, 20), (85, 15), (76, 12), (82, 5)], 50.0)
        hl = module.detect_hl(bars)
        self.assertIsNotNone(hl, module.zigzag(bars, 0.08))
        self.assertEqual((hl["name"], hl["state"]), ("C不破A低", "candidate"))
        self.assertGreater(hl["c"]["price"], hl["a"]["price"] * 1.04)
        self.assertAlmostEqual(hl["trigger"], 85 * 1.004, delta=0.6)
        lower = series([(90, 60), (70, 20), (85, 15), (66, 12), (74, 5)], 50.0)                  # C 破了 A 的低
        self.assertIsNone(module.detect_hl(lower))

    def test_vcp_shrinking_pullbacks(self) -> None:
        pts = [(100, 80), (80, 15), (98, 10), (88, 10), (97, 8), (92, 6), (95, 3)]
        n = len(series(pts, 50.0))
        vols = [1500.0] * (n - 40) + [700.0] * 40                                                 # 最後 40 根量縮
        bars = series(pts, 50.0, vols)
        v = module.detect_vcp(bars)
        self.assertIsNotNone(v, module.zigzag(bars, 0.045))
        self.assertEqual((v["name"], v["state"]), ("VCP收斂", "candidate"))
        self.assertGreaterEqual(len(v["contractions"]), 2)
        self.assertTrue(all(v["contractions"][i] < v["contractions"][i - 1] for i in range(1, len(v["contractions"]))))   # 一次比一次小
        self.assertLess(v["volRatio"], 0.95)
        loud = series(pts, 50.0)                                                                  # 量沒縮（全程同量）
        self.assertIsNone(module.detect_vcp(loud))
        gp = [(100, 80), (80, 12), (98, 10), (70, 14), (97, 10), (92, 6), (95, 3)]               # 第二次回檔（98→70）比第一次（100→80）更深
        gn = len(series(gp, 50.0))
        growing = series(gp, 50.0, [1500.0] * (gn - 40) + [700.0] * 40)
        self.assertIsNone(module.detect_vcp(growing))

    def test_short_history_and_detect_all(self) -> None:
        self.assertEqual(module.detect_all(series([(90, 20)], 100.0)), {})
        self.assertEqual(module.detect_all(series([(100, 100)], 100.0)), {})                       # 一直線：什麼形態都沒有


class PayloadTests(unittest.TestCase):
    def test_payload_lists_only_stocks_with_patterns(self) -> None:
        from unittest.mock import patch

        w_bars = series([(100.0, 60), (80, 40), (92, 15), (80.8, 15), (88, 8)], 100.0)
        flat = series([(100, 110)], 100.0)
        as_of = w_bars[-1][0]
        flat = flat[:len(w_bars)] if len(flat) >= len(w_bars) else flat
        fake = {"2330": w_bars, "2317": [(d, h, l, c, v) for (d, h, l, c, v) in flat]}
        with patch("brew_launch._latest_bar_date", return_value=as_of), patch("brew_launch._load_bars", return_value=fake), \
                patch("stock_groups.STOCK_GROUPS", {"測試族群": [("2330", "甲"), ("2317", "乙")]}), patch("stock_groups.SPECIAL_GROUP_NAMES", set()), \
                patch("brew_launch_history.group_and_name", side_effect=lambda c: ("測試族群", {"2330": "甲", "2317": "乙"}[c])):
            out = module.payload()
        self.assertEqual((out["status"], out["date"]), ("ok", as_of))
        self.assertEqual([r["code"] for r in out["rows"]], ["2330"])
        self.assertEqual(out["counts"]["w"], 1)
        self.assertGreater(out["rows"][0]["patterns"]["w"]["distPct"], 0)                         # 觸發價在現價上面


if __name__ == "__main__":
    unittest.main()
