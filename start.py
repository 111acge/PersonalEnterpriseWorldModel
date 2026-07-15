#!/usr/bin/env python3
"""一键启动个人企业世界模型的本地桌面界面。

用法：
    python3 start.py

无界面运行管线：
    python3 start.py --pipeline [--no-git] [--reset] [--status]

基于 Flask + pywebview，无需外部浏览器。所有数据保存在项目目录的 data/ 下。
"""
import argparse
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 统一日志配置
sys.path.insert(0, str(ROOT))
from pewm.processors.log_config import setup_logging

setup_logging()


def _show_fatal_error(msg: str):
    """在控制台显示致命错误。"""
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
