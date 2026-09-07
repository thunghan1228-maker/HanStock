"""Deployment switches for optional quote products."""

import os


STOCK_FUTURES_DISABLED_MESSAGE = "戰鬥版已停用個股期貨行情，訂閱名額保留給股票"


def stock_futures_enabled() -> bool:
    # The battle deployment runs equities only unless explicitly re-enabled.
    return os.getenv("SHIOAJI_STOCK_FUTURES_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }
