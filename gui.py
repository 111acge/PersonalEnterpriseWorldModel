"""GUI 入口兼容 shim。"""
import sys
from pathlib import Path

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pewm.processors.crash_handler import install_crash_handler
from pewm.web.desktop import start_desktop_app

install_crash_handler()


def main():
    start_desktop_app()


if __name__ == "__main__":
    main()
