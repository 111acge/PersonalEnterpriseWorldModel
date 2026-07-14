#!/usr/bin/env python3
"""AI 管线运行入口。

因为 .pipeline 目录以点开头，无法直接作为 Python 模块名使用，
所以提供本入口脚本转发到 .pipeline/processors/__main__.py。
"""
import sys
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / ".pipeline" / "processors"))
sys.path.insert(0, str(ROOT))

# 让 __main__.py 以为自己是被直接执行的，并保留命令行参数
entry = ROOT / ".pipeline" / "processors" / "__main__.py"
sys.argv[0] = str(entry)
runpy.run_path(str(entry), run_name="__main__")
