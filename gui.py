#!/usr/bin/env python3
"""兼容 shim：旧入口保留，实际逻辑已迁移到 pewm.gui。"""
from pewm.gui import main

if __name__ == "__main__":
    main()
