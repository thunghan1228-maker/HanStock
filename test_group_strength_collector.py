from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import group_strength_collector as module


TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 9, 1, 9, 32, 45, tzinfo=TAIPEI)


def _rows(count: int = 67, *, with_rank: bool = True):
    return [
        {
            "group": f"族群{index:02d}",
            "avgChange": float(count - index),
            **({"rank": index} if with_rank else {}),
        }
        for index in range(1, count + 1)
    ]


def test_accepts_current_group_strength_payload_fields():
    data = {
        "liveData": True,
        "sourceDate": "2026/09/01",
        "snapshotTs": 1_788_226_320_000,
        "rows": _rows(),
    }

    assert module._snapshot_identity(data, NOW) == ("2026-09-01", 1_788_226_200_000)
    ranks = module._extract_ranks(data)
    assert len(ranks) == 67
    assert ranks["族群01"] == 1


def test_builds_ranks_from_average_change_when_rank_is_missing():
    ranks = module._extract_ranks({"rows": _rows(with_rank=False)})

    assert len(ranks) == 67
    assert ranks["族群01"] == 1
    assert ranks["族群67"] == 67


def test_rejects_stale_or_explicitly_non_live_payload():
    assert module._snapshot_identity({"liveData": True, "sourceDate": "2026-08-31"}, NOW) is None
    assert module._snapshot_identity({"liveData": False, "sourceDate": "2026-09-01"}, NOW) is None


def test_falls_back_to_current_five_minute_bucket_for_live_payload():
    assert module._snapshot_identity({"liveData": True}, NOW) == (
        "2026-09-01",
        1_788_226_200_000,
    )
