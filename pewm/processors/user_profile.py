"""用户身份信息模块：个人身份 + 公司信息。

数据持久化在 ~/.enterprise_world_model/profile.json，跨机器/跨 exe 保留。
"""
import json
from pathlib import Path
from typing import Dict

from pewm.processors.llm_client import CONFIG_DIR, load_config, save_config  # 复用同一个配置目录


PROFILE_FILE = CONFIG_DIR / "profile.json"

# 默认字段结构（用户可在 GUI 中自定义）
DEFAULT_PROFILE = {
    # 个人信息
    "personal_name": "",           # 姓名
    "personal_role": "",           # 职位/角色
    "personal_dept": "",           # 部门
    "personal_email": "",          # 邮箱
    "personal_phone": "",          # 电话
    "personal_bio": "",            # 个人简介（一句话，会被注入到 AI 提示词中）

    # 公司信息
    "company_name": "",            # 公司名称
    "company_industry": "",        # 行业
    "company_scale": "",           # 规模
    "company_products": "",        # 主要产品/服务
    "company_bio": "",             # 公司简介
    "company_address": "",         # 地址
    "company_website": "",         # 网址

    # 偏好
    "language": "中文",             # 首选语言
    "extra_context": "",           # 额外的 AI 上下文（用户自己补充的背景信息）
}


def load_profile() -> Dict:
    """加载用户身份，缺失字段用默认值补齐。"""
    profile = DEFAULT_PROFILE.copy()
    if PROFILE_FILE.exists():
        try:
            data = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                profile.update(data)
        except Exception:
            pass
    return profile


def save_profile(profile: Dict) -> None:
    """保存用户身份。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_FILE.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def profile_to_context(profile: Dict = None) -> str:
    """把用户身份格式化为一段可注入到 AI 提示词里的上下文文本。

    空字段会被自动过滤，只输出有内容的部分。
    """
    if profile is None:
        profile = load_profile()
    parts = []

    # 个人部分
    personal_fields = [
        ("personal_name", "用户姓名"),
        ("personal_role", "职位"),
        ("personal_dept", "部门"),
        ("personal_email", "邮箱"),
        ("personal_phone", "电话"),
        ("personal_bio", "个人简介"),
    ]
    personal_lines = []
    for key, label in personal_fields:
        v = (profile.get(key) or "").strip()
        if v:
            personal_lines.append(f"- {label}：{v}")
    if personal_lines:
        parts.append("## 用户身份\n" + "\n".join(personal_lines))

    # 公司部分
    company_fields = [
        ("company_name", "公司"),
        ("company_industry", "行业"),
        ("company_scale", "规模"),
        ("company_products", "主要产品/服务"),
        ("company_bio", "公司简介"),
        ("company_address", "地址"),
        ("company_website", "网址"),
    ]
    company_lines = []
    for key, label in company_fields:
        v = (profile.get(key) or "").strip()
        if v:
            company_lines.append(f"- {label}：{v}")
    if company_lines:
        parts.append("## 公司信息\n" + "\n".join(company_lines))

    # 额外上下文
    extra = (profile.get("extra_context") or "").strip()
    if extra:
        parts.append("## 用户补充背景\n" + extra)

    return "\n\n".join(parts)
