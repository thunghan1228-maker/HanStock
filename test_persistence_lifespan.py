from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import persistent_app


class PersistenceLifespanTests(unittest.TestCase):
    def test_fastapi_lifespan_starts_all_persistence_workers(self) -> None:
        # 這個測試曾經對著4個早就不存在/從沒被呼叫過的函式斷言
        # (start_triangle_intraday_collector、start_daily_pick_collector、
        # stop_daily_pick_collector從未在persistent_app.py裡被匯入或呼叫，
        # start_stock_bar_repair_collector雖然存在但也從來沒被接上)，長期
        # 停留在失敗狀態、被誤當成跟改動無關的既有失敗——但main_force_
        # backfill_jobs佇列裡的工作永遠沒有worker處理，根因正是這裡。
        # 改成驗證persistent_app.py實際上會啟動的每一個背景工作。
        async def exercise_lifespan() -> None:
            async with persistent_app.app.router.lifespan_context(persistent_app.app):
                pass

        # queue_backfill_for_all_group_stocks特意不在這裡斷言：它是從lifespan
        # 內另外spawn的背景執行緒呼叫的(fire-and-forget，不等它完成)，跟這個
        # 測試同步斷言的其他collector不一樣，直接patch+assert會有時間點競爭、
        # 測試不穩定。它自己的行為已經由test_main_force_backfill_jobs.py的
        # test_queue_backfill_for_all_group_stocks_covers_recent_weekdays驗證。
        with (
            patch.object(persistent_app, "start_main_force_collector") as main_force_worker,
            patch.object(persistent_app, "start_intraday_large_order_collector") as large_order_worker,
            patch.object(persistent_app, "start_four_gate_signals_collector") as four_gate_worker,
            patch.object(persistent_app, "start_daily_bars_collector") as daily_bars_worker,
            patch.object(persistent_app, "start_after_hours_fixed_price_collector") as after_hours_worker,
            patch.object(persistent_app, "start_otc_gap_backfill") as otc_gap_worker,
            patch.object(persistent_app, "start_stock_bar_repair_collector") as repair_worker,
            patch.object(persistent_app, "start_kline_signal_backfill_collector") as kline_backfill_worker,
        ):
            asyncio.run(exercise_lifespan())

        main_force_worker.assert_called_once_with()
        large_order_worker.assert_called_once_with()
        four_gate_worker.assert_called_once_with()
        daily_bars_worker.assert_called_once_with()
        after_hours_worker.assert_called_once_with()
        otc_gap_worker.assert_called_once_with()
        repair_worker.assert_called_once_with()
        kline_backfill_worker.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
