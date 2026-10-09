import triangle_daily_collector as collector


def test_collector_is_permanently_disabled():
    # 三角收斂與 VCP 已移除：收集器只剩停用的空殼，不抓資料、不掃描。
    assert collector.start_triangle_daily_collector() is False
    result = collector.collect_once()
    assert result["status"] == "disabled"
    assert result["insertedBars"] == 0
    assert result["matchedCount"] == 0
    assert collector.triangle_daily_collector_status() == result


def test_status_is_a_copy():
    status = collector.triangle_daily_collector_status()
    status["status"] = "completed"
    assert collector.triangle_daily_collector_status()["status"] == "disabled"
