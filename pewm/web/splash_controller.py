"""启动界面控制器。

负责在启动画面展示期间执行所有初始化：
- 读取核心配置
- 初始化数据库
- 初始化向量库（不加载 embedding 模型）
- 启动 Flask 服务
- embedding 模型改为延迟加载：进入主界面后在后台预热，或首次检索时加载

通过 js_api 暴露给前端，前端每 100ms 轮询进度。
"""
import logging
import socket
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from pewm.processors.log_config import get_logger
from pewm.processors.metrics import record, timed

logger = get_logger(__name__)


@dataclass
class SplashState:
    progress: int = 0
    status: str = "准备启动..."
    phase: str = "idle"  # idle | loading | error | done
    error: str = ""
    error_detail: str = ""
    can_retry: bool = False
    can_exit: bool = True
    version: str = "1.0.0"
    start_time: float = field(default_factory=time.time)


class SplashController:
    """启动画面后端 API，供 splash.html 通过 pywebview.js_api 调用。"""

    def __init__(self, version: str = "1.0.0", timeout: float = 15.0):
        self.state = SplashState(version=version)
        self.timeout = timeout
        self._window = None
        self._flask_app = None
        self._flask_thread: Optional[threading.Thread] = None
        self._flask_port: Optional[int] = None
        self._init_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._on_complete: Optional[Callable] = None
        self._phase_results: dict = {}
        self._maximized = False
        self._minimized = False

    def set_window(self, window):
        self._window = window

    def on_complete(self, callback: Callable):
        self._on_complete = callback

    def start(self):
        """在后台线程启动初始化流程。"""
        if self._init_thread and self._init_thread.is_alive():
            return
        self._stop_event.clear()
        self._init_thread = threading.Thread(target=self._run_init, daemon=True)
        self._init_thread.start()

    def _update(self, progress: int, status: str, phase: str = "loading"):
        with self._lock:
            self.state.progress = progress
            self.state.status = status
            self.state.phase = phase

    def _set_error(self, message: str, detail: str = "", can_retry: bool = True):
        with self._lock:
            self.state.phase = "error"
            self.state.error = message
            self.state.error_detail = detail
            self.state.can_retry = can_retry
            self.state.status = "加载失败"

    def _run_init(self):
        """分阶段执行初始化，无依赖阶段并行化。"""
        try:
            self._update(0, "正在读取核心配置...")
            self._phase_results = {}

            # 阶段 1：可并行化的初始化
            self._update(5, "正在并行初始化核心服务...")
            with ThreadPoolExecutor(max_workers=3) as executor:
                future_config = executor.submit(self._phase_read_config)
                future_db = executor.submit(self._phase_init_database)
                future_vec = executor.submit(self._phase_init_vector_db)

                results = {
                    "config": future_config.result(),
                    "database": future_db.result(),
                    "vector_db": future_vec.result(),
                }
                self._phase_results.update(results)

            if self._stop_event.is_set():
                return

            self._update(60, "正在启动本地服务...")
            self._phase_start_flask()

            if self._stop_event.is_set():
                return

            self._update(100, "加载完成，正在进入主界面...", phase="done")
            if self._on_complete:
                self._on_complete(self._flask_port)
            # 启动完成后后台预热 embedding 模型（不阻塞主界面）
            threading.Thread(target=self._background_warmup, daemon=True).start()
            time.sleep(0.3)
            self._fade_to_main()
        except Exception as e:
            detail = traceback.format_exc()
            logger.error("初始化失败：%s\n%s", e, detail)
            self._set_error(f"初始化失败：{e}", detail=detail, can_retry=True)
            record(
                "splash.init",
                duration_ms=int((time.time() - self.state.start_time) * 1000),
                success=False,
                error_msg=f"{type(e).__name__}: {e}",
            )

    def _background_warmup(self):
        """进入主界面后在后台预热 embedding 模型（可选，不阻塞 UI）。"""
        try:
            from pewm.processors.vector_db import _load_embedder

            record("splash.warmup_start")
            start = time.perf_counter()
            _load_embedder()
            duration_ms = int((time.perf_counter() - start) * 1000)
            record("splash.warmup_done", duration_ms=duration_ms)
        except Exception as e:
            record(
                "splash.warmup_done",
                success=False,
                error_msg=f"{type(e).__name__}: {e}",
            )
            logger.warning("后台模型预热失败：%s", e)

    def _fade_to_main(self):
        """从启动画面淡出，切换到主界面。"""
        try:
            if self._window:
                self._window.evaluate_js("document.getElementById('splash').classList.add('fade-out')")
            time.sleep(0.6)  # 等待 CSS 淡出动画
            if self._do_navigate:
                self._do_navigate()
        except Exception as e:
            logger.warning("切换主界面失败：%s", e)

    @timed("splash.phase.config")
    def _phase_read_config(self):
        """读取核心配置。"""
        from pewm.processors.llm_client import load_config
        from pewm.processors.ocr_api import load_ocr_config
        from pewm.processors.prompt_config import load_prompt
        from pewm.processors.user_profile import load_profile
        from pewm.processors.torch_validator import validate_torch_environment

        load_config()
        load_ocr_config()
        load_profile()
        load_prompt()
        # 启动期完成 torch 环境验证并记录结果
        validate_torch_environment()
        return {"ok": True}

    @timed("splash.phase.database")
    def _phase_init_database(self):
        """初始化 SQLite 数据库。"""
        from pewm.processors.database import init_db
        init_db()
        return {"ok": True}

    @timed("splash.phase.vector_db")
    def _phase_init_vector_db(self):
        """初始化向量库（不加载 embedding 模型）。"""
        from pewm.processors.vector_db import VectorDB
        VectorDB()
        return {"ok": True}

    def _phase_start_flask(self):
        """启动 Flask 服务。"""
        from pewm.web.app import create_app

        self._flask_port = self._find_free_port()
        self._flask_app = create_app()

        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)

        self._flask_thread = threading.Thread(
            target=self._flask_app.run,
            kwargs={"host": "127.0.0.1", "port": self._flask_port,
                    "debug": False, "use_reloader": False, "threaded": True},
            daemon=True,
        )
        self._flask_thread.start()

        # 等待 Flask 就绪
        for _ in range(200):
            if self._stop_event.is_set():
                return
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", self._flask_port)) == 0:
                    record("splash.phase.flask", duration_ms=int((time.time() - self.state.start_time) * 1000))
                    return
            time.sleep(0.05)
        raise RuntimeError("Flask 服务启动超时")

    def _find_free_port(self, start=14725):
        for port in range(start, start + 100):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    return port
        raise RuntimeError("找不到可用端口")

    # ========== js_api 暴露给前端 ==========

    def get_progress(self):
        """前端轮询进度。"""
        elapsed = time.time() - self.state.start_time
        # 超时检测
        if self.state.phase == "loading" and elapsed > self.timeout:
            self._set_error(
                f"加载超时（已超过 {self.timeout:.0f} 秒）",
                detail="请检查网络连接或模型文件完整性。",
                can_retry=True,
            )
        return {
            "progress": self.state.progress,
            "status": self.state.status,
            "phase": self.state.phase,
            "error": self.state.error,
            "errorDetail": self.state.error_detail,
            "canRetry": self.state.can_retry,
            "canExit": self.state.can_exit,
            "version": self.state.version,
            "elapsed": round(elapsed, 1),
        }

    def retry(self):
        """重试初始化。"""
        with self._lock:
            self.state = SplashState(version=self.state.version)
            self.state.start_time = time.time()
        self.start()
        return {"success": True}

    def minimize_window(self):
        """最小化窗口。"""
        try:
            if self._window:
                self._window.minimize()
                self._minimized = True
        except Exception as e:
            logger.warning("最小化窗口失败：%s", e)
            return {"success": False, "error": str(e)}
        return {"success": True}

    def maximize_window(self):
        """最大化/还原窗口。"""
        try:
            if not self._window:
                return {"success": True}
            # 优先使用窗口对象自带的状态查询
            try:
                currently_maximized = bool(self._window.is_maximized)
            except Exception:
                currently_maximized = self._maximized

            if currently_maximized:
                self._window.restore()
                self._maximized = False
            else:
                self._window.maximize()
                self._maximized = True
        except Exception as e:
            logger.warning("最大化/还原窗口失败：%s", e)
            return {"success": False, "error": str(e)}
        return {"success": True}

    def close_window(self):
        """关闭当前窗口并退出应用进程。"""
        try:
            if self._window:
                self._window.destroy()
        except Exception as e:
            logger.warning("关闭窗口失败：%s", e)
        # 窗口真实存在时退出进程，避免残留；测试中 _window 为 None，仅返回 success
        if self._window:
            sys.exit(0)
        return {"success": True}

    def exit_app(self):
        """退出应用。"""
        self._stop_event.set()
        self.close_window()
        sys.exit(0)

    def go_home(self):
        """从错误页返回首页。"""
        if hasattr(self, "_do_go_home"):
            self._do_go_home()
        return {"success": True}

    def navigate_to_main(self):
        """前端动画结束后调用，切换到主界面。"""
        # 由 desktop.py 注入实现
        if hasattr(self, "_do_navigate"):
            self._do_navigate()
        return {"success": True}
