""" centralized path resolution for PEWM. """
import sys
from pathlib import Path


def resolve_root() -> Path:
    """Return the project root directory.

    Works both in source mode and PyInstaller onefile mode.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _resolve_resource_root() -> Path:
    """只读资源（打包进程序的代码/配置）所在根目录。

    PyInstaller 单文件模式下，datas 会释放到临时目录 sys._MEIPASS，
    而 ROOT 指向 exe 所在目录（用于可写数据）。两者需要区分。
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


ROOT = resolve_root()
RESOURCE_ROOT = _resolve_resource_root()
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "world-model.db"
INBOX_DIR = ROOT / "00-Inbox"
MEDIA_DIR = INBOX_DIR / "_media"
CONFIG_DIR = RESOURCE_ROOT / "pewm" / "config"
SCHEMAS_DIR = CONFIG_DIR / "schemas"
PIPELINE_DIR = RESOURCE_ROOT / "pewm" / "processors"
QUERY_DIR = RESOURCE_ROOT / "90-Meta" / "query"
