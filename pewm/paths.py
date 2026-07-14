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


ROOT = resolve_root()
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "world-model.db"
INBOX_DIR = ROOT / "00-Inbox"
MEDIA_DIR = INBOX_DIR / "_media"
CONFIG_DIR = ROOT / "pewm" / "config"
SCHEMAS_DIR = CONFIG_DIR / "schemas"
PIPELINE_DIR = ROOT / "pewm" / "processors"
QUERY_DIR = ROOT / "90-Meta" / "query"
