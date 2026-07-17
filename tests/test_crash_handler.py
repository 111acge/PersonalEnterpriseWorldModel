"""测试崩溃日志处理。"""
from pathlib import Path

from pewm.processors.crash_handler import (
    _format_crash_report,
    _format_environment,
    _write_crash_log,
    get_recent_crash_logs,
    handle_crash,
)


class FakeException:
    pass


def test_format_environment():
    env = _format_environment()
    assert "app_version" in env
    assert "python_version" in env
    assert "platform" in env


def test_format_crash_report():
    try:
        raise ValueError("boom")
    except Exception:
        import sys
        report = _format_crash_report(*sys.exc_info())
    assert report["exc_type"] == "ValueError"
    assert "boom" in report["exc_message"]
    assert "ValueError" in report["traceback"]
    assert "environment" in report


def test_write_crash_log(temp_project):
    report = {
        "timestamp": "2026-07-15T10:00:00",
        "exc_type": "RuntimeError",
        "exc_message": "fail",
        "traceback": "line 1\nline 2",
        "environment": _format_environment(),
    }
    path = _write_crash_log(report)
    assert Path(path).exists()
    text = Path(path).read_text(encoding="utf-8")
    assert "RuntimeError" in text
    assert "fail" in text
    assert "Python 版本" in text


def test_handle_crash_writes_log(temp_project):
    try:
        raise TypeError("crash")
    except Exception:
        import sys
        handle_crash(*sys.exc_info())
    logs = get_recent_crash_logs(limit=10)
    assert len(logs) >= 1


def test_recent_crash_logs_sorted(temp_project):
    report = {
        "timestamp": "2026-07-15T10:00:00",
        "exc_type": "RuntimeError",
        "exc_message": "fail",
        "traceback": "line 1",
        "environment": _format_environment(),
    }
    _write_crash_log(report)
    _write_crash_log(report)
    logs = get_recent_crash_logs(limit=2)
    assert len(logs) == 2
    assert all(str(p).endswith(".log") for p in logs)
