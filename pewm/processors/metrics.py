"""本地性能埋点与指标收集。

使用 SQLite 表 `metrics` 记录关键路径耗时与成功状态，提供装饰器 `@timed`
与显式记录接口。所有数据保存在 data/world-model.db 中，便于在「设置」页展示。
"""
import functools
import sqlite3
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pewm.paths as paths

_thread_local = threading.local()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _connection() -> sqlite3.Connection:
    """获取线程本地 SQLite 连接。"""
    conn = getattr(_thread_local, "metrics_conn", None)
    if conn is None:
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(paths.DB_PATH))
        conn.row_factory = sqlite3.Row
        _thread_local.metrics_conn = conn
    return conn


def close_connection() -> None:
    """显式关闭当前线程的指标连接。"""
    conn = getattr(_thread_local, "metrics_conn", None)
    if conn is not None:
        conn.close()
        _thread_local.metrics_conn = None


def init_metrics_table() -> None:
    """初始化指标表（与业务表共存于 world-model.db）。"""
    conn = _connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT NOT NULL,
            duration_ms INTEGER,
            success BOOLEAN NOT NULL DEFAULT 1,
            error_msg TEXT,
            meta TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_metrics_event_time
        ON metrics(event, created_at)
        """
    )
    conn.commit()


def record(
    event: str,
    duration_ms: Optional[int] = None,
    success: bool = True,
    error_msg: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """记录一条指标。"""
    try:
        init_metrics_table()
        conn = _connection()
        meta_json = ""
        if meta:
            import json

            meta_json = json.dumps(meta, ensure_ascii=False, default=str)
        conn.execute(
            """
            INSERT INTO metrics (event, duration_ms, success, error_msg, meta, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event,
                duration_ms,
                1 if success else 0,
                error_msg or "",
                meta_json,
                _now_iso(),
            ),
        )
        conn.commit()
    except Exception as e:
        # 埋点失败不应影响主流程
        print(f"[metrics] 记录指标失败：{e}")


@contextmanager
def timed_ctx(event: str, meta: Optional[Dict[str, Any]] = None):
    """上下文管理器：记录代码块耗时。"""
    start = time.perf_counter()
    error_msg = ""
    success = True
    try:
        yield
    except Exception as e:
        success = False
        error_msg = f"{type(e).__name__}: {e}"
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        record(event, duration_ms=duration_ms, success=success, error_msg=error_msg, meta=meta)


def timed(event: str):
    """装饰器：记录函数调用耗时。"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            success = True
            error_msg = ""
            try:
                return func(*args, **kwargs)
            except Exception as e:
                success = False
                error_msg = f"{type(e).__name__}: {e}"
                raise
            finally:
                duration_ms = int((time.perf_counter() - start) * 1000)
                record(event, duration_ms=duration_ms, success=success, error_msg=error_msg)

        return wrapper

    return decorator


def get_recent(event: Optional[str] = None, limit: int = 100) -> list:
    """获取最近指标。"""
    try:
        init_metrics_table()
        conn = _connection()
        if event:
            rows = conn.execute(
                "SELECT * FROM metrics WHERE event = ? ORDER BY created_at DESC LIMIT ?",
                (event, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM metrics ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[metrics] 查询指标失败：{e}")
        return []


def get_summary(event: str, limit: int = 100) -> Dict[str, Any]:
    """返回某个事件的统计摘要。"""
    try:
        init_metrics_table()
        conn = _connection()
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS count,
                AVG(duration_ms) AS avg_ms,
                MAX(duration_ms) AS max_ms,
                MIN(duration_ms) AS min_ms,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count
            FROM (
                SELECT * FROM metrics WHERE event = ? ORDER BY created_at DESC LIMIT ?
            )
            """,
            (event, limit),
        ).fetchone()
        if row is None:
            return {}
        data = dict(row)
        total = data.get("count", 0) or 0
        success = data.get("success_count", 0) or 0
        data["success_rate"] = round(success / total, 4) if total > 0 else 1.0
        return data
    except Exception as e:
        print(f"[metrics] 汇总指标失败：{e}")
        return {}
