"""通用工具函数。"""
import re
from pathlib import Path
from typing import List

import pewm.paths as paths
from pewm.processors.database import is_inbox_processed


def list_inbox_files() -> List[Path]:
    """列出 00-Inbox 中所有 Markdown/TXT 文件（不含 _media）。"""
    files = []
    for pattern in ("*.md", "*.txt"):
        for p in paths.INBOX_DIR.rglob(pattern):
            if "_media" in p.parts:
                continue
            files.append(p)
    return sorted(files)


def is_unprocessed(path: Path) -> bool:
    """检查文件是否尚未处理或已被修改。"""
    rel = str(path.relative_to(paths.ROOT))
    mtime = str(path.stat().st_mtime)
    return not is_inbox_processed(rel, mtime)


def now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


_WINDOWS_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

_MAX_FILENAME_LEN = 60


def sanitize_filename(name: str) -> str:
    """生成安全的文件名：过滤非法字符、规避 Windows 保留名、限制长度并保证非空。"""
    name = re.sub(r"[^\w\u4e00-\u9fff-]", "", str(name))
    name = name.strip("-")
    if len(name) > _MAX_FILENAME_LEN:
        name = name[:_MAX_FILENAME_LEN].rstrip("-")
    if name.upper() in _WINDOWS_RESERVED_NAMES:
        name += "_"
    return name or "untitled"


def read_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def load_yaml(path: Path):
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
