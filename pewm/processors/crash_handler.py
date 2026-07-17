"""崩溃日志处理模块。

为 PyQt/Tk/pywebview 等 GUI 入口统一配置 sys.excepthook。
- 崩溃时写入本地专用日志目录
- 单个日志文件最大 100MB，超过自动切分
- 仅保留最近 7 天的日志，过期自动清理
- 日志内容包含崩溃时间、堆栈轨迹、系统环境信息
- 不收集、不上传任何数据到远程服务器
"""
import datetime
import os
import platform
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, List

from pewm.paths import ROOT
from pewm.processors.log_config import get_logger

logger = get_logger(__name__)

# 崩溃日志统一放在用户目录下的应用专用文件夹
_CRASH_DIR = Path.home() / ".enterprise_world_model" / "logs" / "crashes"
_MAX_FILE_BYTES = 100 * 1024 * 1024  # 100MB
_KEEP_DAYS = 7
_LOCK = threading.Lock()


def _crash_dir() -> Path:
    _CRASH_DIR.mkdir(parents=True, exist_ok=True)
    return _CRASH_DIR


def _app_version() -> str:
    version_file = ROOT / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip()
    return "unknown"


def _format_environment() -> Dict[str, Any]:
    """收集非敏感的系统环境信息，用于本地排查。"""
    return {
        "crash_time": datetime.datetime.now().isoformat(timespec="seconds"),
        "app_version": _app_version(),
        "python_version": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "os": os.name,
        "cwd": str(Path.cwd()),
    }


def _format_crash_report(exc_type, exc_value, exc_tb) -> Dict[str, Any]:
    """将崩溃三件套格式化为可序列化的报告。"""
    return {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "exc_type": exc_type.__name__ if exc_type else None,
        "exc_message": str(exc_value) if exc_value else "",
        "traceback": "".join(traceback.format_exception(exc_type, exc_value, exc_tb)) if exc_tb else "",
        "environment": _format_environment(),
    }


def _cleanup_old_logs() -> None:
    """删除超过保留期限的旧日志文件。"""
    try:
        cutoff = datetime.datetime.now() - datetime.timedelta(days=_KEEP_DAYS)
        for path in _crash_dir().glob("crash-*.log*"):
            try:
                mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
                if mtime < cutoff:
                    path.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception as e:
        logger.warning("清理旧崩溃日志失败：%s", e)


def _rotate_if_needed(base_path: Path) -> Path:
    """若目标日志文件超过大小限制，则按序号切分。"""
    if not base_path.exists() or base_path.stat().st_size < _MAX_FILE_BYTES:
        return base_path

    # 切分序号：crash-YYYY-MM-DD-HHMMSS-1.log, -2.log, ...
    counter = 1
    while True:
        rotated = base_path.with_stem(f"{base_path.stem}-{counter}")
        if not rotated.exists() or rotated.stat().st_size < _MAX_FILE_BYTES:
            return rotated
        counter += 1


def _write_crash_log(report: Dict[str, Any]) -> Path:
    """写入崩溃日志文件，并执行清理与切分。"""
    with _LOCK:
        _cleanup_old_logs()
        crash_dir = _crash_dir()
        filename = f"crash-{datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S')}.log"
        base_path = crash_dir / filename
        path = _rotate_if_needed(base_path)

        env = report["environment"]
        content = (
            f"时间：{report['timestamp']}\n"
            f"应用版本：{env['app_version']}\n"
            f"Python 版本：{env['python_version']}\n"
            f"操作系统：{env['platform']}\n"
            f"机器架构：{env['machine']}\n"
            f"处理器：{env['processor']}\n"
            f"OS 名称：{env['os']}\n"
            f"工作目录：{env['cwd']}\n"
            f"异常类型：{report['exc_type']}\n"
            f"异常消息：{report['exc_message']}\n"
            f"堆栈轨迹：\n{report['traceback']}\n"
        )
        path.write_text(content, encoding="utf-8")
        return path


def handle_crash(exc_type, exc_value, exc_tb) -> None:
    """崩溃处理入口：写本地日志、记录 logger。"""
    report = _format_crash_report(exc_type, exc_value, exc_tb)
    try:
        path = _write_crash_log(report)
        logger.error("应用崩溃，日志已写入：%s", path)
        logger.error("%s: %s", report["exc_type"], report["exc_message"])
        logger.error("Traceback:\n%s", report["traceback"])
    except Exception as e:
        # 崩溃处理本身不能再次崩溃
        sys.stderr.write(f"崩溃日志处理失败：{e}\n")
        sys.stderr.write(f"原始异常：{report.get('exc_type')}：{report.get('exc_message')}\n")


def install_crash_handler() -> None:
    """安装全局未捕获异常处理器。"""
    sys.excepthook = handle_crash


def get_recent_crash_logs(limit: int = 10) -> List[str]:
    """返回最近的崩溃日志文件路径。"""
    crash_dir = _crash_dir()
    files = sorted(crash_dir.glob("crash-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p) for p in files[:limit]]
