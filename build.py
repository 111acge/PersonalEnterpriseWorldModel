#!/usr/bin/env python3
"""PyInstaller 打包辅助脚本。

运行后会根据当前平台生成可执行文件：
- Windows: dist/个人企业世界模型.exe
- Linux/macOS: dist/个人企业世界模型

用法：
    python3 build.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def check_pyinstaller():
    if shutil.which("pyinstaller") is None:
        print("[错误] 未找到 pyinstaller。请运行：")
        print("    pip install pyinstaller")
        sys.exit(1)


def main():
    check_pyinstaller()

    print("[info] 开始打包个人企业世界模型...")
    cmd = ["pyinstaller", "build.spec", "--clean", "--noconfirm"]
    result = subprocess.run(cmd, cwd=ROOT)

    if result.returncode == 0:
        print("[info] 打包完成，输出目录：dist/")
        print("[info] 请将 dist/ 下的可执行文件与 data/ 目录放在同一文件夹使用。")
    else:
        print("[error] 打包失败，请查看上方错误信息。")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
