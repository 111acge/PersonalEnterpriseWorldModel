"""统一日志配置。

使用标准库 logging，输出到控制台和文件，按天轮转，保留 7 天。
所有模块通过 get_logger(__name__) 获取 logger。
"""
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from pewm.paths import ROOT

_LOG_DIR = ROOT / "logs"
_LOG_FILE = _LOG_DIR / "app.log"

_zerodiv = False


def setup_logging() -> None:
    """配置根日志器。应在程序入口处调用一次。"""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # 避免重复添加 handler
    if root.handlers:
        return

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台：INFO 及以上
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 文件：DEBUG 及以上，按天轮转，保留 7 天
    file_handler = TimedRotatingFileHandler(
        str(_LOG_FILE),
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的 logger。"""
    return logging.getLogger(name)
