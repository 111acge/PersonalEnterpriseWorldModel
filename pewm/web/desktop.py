"""pywebview 桌面启动器。

在独立线程中启动 Flask，然后打开一个无边框/原生风格的桌面窗口。
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


def start_desktop_app(title="个人企业世界模型", width=1280, height=800):
    """启动 Flask + pywebview 桌面应用。"""
    # 延迟导入，避免在 PyInstaller 分析阶段触发 heavy import
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
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)

    url = f"http://127.0.0.1:{port}/"

    # 在 pywebview 窗口中加载本地页面
    webview.create_window(
        title=title,
        url=url,
        width=width,
        height=height,
        min_size=(900, 600),
        text_select=True,
    )
    webview.start(debug=False)


if __name__ == "__main__":
    start_desktop_app()
