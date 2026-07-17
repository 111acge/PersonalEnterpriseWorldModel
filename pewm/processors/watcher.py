"""后台监听 00-Inbox/，文件变化时自动触发本体生成。"""
import io
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from pewm.paths import INBOX_DIR
from pewm.processors.log_config import get_logger

logger = get_logger(__name__)

# 兼容未安装 watchdog 的环境：功能降级，但 API 不崩溃
try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_AVAILABLE = True
except Exception as _e:
    WATCHDOG_AVAILABLE = False
    FileSystemEventHandler = object  # type: ignore
    Observer = None  # type: ignore
    logger.warning("watchdog 未安装，后台监听功能不可用：%s", _e)


class InboxHandler(FileSystemEventHandler):
    """监听 Inbox 目录变化，防抖后触发本体生成。"""

    def __init__(self, callback: Callable, debounce_seconds: float = 2.0):
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def on_any_event(self, event):
        if event.is_directory:
            return
        # 忽略临时文件和 _media
        src = Path(str(event.src_path))
        if "_media" in src.parts:
            return
        if src.suffix not in (".md", ".txt"):
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_seconds, self.callback)
            self._timer.start()


class PipelineWatcher:
    """后台本体生成监听器。"""

    def __init__(self, callback: Optional[Callable] = None, debounce: float = 2.0):
        self.observer: Optional[Observer] = None  # type: ignore
        self.handler: Optional[InboxHandler] = None
        self.callback = callback or self._default_callback
        self.debounce = debounce
        self._running = False
        self._last_log = ""
        self._unavailable = not WATCHDOG_AVAILABLE

    def _default_callback(self):
        """默认回调：直接调用本体生成函数（不依赖磁盘上的 run.py，兼容打包模式）。"""
        old_stdout = sys.stdout
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            from pewm.processors.__main__ import run_pipeline
            run_pipeline(no_git=True, no_ocr=True)
            self._last_log = buffer.getvalue()
        except Exception as e:
            self._last_log = f"后台本体生成运行失败：{e}\n"
            logger.warning("后台本体生成运行失败：%s", e)
        finally:
            sys.stdout = old_stdout

    def start(self):
        """启动监听。"""
        if self._unavailable:
            self._last_log = f"[{time.strftime('%H:%M:%S')}] watchdog 未安装，后台监听不可用\n"
            logger.warning("watchdog 未安装，无法启动后台监听")
            return False
        if self._running:
            return False
        INBOX_DIR.mkdir(parents=True, exist_ok=True)
        self.handler = InboxHandler(self.callback, self.debounce)
        self.observer = Observer()
        self.observer.schedule(self.handler, str(INBOX_DIR), recursive=True)
        self.observer.start()
        self._running = True
        self._last_log = f"[{time.strftime('%H:%M:%S')}] 已启动 Inbox 监听\n"
        return True

    def stop(self):
        """停止监听。"""
        if not self._running:
            return False
        if self.observer:
            self.observer.stop()
            self.observer.join()
        self.observer = None
        self.handler = None
        self._running = False
        self._last_log += f"[{time.strftime('%H:%M:%S')}] 已停止 Inbox 监听\n"
        return True

    @property
    def running(self) -> bool:
        return self._running

    def get_logs(self) -> str:
        return self._last_log


# 全局单例
_watcher: Optional[PipelineWatcher] = None


def get_watcher() -> PipelineWatcher:
    global _watcher
    if _watcher is None:
        _watcher = PipelineWatcher()
    return _watcher
