"""pywebview 桌面启动器（带完整启动画面）。

使用单窗口策略：
1. 窗口先加载 splash.html（本地文件，无需等待 Flask）
2. splash.html 通过 pywebview.js_api 轮询 SplashController 获取进度
3. 所有初始化（配置、数据库、模型、Flask）在 SplashController 后台线程执行
4. 初始化完成后，前端淡出启动界面，后端将窗口 load_url 切换到 Flask 主界面
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

    def _verify_page_loaded(timeout=5.0):
        """验证主页面是否真正加载成功。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = window.evaluate_js(
                    "document && document.title && document.title.indexOf('个人企业世界模型') !== -1"
                )
                if result:
                    return True
            except Exception:
                pass
            time.sleep(0.2)
        return False

    def navigate_to_main():
        """切换到主界面，失败时兜底到 /error。"""
        try:
            main_url = _get_main_url()
            if not main_url:
                raise RuntimeError("Flask 端口未初始化")

            # 先确认 Flask 真的在响应
            ok = False
            for _ in range(50):
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        if s.connect_ex(("127.0.0.1", controller._flask_port)) == 0:
                            ok = True
                            break
                except Exception:
                    pass
                time.sleep(0.05)
            if not ok:
                raise RuntimeError("Flask 服务未就绪")

            # 加载主 URL 并调整窗口
            if not _load_url_with_retry(main_url):
                raise RuntimeError("主页面加载失败")

            try:
                window.resize(main_width, main_height)
            except Exception:
                pass

            # 验证页面真的加载了，否则跳转错误页
            if not _verify_page_loaded(timeout=5.0):
                raise RuntimeError("主页面验证失败")

        except Exception as e:
            print(f"[desktop] 切换主界面失败：{e}")
            _show_error_page()

    def _show_error_page():
        """显示错误页（Flask 已启动时使用 /error，否则用本地 error.html）。"""
        try:
            if controller._flask_port:
                error_url = f"http://127.0.0.1:{controller._flask_port}/error"
                _load_url_with_retry(error_url)
            else:
                window.load_url(_resource_path("templates/error.html"))
        except Exception as e:
            print(f"[desktop] 显示错误页失败：{e}")

    def go_home():
        """从错误页返回首页。"""
        main_url = _get_main_url()
        if main_url:
            _load_url_with_retry(main_url)
        else:
            _show_error_page()

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
