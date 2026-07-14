"""AI 提示词配置模块：允许用户自定义 RAG 系统提示词。

数据持久化在 ~/.enterprise_world_model/prompt.json。
"""
import json
from pathlib import Path
from typing import Dict

try:
    from .llm_client import CONFIG_DIR
except ImportError:
    from llm_client import CONFIG_DIR


PROMPT_FILE = CONFIG_DIR / "prompt.json"

# 默认 RAG 系统提示词（可编辑）
DEFAULT_SYSTEM_PROMPT = """你是一个本地企业知识库的问答助手。你的任务是基于检索到的企业知识片段，准确、简洁地回答用户的问题。

{{USER_CONTEXT}}

回答规则：
1. 优先使用提供的上下文回答问题，不要编造信息
2. 如果上下文中没有答案，明确告诉用户"知识库中未找到相关信息"
3. 如果涉及具体数值、流程、责任人，必须引用原文
4. 回答末尾列出引用的来源文件路径
5. 使用中文回答
"""

# 默认检索无结果时的回复模板
DEFAULT_NO_RESULT_REPLY = "未检索到相关知识。请先在 00-Inbox 投递内容并运行管线，或在「API 配置」中填写 LLM API Key 启用生成式问答。"

# 字段定义（用于 GUI 渲染）
PROMPT_FIELDS = [
    {
        "key": "system_prompt",
        "label": "RAG 系统提示词",
        "description": "AI 在回答问题时遵循的全局指令。{{USER_CONTEXT}} 会被自动替换为用户身份信息。",
        "default": DEFAULT_SYSTEM_PROMPT,
        "rows": 12,
    },
    {
        "key": "no_result_reply",
        "label": "无检索结果时的回复",
        "description": "当知识库为空或查询无结果时显示的文本。",
        "default": DEFAULT_NO_RESULT_REPLY,
        "rows": 3,
    },
    {
        "key": "greeting",
        "label": "欢迎语",
        "description": "启动时显示在对话窗口的问候文本。",
        "default": "你好！我是你的本地知识助手。\n\n先在「Inbox 速记」里写几条见闻，再到「管线」页面点击「运行 AI 管线」，然后在这里提问。",
        "rows": 3,
    },
]


def load_prompt() -> Dict:
    """加载提示词配置，缺失字段用默认值补齐。"""
    result = {f["key"]: f["default"] for f in PROMPT_FIELDS}
    if PROMPT_FILE.exists():
        try:
            data = json.loads(PROMPT_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k in result:
                    if k in data and isinstance(data[k], str):
                        result[k] = data[k]
        except Exception:
            pass
    return result


def save_prompt(prompt: Dict) -> None:
    """保存提示词配置。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # 只保存已知字段
    filtered = {f["key"]: prompt.get(f["key"], f["default"]) for f in PROMPT_FIELDS}
    PROMPT_FILE.write_text(
        json.dumps(filtered, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def render_system_prompt(user_context: str = "") -> str:
    """渲染最终的系统提示词（注入用户上下文）。"""
    cfg = load_prompt()
    template = cfg.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
    return template.replace("{{USER_CONTEXT}}", user_context.strip())


def get_greeting() -> str:
    """获取启动时的欢迎语。"""
    cfg = load_prompt()
    return cfg.get("greeting", PROMPT_FIELDS[2]["default"])


def get_no_result_reply() -> str:
    """获取无结果时的回复模板。"""
    cfg = load_prompt()
    return cfg.get("no_result_reply", PROMPT_FIELDS[1]["default"])
