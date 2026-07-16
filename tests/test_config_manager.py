"""配置导入导出测试。"""
from pathlib import Path
from unittest.mock import patch

from pewm.processors.config_manager import backup_to_dir, export_all, import_from


def test_export_all_creates_json(temp_project):
    from pewm.processors.llm_client import save_config
    save_config({"provider": "deepseek", "api_key": "secret"})
    dest = temp_project / "export.json"
    ok, msg = export_all(dest, include_api_keys=False)
    assert ok is True
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert "deepseek" in content


def test_export_all_omits_api_key_when_requested(temp_project):
    from pewm.processors.llm_client import save_config
    save_config({"provider": "deepseek", "api_key": "secret"})
    dest = temp_project / "export.json"
    ok, msg = export_all(dest, include_api_keys=False)
    assert ok is True
    content = dest.read_text(encoding="utf-8")
    assert "secret" not in content


def test_import_from_valid_json(temp_project):
    from pewm.processors.llm_client import save_config
    save_config({"provider": "deepseek"})
    dest = temp_project / "export.json"
    export_all(dest, include_api_keys=True)
    ok, msg = import_from(dest, overwrite=True)
    assert ok is True


def test_backup_to_dir_creates_snapshot(temp_project):
    from pewm.paths import CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / "sample.txt").write_text("sample", encoding="utf-8")
    ok, msg = backup_to_dir(temp_project)
    assert ok is True
    assert "config-backup" in msg


def test_import_from_invalid_json_returns_false(temp_project):
    dest = temp_project / "bad.json"
    dest.write_text("not json", encoding="utf-8")
    ok, msg = import_from(dest, overwrite=True)
    assert ok is False
