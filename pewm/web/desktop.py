"""pywebview 桌面启动器（带启动画面）。

在独立线程中启动 Flask，然后打开一个无边框/原生风格的桌面窗口。
启动画面会先显示，等 Flask 服务就绪后再打开主窗口。
"""
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

import webview


def find_free_port(start=14725):
    """找一个可用端口。"""
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("找不到可用端口")


def _run_flask(app, host, port):
    """在线程中运行 Flask，关闭日志输出。"""
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


def _resource_path(relative_path):
    """获取资源路径，兼容 PyInstaller 单文件模式。"""
    if getattr(sys, "frozen", False):
        # PyInstaller 单文件运行时会解压到临时 _MEIPASS 目录
        base_path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base_path = Path(__file__).resolve().parent
    return str(base_path / relative_path)


def start_desktop_app(title="个人企业世界模型", width=1280, height=800):
    """启动 Flask + pywebview 桌面应用。"""
    # 先显示启动画面（在重依赖导入之前，给用户即时反馈）
    splash = webview.create_window(
        title="PEWM 启动中",
        url=_resource_path("templates/splash.html"),
        width=420,
        height=320,
        frameless=True,
        on_top=True,
        resizable=False,
    )

    # 延迟导入重依赖
    from pewm.web.app import create_app

    port = find_free_port()
    app = create_app()

    flask_thread = threading.Thread(
        target=_run_flask,
        args=(app, "127.0.0.1", port),
        daemon=True,
    )
    flask_thread.start()

    # 等待 Flask 启动
    for _ in range(100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.05)

    url = f"http://127.0.0.1:{port}/"

    # 创建主窗口
    webview.create_window(
        title=title,
        url=url,
        width=width,
        height=height,
        min_size=(900, 600),
        text_select=True,
    )

    # 关闭启动画面，然后启动事件循环
    splash.destroy()
    webview.start(debug=False)


if __name__ == "__main__":
    start_desktop_app()
