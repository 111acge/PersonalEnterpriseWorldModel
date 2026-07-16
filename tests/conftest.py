"""pytest 共享配置。"""
import shutil
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="function")
def temp_project(tmp_path):
    """为每个测试创建隔离的项目目录。"""
    project_root = Path(__file__).resolve().parents[1]
    temp_root = tmp_path / "pewm_project"
    temp_root.mkdir()

    # 复制必要的目录结构
    for d in ["00-Inbox", "10-Theory", "20-Ontology", "30-Instances", "40-Skills", "90-Meta", "pewm", "bge-model"]:
        src = project_root / d
        if src.exists():
            shutil.copytree(src, temp_root / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # 把 pewm 加入路径
    sys.path.insert(0, str(temp_root))
    sys.path.insert(0, str(temp_root.parent))

    # 计算隔离后的路径
    import pewm.paths as paths
    data_dir = temp_root / "data"
    db_path = data_dir / "world-model.db"
    inbox_dir = temp_root / "00-Inbox"
    media_dir = inbox_dir / "_media"
    config_dir = temp_root / "pewm" / "config"
    schemas_dir = config_dir / "schemas"
    vector_dir = data_dir / "vector"
    vector_db = vector_dir / "vectors.db"
    model_cache_dir = vector_dir / "embedding_model"

    # 修改路径模块根变量
    paths.ROOT = temp_root
    paths.DATA_DIR = data_dir
    paths.DB_PATH = db_path
    paths.INBOX_DIR = inbox_dir
    paths.MEDIA_DIR = media_dir
    paths.CONFIG_DIR = config_dir
    paths.SCHEMAS_DIR = schemas_dir

    # 关闭旧连接
    from pewm.processors.database import close_connection
    from pewm.processors.metrics import close_connection as close_metrics_connection
    from pewm.processors.vector_db import _close_db
    close_connection()
    close_metrics_connection()
    _close_db()

    # 补丁各模块中通过 from-import 引入的模块级路径名
    import pewm.processors.database as db_mod
    import pewm.processors.metrics as metrics_mod
    import pewm.processors.ocr as ocr_mod
    import pewm.processors.ocr_api as ocr_api_mod
    import pewm.processors.vector_db as vdb_mod
    import pewm.processors.vectorizer as vzer_mod
    import pewm.web.app as app_mod
    import pewm.processors.__main__ as main_mod

    old_metrics_db = metrics_mod.paths.DB_PATH
    old_ocr_media = ocr_mod.MEDIA_DIR
    old_ocr_inbox = ocr_mod.INBOX_DIR
    old_ocr_root = ocr_mod.ROOT
    old_ocr_api_config_dir = ocr_api_mod.CONFIG_DIR
    old_vdb_vector_dir = vdb_mod._vector_dir
    old_vdb_db_file = vdb_mod._db_file
    old_vdb_model_cache_dir = vdb_mod._model_cache_dir
    old_vzer_vector_db = vzer_mod.VectorDB
    old_app_root = app_mod.ROOT
    old_main_root = main_mod.ROOT
    old_main_inbox = main_mod.INBOX_DIR

    metrics_mod.paths.DB_PATH = db_path
    ocr_mod.MEDIA_DIR = media_dir
    ocr_mod.INBOX_DIR = inbox_dir
    ocr_mod.ROOT = temp_root
    ocr_api_mod.CONFIG_DIR = config_dir
    main_mod.ROOT = temp_root
    main_mod.INBOX_DIR = inbox_dir
    # 用 lambda 返回动态路径，确保新创建 VectorDB 时打开的是隔离文件
    vdb_mod._vector_dir = lambda: vector_dir
    vdb_mod._db_file = lambda: vector_db
    vdb_mod._model_cache_dir = lambda: model_cache_dir
    app_mod.ROOT = temp_root

    yield temp_root

    # 清理与恢复
    close_connection()
    close_metrics_connection()
    _close_db()

    metrics_mod.paths.DB_PATH = old_metrics_db
    ocr_mod.MEDIA_DIR = old_ocr_media
    ocr_mod.INBOX_DIR = old_ocr_inbox
    ocr_mod.ROOT = old_ocr_root
    ocr_api_mod.CONFIG_DIR = old_ocr_api_config_dir
    main_mod.ROOT = old_main_root
    main_mod.INBOX_DIR = old_main_inbox
    vdb_mod._vector_dir = old_vdb_vector_dir
    vdb_mod._db_file = old_vdb_db_file
    vdb_mod._model_cache_dir = old_vdb_model_cache_dir
    vzer_mod.VectorDB = old_vzer_vector_db
    app_mod.ROOT = old_app_root

    shutil.rmtree(temp_root, ignore_errors=True)
