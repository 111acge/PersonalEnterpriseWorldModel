"""崩溃日志处理模块。

- 为 PyQt/Tk/pywebview 等 GUI 入口统一配置 sys.excepthook
- 崩溃时写入 logs/crash-YYYY-MM-DD-HHMMSS.log
- 支持可选的上报开关（仅预留接口，不上传敏感信息）
"""
import datetime
import sys
import traceback
from pathlib import Path
from typing import Any, Dict

from pewm.paths import ROOT
from pewm.processors.llm_client import load_config
from pewm.processors.log_config import get_logger

logger = get_logger(__name__)


def _crash_dir() -> Path:
    path = ROOT / "logs" / "crashes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _format_crash_report(exc_type, exc_value, exc_tb) -> Dict[str, Any]:
    """将崩溃三件套格式化为可序列化的报告。"""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    return {
        "timestamp": now,
        "exc_type": exc_type.__name__ if exc_type else None,
        "exc_message": str(exc_value) if exc_value else "",
        "traceback": "".join(traceback.format_exception(exc_type, exc_value, exc_tb)) if exc_tb else "",
    }


def _write_crash_log(report: Dict[str, Any]) -> Path:
    """写入崩溃日志文件。"""
    crash_dir = _crash_dir()
    filename = f"crash-{datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S')}.log"
    path = crash_dir / filename
    path.write_text(
        f"时间：{report['timestamp']}\n"
        f"异常类型：{report['exc_type']}\n"
        f"异常消息：{report['exc_message']}\n"
        f"堆栈：\n{report['traceback']}\n",
        encoding="utf-8",
    )
    return path


def _should_upload() -> bool:
    """读取是否开启崩溃上报。默认关闭。"""
    cfg = load_config()
    return bool(cfg.get("crash_reporting_enabled", False))


def _upload_report(report: Dict[str, Any]) -> None:
    """预留：崩溃上报接口。

    当前实现仅记录日志，不上传任何信息，避免泄露 API Key 与本地路径。
    如需开启服务端收集，可在此实现 HTTP 上报，并确保：
    - 仅上报异常类型、消息、版本号、时间戳，不上报堆栈中的文件路径
    - 用户明确同意隐私协议
    """
    logger.info("崩溃上报开关已关闭，跳过上传。")


def handle_crash(exc_type, exc_value, exc_tb) -> None:
    """崩溃处理入口：写日志、记录 logger、可选上报。"""
    report = _format_crash_report(exc_type, exc_value, exc_tb)
    try:
        path = _write_crash_log(report)
        logger.error("应用崩溃，日志已写入：%s", path)
        logger.error("%s: %s", report["exc_type"], report["exc_message"])
        logger.error("Traceback:\n%s", report["traceback"])
        if _should_upload():
            _upload_report(report)
    except Exception as e:
        # 崩溃处理本身不能再次崩溃
        sys.stderr.write(f"崩溃日志处理失败：{e}\n")
        sys.stderr.write(f"原始异常：{report.get('exc_type')}：{report.get('exc_message')}\n")


def install_crash_handler() -> None:
    """安装全局未捕获异常处理器。"""
    sys.excepthook = handle_crash


def get_recent_crash_logs(limit: int = 10) -> list:
    """返回最近的崩溃日志文件路径。"""
    crash_dir = _crash_dir()
    files = sorted(crash_dir.glob("crash-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p) for p in files[:limit]]
