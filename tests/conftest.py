"""pytest 共享配置。"""
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="function")
def temp_project(tmp_path):
    """为每个测试创建隔离的项目目录。"""
    project_root = Path(__file__).resolve().parents[1]
    temp_root = tmp_path / "pewm_project"
    temp_root.mkdir()

    # 复制必要的目录结构
    for d in ["00-Inbox", "10-Theory", "20-Ontology", "30-Instances", "40-Skills", "90-Meta", "pewm"]:
        src = project_root / d
        if src.exists():
            shutil.copytree(src, temp_root / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # 把 pewm 加入路径
    sys.path.insert(0, str(temp_root))
    sys.path.insert(0, str(temp_root.parent))

    # 修改 ROOT 指向临时目录
    import pewm.paths
    pewm.paths.ROOT = temp_root
    pewm.paths.DATA_DIR = temp_root / "data"
    pewm.paths.DB_PATH = pewm.paths.DATA_DIR / "world-model.db"
    pewm.paths.INBOX_DIR = temp_root / "00-Inbox"
    pewm.paths.CONFIG_DIR = temp_root / "pewm" / "config"
    pewm.paths.SCHEMAS_DIR = pewm.paths.CONFIG_DIR / "schemas"

    # 关闭可能存在的旧线程连接，确保使用新的 DB_PATH
    from pewm.processors.database import close_connection
    close_connection()

    yield temp_root

    # 清理
    close_connection()
    shutil.rmtree(temp_root, ignore_errors=True)
