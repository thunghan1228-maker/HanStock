from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import persistent_app


class PersistenceLifespanTests(unittest.TestCase):
    def test_fastapi_lifespan_starts_both_persistence_workers(self) -> None:
        async def exercise_lifespan() -> None:
            async with persistent_app.app.router.lifespan_context(persistent_app.app):
                pass

        with (
            patch.object(persistent_app, "start_group_strength_collector") as group_worker,
            patch.object(persistent_app, "start_intraday_signal_collector") as signal_worker,
            patch.object(persistent_app, "start_stock_bar_repair_collector") as repair_worker,
        ):
            asyncio.run(exercise_lifespan())

        group_worker.assert_called_once_with()
        signal_worker.assert_called_once_with()
        repair_worker.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
