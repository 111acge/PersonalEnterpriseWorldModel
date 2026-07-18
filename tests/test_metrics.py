"""性能埋点模块测试。"""
import time

import pytest

from pewm.processors.metrics import get_recent, get_summary, init_metrics_table, record, timed


def test_record_and_query(temp_project):
    init_metrics_table()
    record("test.event", duration_ms=100, success=True)
    rows = get_recent(event="test.event", limit=10)
    assert len(rows) >= 1
    assert rows[0]["event"] == "test.event"
    assert rows[0]["duration_ms"] == 100
    assert rows[0]["success"] == 1


def test_timed_decorator_records_success(temp_project):
    init_metrics_table()

    @timed("test.decorated")
    def work():
        return 42

    result = work()
    assert result == 42
    rows = get_recent(event="test.decorated", limit=10)
    assert len(rows) >= 1
    assert rows[0]["success"] == 1


def test_timed_decorator_records_failure(temp_project):
    init_metrics_table()

    @timed("test.decorated_fail")
    def fail():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        fail()

    rows = get_recent(event="test.decorated_fail", limit=10)
    assert len(rows) >= 1
    assert rows[0]["success"] == 0
    assert "boom" in rows[0]["error_msg"]


def test_init_metrics_table_idempotent(temp_project):
    """init_metrics_table 应只真正执行一次（模块级标志），重复调用不报错。"""
    init_metrics_table()
    init_metrics_table()
    init_metrics_table()
    # 重置后可再次执行（切换数据库的场景）
    from pewm.processors.metrics import close_connection
    close_connection()
    init_metrics_table()
    record("test.idempotent", success=True)
    rows = get_recent(event="test.idempotent", limit=1)
    assert len(rows) == 1


def test_summary_statistics(temp_project):
    init_metrics_table()
    # 使用唯一事件名避免受历史数据影响
    event = "test.summary.unique"
    for i in range(3):
        record(event, duration_ms=100 + i * 50, success=(i < 2))
    summary = get_summary(event, limit=10)
    assert summary["count"] == 3
    assert summary["success_count"] == 2
    assert summary["avg_ms"] == 150.0
    assert summary["max_ms"] == 200
    assert summary["min_ms"] == 100
