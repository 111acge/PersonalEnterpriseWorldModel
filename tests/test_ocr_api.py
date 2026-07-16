"""OCR API 配置测试。"""
from pathlib import Path
from unittest.mock import patch

from pewm.processors.ocr_api import load_ocr_config, save_ocr_config, OCR_PROVIDERS


def test_ocr_providers_have_required_fields():
    """OCR 提供商配置应包含必要字段。"""
    for name, cfg in OCR_PROVIDERS.items():
        assert "description" in cfg
        assert "fields" in cfg


def test_ocr_config_roundtrip(temp_project):
    """OCR 配置应能正确读写。"""
    save_ocr_config({"mode": "api", "provider": "tencent", "credentials": {"secret_id": "abc"}})
    cfg = load_ocr_config()
    assert cfg.get("mode") == "api"
    assert cfg.get("provider") == "tencent"
    assert cfg.get("credentials", {}).get("secret_id") == "abc"


def test_ocr_config_default_values(temp_project):
    """默认 OCR 配置为本地模式、百度 provider。"""
    from pewm.paths import CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_file = CONFIG_DIR / "config.json"
    if config_file.exists():
        config_file.unlink()
    with patch("pewm.processors.ocr_api.load_config", return_value={"provider": ""}):
        cfg = load_ocr_config()
    assert cfg.get("mode") == "local"
    assert cfg.get("provider") == "baidu"


def test_ocr_config_saves_partial_update(temp_project):
    """save_ocr_config 应保留原有 config 中的其他字段。"""
    from pewm.processors.llm_client import load_config, save_config
    save_config({"provider": "deepseek", "ocr": {"mode": "api"}})
    save_ocr_config({"mode": "api", "provider": "aliyun", "credentials": {"key": "v"}})
    full = load_config()
    assert full["provider"] == "deepseek"
    assert full["ocr"]["provider"] == "aliyun"


def test_ocr_config_file_missing_uses_defaults(temp_project):
    """当 OCR 配置字段缺失时返回默认配置。"""
    from pewm.paths import CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with patch("pewm.processors.ocr_api.load_config", return_value={"provider": ""}):
        cfg = load_ocr_config()
    assert cfg["mode"] == "local"
    assert cfg["provider"] == "baidu"
    assert cfg["credentials"] == {}
