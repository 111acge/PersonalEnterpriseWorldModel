"""后台监听 00-Inbox/，文件变化时自动触发 AI 管线。"""
import io
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from pewm.paths import INBOX_DIR
from pewm.processors.log_config import get_logger

logger = get_logger(__name__)


class InboxHandler(FileSystemEventHandler):
    """监听 Inbox 目录变化，防抖后触发管线。"""

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
    """后台 AI 管线监听器。"""

    def __init__(self, callback: Optional[Callable] = None, debounce: float = 2.0):
        self.observer: Optional[Observer] = None
        self.handler: Optional[InboxHandler] = None
        self.callback = callback or self._default_callback
        self.debounce = debounce
        self._running = False
        self._last_log = ""

    def _default_callback(self):
        """默认回调：运行 run.py 管线。"""
        old_stdout = sys.stdout
        buffer = io.StringIO()
        sys.stdout = buffer
        try:
            import runpy
            argv = ["run.py", "--no-git", "--no-ocr"]
            sys.argv = argv
            runpy.run_path(str(Path(__file__).resolve().parents[2] / "run.py"), run_name="__main__")
            self._last_log = buffer.getvalue()
        except Exception as e:
            self._last_log = f"后台管线运行失败：{e}\n"
            logger.warning("后台管线运行失败：%s", e)
        finally:
            sys.stdout = old_stdout

    def start(self):
        """启动监听。"""
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
