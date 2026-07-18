"""配置导入导出测试。"""
import json
from pathlib import Path
from unittest.mock import patch

from pewm.processors.config_manager import (
    backup_to_dir,
    export_all,
    import_from,
    restore_from_dir,
)


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


def test_export_all_defaults_to_masked(temp_project):
    """#60：导出默认不含 API Key，且带 contains_api_keys 元字段。"""
    from pewm.processors.llm_client import save_config
    save_config({
        "provider": "deepseek",
        "api_key": "secret",
        "ocr": {"mode": "api", "provider": "baidu",
                "credentials": {"api_key": "OCRKEY", "secret_key": "OCRSECRET"}},
    })
    dest = temp_project / "export.json"
    ok, msg = export_all(dest)
    assert ok is True
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["contains_api_keys"] is False
    assert "secret" not in payload["llm"].values()
    assert "api_key" not in payload["llm"]
    assert "OCRKEY" not in dest.read_text(encoding="utf-8")
    assert "OCRSECRET" not in dest.read_text(encoding="utf-8")
    assert payload["ocr"]["credentials"]["api_key"] == "***"


def test_import_masked_credentials_keep_existing(temp_project):
    """#24：导入文件中 '***' 占位符不覆盖本地真实 OCR 密钥。"""
    from pewm.processors.llm_client import load_config, save_config
    save_config({
        "provider": "deepseek",
        "ocr": {"mode": "api", "provider": "baidu",
                "credentials": {"api_key": "REALKEY", "secret_key": "REALSECRET"}},
    })
    src = temp_project / "import.json"
    src.write_text(json.dumps({
        "version": 1,
        "app": "个人企业世界模型",
        "contains_api_keys": False,
        "llm": {"provider": "kimi"},
        "ocr": {"mode": "api", "provider": "tencent",
                "credentials": {"api_key": "***", "secret_key": "***"}},
        "profile": {},
        "prompt": {},
    }, ensure_ascii=False), encoding="utf-8")
    ok, msg = import_from(src, overwrite=True)
    assert ok is True
    cfg = load_config()
    assert cfg["provider"] == "kimi"
    assert cfg["ocr"]["credentials"]["api_key"] == "REALKEY"
    assert cfg["ocr"]["credentials"]["secret_key"] == "REALSECRET"


def test_import_invalid_structure_does_not_write(temp_project):
    """#70：四段结构校验失败时整体拒绝导入，不落盘。"""
    from pewm.processors.llm_client import load_config, save_config
    save_config({"provider": "deepseek", "api_key": "keepme"})
    src = temp_project / "import_bad.json"
    src.write_text(json.dumps({
        "version": 1,
        "app": "个人企业世界模型",
        "llm": {"provider": "kimi"},
        "ocr": {"mode": "api", "credentials": "not-a-dict"},
        "profile": {},
        "prompt": {},
    }, ensure_ascii=False), encoding="utf-8")
    ok, msg = import_from(src, overwrite=True)
    assert ok is False
    cfg = load_config()
    assert cfg["provider"] == "deepseek"
    assert cfg["api_key"] == "keepme"


def test_restore_from_dir_missing_config_fails(temp_project):
    """#71：备份目录缺少 config.json 时拒绝还原。"""
    empty_backup = temp_project / "empty-backup"
    empty_backup.mkdir()
    result = restore_from_dir(empty_backup)
    assert result["success"] is False


def test_restore_from_dir_restores_and_snapshots(temp_project):
    """#71：还原成功且还原前自动做快照。"""
    import pewm.processors.config_manager as cm

    fake_config_dir = temp_project / "fake-config"
    fake_config_dir.mkdir()
    (fake_config_dir / "config.json").write_text(
        json.dumps({"provider": "old"}), encoding="utf-8")

    backup_dir = temp_project / "backup"
    backup_dir.mkdir()
    (backup_dir / "config.json").write_text(
        json.dumps({"provider": "restored"}), encoding="utf-8")

    with patch.object(cm, "CONFIG_DIR", fake_config_dir):
        result = restore_from_dir(backup_dir)
    assert result["success"] is True
    assert json.loads((fake_config_dir / "config.json").read_text(
        encoding="utf-8"))["provider"] == "restored"
    # 快照目录已生成
    snapshots = list(temp_project.glob("config-backup-*"))
    assert len(snapshots) == 1
    assert json.loads((snapshots[0] / "config.json").read_text(
        encoding="utf-8"))["provider"] == "old"
