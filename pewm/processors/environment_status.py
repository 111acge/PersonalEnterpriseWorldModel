"""环境状态汇总模块。

整合 torch、OCR、watchdog 等核心依赖的运行状态，供前端"环境状态"面板展示。
"""
import importlib.metadata
import sys
from pathlib import Path
from typing import Any, Dict

import pewm.paths as paths
from pewm.processors.log_config import get_logger
from pewm.processors.metrics import record
from pewm.processors.torch_validator import get_torch_status

logger = get_logger(__name__)


def _resource_path(*parts: str) -> Path:
    """返回资源文件的绝对路径。兼容源码模式与 PyInstaller 单文件模式。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS).joinpath(*parts)
    return paths.ROOT.joinpath(*parts)


def _ocr_status() -> Dict[str, Any]:
    """获取 OCR 相关状态（本地 PaddleOCR / API 模式）。"""
    status = {
        "available": False,
        "mode": "none",
        "provider": None,
        "version": None,
        "local_backend": False,
        "resource_usage": None,
        "error": None,
    }
    try:
        from pewm.processors.ocr_api import load_ocr_config

        cfg = load_ocr_config()
        status["mode"] = cfg.get("mode", "local")
        status["provider"] = cfg.get("provider", "baidu")
    except Exception as e:
        status["error"] = f"读取 OCR 配置失败：{e}"
        return status

    if status["mode"] == "local":
        try:
            from paddleocr import PaddleOCR

            status["local_backend"] = True
            status["available"] = True
            try:
                status["version"] = importlib.metadata.version("paddleocr")
            except Exception:
                status["version"] = "unknown"
        except Exception as e:
            status["error"] = f"本地 PaddleOCR 不可用：{e}"
    else:
        # API 模式：依赖 requests，不占用本地资源
        status["available"] = True
        status["error"] = None

    return status


def _watchdog_status() -> Dict[str, Any]:
    """获取 watchdog 安装与运行状态。"""
    status = {
        "installed": False,
        "version": None,
        "running": False,
        "watch_dir": None,
        "error": None,
    }
    try:
        from pewm.paths import INBOX_DIR
        from pewm.processors.watcher import get_watcher

        status["installed"] = True
        status["version"] = importlib.metadata.version("watchdog")
        watcher = get_watcher()
        status["running"] = watcher.running
        status["watch_dir"] = str(INBOX_DIR)
    except Exception as e:
        status["error"] = f"watchdog 状态获取失败：{e}"
    return status


def get_environment_status() -> Dict[str, Any]:
    """汇总环境状态，返回结构化字典。"""
    torch_status = get_torch_status()
    ocr_status = _ocr_status()
    watchdog_status = _watchdog_status()

    report = {
        "torch": torch_status,
        "ocr": ocr_status,
        "watchdog": watchdog_status,
        "healthy": torch_status.get("healthy", False) and ocr_status.get("available", False) is not False,
    }

    record(
        "environment.status",
        success=report["healthy"],
        error_msg="; ".join(
            filter(None, [torch_status.get("errors") and "; ".join(torch_status["errors"]), ocr_status.get("error"), watchdog_status.get("error")])
        ),
        meta=report,
    )
    return report


if __name__ == "__main__":
    import json

    status = get_environment_status()
    print(json.dumps(status, ensure_ascii=False, indent=2))
