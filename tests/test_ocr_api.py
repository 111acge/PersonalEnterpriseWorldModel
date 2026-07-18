"""OCR API 配置测试。"""
import json
from pathlib import Path
from unittest.mock import patch

import pewm.processors.ocr_api as ocr_api
from pewm.processors.ocr_api import load_ocr_config, save_ocr_config, OCR_PROVIDERS


class _FakeHTTPResponse:
    """模拟 urlopen 返回的上下文管理器响应。"""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


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


def test_baidu_token_cached_per_credentials(temp_project):
    """#68：同一 (api_key, secret_key) 的 access_token 在过期前复用。"""
    ocr_api._BAIDU_TOKEN_CACHE.clear()
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _FakeHTTPResponse({"access_token": "TOKEN123", "expires_in": 2592000})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        t1 = ocr_api._baidu_get_token("ak", "sk")
        t2 = ocr_api._baidu_get_token("ak", "sk")
    assert t1 == t2 == "TOKEN123"
    assert len(calls) == 1  # 第二次命中缓存，不再请求

    # 不同凭证独立缓存
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ocr_api._baidu_get_token("ak2", "sk2")
    assert len(calls) == 2
    ocr_api._BAIDU_TOKEN_CACHE.clear()


def test_baidu_token_refreshed_when_expired(temp_project):
    """#68：缓存过期后重新获取 token。"""
    import time
    ocr_api._BAIDU_TOKEN_CACHE.clear()
    # 预置一个已过期的缓存
    ocr_api._BAIDU_TOKEN_CACHE[("ak", "sk")] = ("OLD", time.time() - 1)

    def fake_urlopen(req, timeout=None):
        return _FakeHTTPResponse({"access_token": "NEW", "expires_in": 2592000})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        token = ocr_api._baidu_get_token("ak", "sk")
    assert token == "NEW"
    ocr_api._BAIDU_TOKEN_CACHE.clear()


def test_test_ocr_api_cleans_up_sample_image(temp_project):
    """#69：连通性测试后不在配置目录遗留 _ocr_test.png。"""
    sample = ocr_api.CONFIG_DIR / "_ocr_test.png"
    if sample.exists():
        sample.unlink()
    with patch("pewm.processors.ocr_api.ocr_by_api", return_value=[]):
        result = ocr_api.test_ocr_api("baidu", {"api_key": "k", "secret_key": "s"})
    assert result.startswith("OK")
    assert not sample.exists()


def test_test_ocr_api_cleans_up_on_error(temp_project):
    """#69：测试失败时同样清理 _ocr_test.png。"""
    sample = ocr_api.CONFIG_DIR / "_ocr_test.png"
    if sample.exists():
        sample.unlink()
    with patch("pewm.processors.ocr_api.ocr_by_api",
               side_effect=RuntimeError("boom")):
        result = ocr_api.test_ocr_api("baidu", {"api_key": "k", "secret_key": "s"})
    assert result.startswith("ERROR")
    assert not sample.exists()
