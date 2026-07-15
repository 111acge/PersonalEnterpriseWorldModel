"""pywebview 桌面启动器（带完整启动画面）。

使用单窗口策略：
1. 窗口先加载 splash.html（本地文件，无需等待 Flask）
2. splash.html 通过 pywebview.js_api 轮询 SplashController 获取进度
3. 所有初始化（配置、数据库、模型、Flask）在 SplashController 后台线程执行
4. 初始化完成后，后端将窗口 load_url 切换到 Flask 主界面
5. 若主界面加载失败，自动兜底到 /error 页面
"""
import socket
import sys
import time
from pathlib import Path

import webview

from pewm.web.splash_controller import SplashController


def _resource_path(relative_path):
    """获取资源路径，兼容 PyInstaller 单文件模式。"""
    if getattr(sys, "frozen", False):
        base_path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base_path = Path(__file__).resolve().parent
    return str(base_path / relative_path)


def get_version() -> str:
    """从版本文件读取版本号。"""
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip()
    return "1.0.0"


def start_desktop_app(title="个人企业世界模型", width=1280, height=800):
    """启动 Flask + pywebview 桌面应用。"""
    controller = SplashController(version=get_version(), timeout=15.0)
    window = None
    main_width, main_height = width, height

    def _get_main_url():
        port = controller._flask_port
        if not port:
            return None
        return f"http://127.0.0.1:{port}/"

    def _get_error_url():
        port = controller._flask_port
        if not port:
            return _resource_path("templates/error.html")
        return f"http://127.0.0.1:{port}/error"

    def _wait_for_flask(port, timeout=5.0):
        """等待 Flask 服务真正开始监听指定端口。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    if s.connect_ex(("127.0.0.1", port)) == 0:
                        return True
            except Exception:
                pass
            time.sleep(0.05)
        return False

    def _load_url_with_retry(url, retries=3, delay=0.5):
        """尝试加载 URL，失败时重试。"""
        for i in range(retries):
            try:
                window.load_url(url)
                return True
            except Exception as e:
                print(f"[desktop] 加载 {url} 失败（{i+1}/{retries}）：{e}")
                time.sleep(delay)
        return False

    def _is_on_error_page():
        """检查当前是否停留在错误页。"""
        try:
            href = window.evaluate_js("window.location.href")
            return href and "/error" in href
        except Exception:
            return False

    def navigate_to_main():
        """切换到主界面，失败时兜底到 /error。"""
        try:
            main_url = _get_main_url()
            if not main_url:
                raise RuntimeError("Flask 端口未初始化")

            port = controller._flask_port
            if not _wait_for_flask(port):
                raise RuntimeError("Flask 服务未就绪")

            # 加载主 URL 并调整窗口
            if not _load_url_with_retry(main_url):
                raise RuntimeError("主页面加载失败")

            # 给页面一点渲染时间
            time.sleep(0.5)

            # 如果仍停在错误页，再试一次
            if _is_on_error_page():
                print("[desktop] 检测到错误页，尝试重新加载主界面...")
                time.sleep(0.5)
                if not _load_url_with_retry(main_url):
                    raise RuntimeError("主页面二次加载失败")
                time.sleep(0.5)
                if _is_on_error_page():
                    raise RuntimeError("主页面确认失败")

            try:
                window.resize(main_width, main_height)
            except Exception:
                pass

        except Exception as e:
            print(f"[desktop] 切换主界面失败：{e}")
            _load_url_with_retry(_get_error_url())

    def go_home():
        """从错误页返回首页。"""
        main_url = _get_main_url()
        if main_url:
            _load_url_with_retry(main_url)
        else:
            _load_url_with_retry(_get_error_url())

    controller._do_navigate = navigate_to_main
    controller._do_go_home = go_home

    window = webview.create_window(
        title=title,
        url=_resource_path("templates/splash.html"),
        js_api=controller,
        width=520,
        height=360,
        frameless=True,
        on_top=True,
        resizable=False,
    )
    controller.set_window(window)

    def on_shown():
        controller.start()

    window.events.shown += on_shown

    webview.start(debug=False)


if __name__ == "__main__":
    start_desktop_app()
