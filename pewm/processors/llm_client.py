"""LLM 客户端统一封装，支持 DeepSeek / Kimi / MiniMax。

所有厂商都走 OpenAI 兼容的 Chat Completions 接口，只是 base_url 和默认模型不同。
"""
import os
import json
from pathlib import Path
from typing import Dict, List, Optional

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# 配置文件路径（保存 API Key 和模型选择）
CONFIG_DIR = Path.home() / ".enterprise_world_model"
CONFIG_FILE = CONFIG_DIR / "config.json"

# 预置的 LLM 提供商配置
PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
    },
    "kimi": {
        "name": "Kimi (Moonshot)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "default_model": "moonshot-v1-8k",
    },
    "minimax": {
        "name": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "models": ["abab5.5-chat", "abab6-chat", "abab6.5s-chat"],
        "default_model": "abab6.5s-chat",
    },
}


def load_config() -> Dict:
    """加载持久化配置（API Key、选定的提供商和模型）。"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"provider": "", "api_key": "", "model": "", "base_url": ""}


def save_config(cfg: Dict) -> None:
    """保存配置到用户目录。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_client(provider: str = None, api_key: str = None, base_url: str = None):
    """根据提供商/Key 创建 OpenAI 兼容客户端。

    如果不传参数，从持久化配置中读取。
    """
    if OpenAI is None:
        raise RuntimeError("未安装 openai 库，请执行 pip install openai")

    cfg = load_config()
    provider = provider or cfg.get("provider")
    api_key = api_key or cfg.get("api_key") or os.environ.get("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError("未配置 API Key，请在界面「API 配置」中填写")

    # 优先使用传入的 base_url，其次用配置，最后根据 provider 推断
    if not base_url:
        base_url = cfg.get("base_url") or ""
    if not base_url and provider and provider in PROVIDERS:
        base_url = PROVIDERS[provider]["base_url"]
    if not base_url:
        raise RuntimeError("无法确定 API base_url，请检查提供商或手动指定")

    return OpenAI(api_key=api_key, base_url=base_url)


def get_model(provider: str = None) -> str:
    """获取当前选定的模型名。"""
    cfg = load_config()
    provider = provider or cfg.get("provider")
    if cfg.get("model"):
        return cfg["model"]
    if provider and provider in PROVIDERS:
        return PROVIDERS[provider]["default_model"]
    return "gpt-3.5-turbo"


def chat_completion(
    messages: List[Dict],
    temperature: float = 0.3,
    max_tokens: int = 1024,
    provider: str = None,
    api_key: str = None,
    model: str = None,
) -> str:
    """调用 LLM 完成对话，返回回答文本。"""
    client = get_client(provider=provider, api_key=api_key)
    model_name = model or get_model(provider)

    resp = client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def chat_completion_stream(
    messages: List[Dict],
    temperature: float = 0.3,
    max_tokens: int = 1024,
    provider: str = None,
    api_key: str = None,
    model: str = None,
):
    """调用 LLM 完成对话，以生成器形式逐段返回文本增量。

    Yields: str 文本片段
    """
    client = get_client(provider=provider, api_key=api_key)
    model_name = model or get_model(provider)

    resp = client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    for chunk in resp:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            yield delta


def test_api(provider: str, api_key: str, base_url: str = None) -> str:
    """测试 API 连通性，返回模型回答。"""
    try:
        client = get_client(provider=provider, api_key=api_key, base_url=base_url)
        provider_cfg = PROVIDERS.get(provider, {})
        model = provider_cfg.get("default_model", "gpt-3.5-turbo")
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "你好，请用一句话介绍自己"}],
            temperature=0.5,
            max_tokens=64,
        )
        return "OK: " + (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"ERROR: {e}"
