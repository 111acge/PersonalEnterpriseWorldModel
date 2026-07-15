"""OCR 配置与调用测试。"""
from unittest.mock import patch

from pewm.processors.ocr_api import load_ocr_config, save_ocr_config


def test_ocr_config_roundtrip(temp_project):
    """OCR 配置应能正确读写。"""
    fake_config = {}
    with patch("pewm.processors.ocr_api.load_config", return_value=fake_config), \
         patch("pewm.processors.ocr_api.save_config") as mock_save:
        save_ocr_config({"mode": "api", "provider": "tencent", "credentials": {"secret_id": "abc"}})
        # save_config 应被调用，且写入了 ocr 字段
        assert mock_save.called
        saved = mock_save.call_args[0][0]
        assert saved["ocr"]["provider"] == "tencent"


def test_ocr_config_default_values(temp_project):
    """默认 OCR 配置为本地模式、百度 provider。"""
    with patch("pewm.processors.ocr_api.load_config", return_value={}):
        cfg = load_ocr_config()
    assert cfg.get("mode") == "local"
    assert cfg.get("provider") == "baidu"
