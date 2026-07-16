"""LLM 客户端测试（mock OpenAI 调用）。"""
from unittest.mock import MagicMock, patch

import pytest

from pewm.processors import llm_client


class TestLoadSaveConfig:
    def test_load_config_missing_file_returns_defaults(self, tmp_path):
        with patch.object(llm_client, "CONFIG_FILE", tmp_path / "nope.json"):
            cfg = llm_client.load_config()
        assert cfg["provider"] == ""
        assert cfg["api_key"] == ""

    def test_save_and_load_config_roundtrip(self, tmp_path):
        cfg_file = tmp_path / "conf" / "config.json"
        with patch.object(llm_client, "CONFIG_DIR", cfg_file.parent), \
             patch.object(llm_client, "CONFIG_FILE", cfg_file):
            llm_client.save_config({"provider": "deepseek", "api_key": "sk-x", "model": "", "base_url": ""})
            loaded = llm_client.load_config()
        assert loaded["provider"] == "deepseek"
        assert loaded["api_key"] == "sk-x"

    def test_load_config_corrupt_json_returns_defaults(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{invalid", encoding="utf-8")
        with patch.object(llm_client, "CONFIG_FILE", cfg_file):
            cfg = llm_client.load_config()
        assert cfg["provider"] == ""


class TestGetClient:
    def test_missing_openai_raises(self):
        with patch.object(llm_client, "OpenAI", None):
            with pytest.raises(RuntimeError, match="openai"):
                llm_client.get_client(api_key="k", base_url="http://x")

    def test_missing_api_key_raises(self, tmp_path):
        with patch.object(llm_client, "CONFIG_FILE", tmp_path / "nope.json"), \
             patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError, match="API Key"):
                llm_client.get_client()

    def test_missing_base_url_raises(self, tmp_path):
        with patch.object(llm_client, "CONFIG_FILE", tmp_path / "nope.json"), \
             patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError, match="base_url"):
                llm_client.get_client(api_key="sk-x", provider="unknown")

    def test_client_created_with_provider(self, tmp_path):
        fake_openai = MagicMock()
        with patch.object(llm_client, "OpenAI", fake_openai), \
             patch.object(llm_client, "CONFIG_FILE", tmp_path / "nope.json"):
            llm_client.get_client(provider="deepseek", api_key="sk-x")
        fake_openai.assert_called_once_with(
            api_key="sk-x", base_url="https://api.deepseek.com"
        )


class TestGetModel:
    def test_default_model_from_provider(self, tmp_path):
        with patch.object(llm_client, "CONFIG_FILE", tmp_path / "nope.json"):
            assert llm_client.get_model("deepseek") == "deepseek-chat"
            assert llm_client.get_model("kimi") == "moonshot-v1-8k"

    def test_explicit_model_from_config(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text('{"provider":"deepseek","model":"custom-m"}', encoding="utf-8")
        with patch.object(llm_client, "CONFIG_FILE", cfg_file):
            assert llm_client.get_model() == "custom-m"

    def test_fallback_default(self, tmp_path):
        with patch.object(llm_client, "CONFIG_FILE", tmp_path / "nope.json"):
            assert llm_client.get_model("unknown") == "gpt-3.5-turbo"


class TestChatCompletion:
    def _fake_client(self, content="你好"):
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = content
        client.chat.completions.create.return_value.choices = [choice]
        return client

    def test_chat_completion_returns_text(self, tmp_path):
        client = self._fake_client("回答内容")
        with patch.object(llm_client, "get_client", return_value=client), \
             patch.object(llm_client, "get_model", return_value="m"):
            out = llm_client.chat_completion(messages=[{"role": "user", "content": "hi"}])
        assert out == "回答内容"

    def test_chat_completion_stream_yields_deltas(self, tmp_path):
        client = MagicMock()
        chunks = []
        for text in ["你", "好", ""]:
            c = MagicMock()
            c.choices = [MagicMock()]
            c.choices[0].delta.content = text
            chunks.append(c)
        client.chat.completions.create.return_value = iter(chunks)
        with patch.object(llm_client, "get_client", return_value=client), \
             patch.object(llm_client, "get_model", return_value="m"):
            deltas = list(llm_client.chat_completion_stream(messages=[]))
        assert deltas == ["你", "好"]


class TestTestApi:
    def test_success(self, tmp_path):
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = "我是 AI"
        client.chat.completions.create.return_value.choices = [choice]
        with patch.object(llm_client, "get_client", return_value=client):
            result = llm_client.test_api("deepseek", "sk-x")
        assert result.startswith("OK:")

    def test_failure_returns_error(self, tmp_path):
        with patch.object(llm_client, "get_client", side_effect=RuntimeError("boom")):
            result = llm_client.test_api("deepseek", "sk-x")
        assert result.startswith("ERROR:")


class TestProviders:
    def test_providers_have_required_keys(self):
        for key, cfg in llm_client.PROVIDERS.items():
            assert "name" in cfg
            assert "base_url" in cfg
            assert "models" in cfg
            assert "default_model" in cfg
            assert cfg["default_model"] in cfg["models"]
