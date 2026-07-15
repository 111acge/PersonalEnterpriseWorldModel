"""pywebview 桌面启动器（带完整启动画面）。

使用单窗口策略：
1. 窗口先加载 splash.html（本地文件，无需等待 Flask）
2. splash.html 通过 pywebview.js_api 轮询 SplashController 获取进度
3. 所有初始化（配置、数据库、模型、Flask）在 SplashController 后台线程执行
4. 初始化完成后，前端淡出启动界面，通知后端 navigate_to_main
5. 后端将窗口 load_url 切换到 Flask 主界面
"""
import sys
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
    """从版本文件或 git tag 读取版本号。"""
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip()
    return "1.0.0"


def start_desktop_app(title="个人企业世界模型", width=1280, height=800):
    """启动 Flask + pywebview 桌面应用。"""
    controller = SplashController(version=get_version(), timeout=15.0)

    def navigate_to_main():
        """前端淡出动画结束后调用，切换窗口到主界面。"""
        try:
            main_url = f"http://127.0.0.1:{controller._flask_port}/"
            window.load_url(main_url)
            # 窗口从启动画面尺寸平滑展开到主界面尺寸
            try:
                window.resize(width, height)
            except Exception:
                pass
            try:
                window.set_title(title)
            except Exception:
                pass
        except Exception as e:
            controller._set_error(f"切换主界面失败：{e}", can_retry=False)

    controller._do_navigate = navigate_to_main

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

    # 初始化完成后自动切换
    controller.on_complete(lambda port: None)

    # 在 webview 事件循环启动后再开始加载
    def on_shown():
        controller.start()

    window.events.shown += on_shown

    webview.start(debug=False)


if __name__ == "__main__":
    start_desktop_app()
