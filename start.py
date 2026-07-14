#!/usr/bin/env python3
"""一键启动个人企业世界模型的本地桌面界面。

用法：
    python3 start.py

无界面运行管线：
    python3 start.py --pipeline [--no-git] [--reset] [--status]

无需服务器、无需浏览器、无需网络。所有数据保存在项目目录的 data/ 下。
"""
import argparse
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def check_tkinter():
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False


def _show_fatal_error(msg: str):
    """在控制台或 Tk 弹窗里显示致命错误（用于 console=False 的 exe 场景）。"""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("启动失败", msg)
        root.destroy()
    except Exception:
        sys.stderr.write(msg + "\n")


def main():
    parser = argparse.ArgumentParser(description="个人企业世界模型启动器")
    parser.add_argument("--pipeline", action="store_true", help="无界面运行 AI 管线")
    args, extra = parser.parse_known_args()

    if args.pipeline:
        # 无界面运行管线，透传额外参数
        cmd = [sys.executable, str(ROOT / "run.py")] + extra
        subprocess.run(cmd, cwd=ROOT)
        return

    if not check_tkinter():
        msg = ("[错误] 当前 Python 未安装 tkinter 支持。\n"
               "       请安装包含 tkinter 的 Python，或使用打包好的可执行文件。")
        _show_fatal_error(msg)
        sys.exit(1)

    print("=" * 45)
    print("  个人企业世界模型 · 本地桌面启动器")
    print("=" * 45)
    print("正在打开图形界面...")

    try:
        sys.path.insert(0, str(ROOT))
        import gui
        gui.main()
    except SystemExit:
        raise
    except Exception:
        msg = "启动图形界面时发生错误：\n\n" + traceback.format_exc()
        _show_fatal_error(msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
