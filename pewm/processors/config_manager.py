"""配置导出/导入模块：把 API Key、用户信息、AI 提示词统一打包成一个 JSON 文件。

导出文件结构：
{
    "version": 1,
    "exported_at": "2026-07-13T10:00:00",
    "app": "个人企业世界模型",
    "llm": {...},         # config.json 的 LLM 段（不含 OCR 段）
    "ocr": {...},         # config.json 的 OCR 段
    "profile": {...},     # profile.json
    "prompt": {...},      # prompt.json
}
"""
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from pewm.processors.llm_client import CONFIG_DIR, load_config, save_config
from pewm.processors.user_profile import load_profile, save_profile, PROFILE_FILE
from pewm.processors.prompt_config import load_prompt, save_prompt, PROMPT_FILE


EXPORT_VERSION = 1


def export_all(dest_path: Path, include_api_keys: bool = True) -> Tuple[bool, str]:
    """导出全部配置到单个 JSON 文件。

    Args:
        dest_path: 目标文件路径
        include_api_keys: 是否包含 API Key（默认 True）。设为 False 会脱敏。

    Returns:
        (成功与否, 提示消息)
    """
    try:
        cfg = load_config()
        llm_cfg = {k: v for k, v in cfg.items() if k != "ocr"}
        if not include_api_keys:
            llm_cfg.pop("api_key", None)
        ocr_cfg = cfg.get("ocr", {})
        if not include_api_keys:
            # 脱敏凭证
            creds = ocr_cfg.get("credentials", {})
            ocr_cfg["credentials"] = {k: ("***" if v else "") for k, v in creds.items()}

        profile = load_profile()
        prompt = load_prompt()

        payload = {
            "version": EXPORT_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "app": "个人企业世界模型",
            "llm": llm_cfg,
            "ocr": ocr_cfg,
            "profile": profile,
            "prompt": prompt,
        }
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True, f"已导出到：{dest_path}"
    except Exception as e:
        return False, f"导出失败：{e}"


def import_from(src_path: Path, overwrite: bool = True) -> Tuple[bool, str]:
    """从导出文件导入配置。

    Args:
        src_path: 源文件路径
        overwrite: True=覆盖全部，False=只补充空字段

    Returns:
        (成功与否, 提示消息)
    """
    try:
        raw = src_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except Exception as e:
        return False, f"读取文件失败：{e}"

    if not isinstance(payload, dict) or payload.get("app") != "个人企业世界模型":
        return False, "文件格式不正确：不是本应用导出的配置"

    version = payload.get("version", 0)
    if version > EXPORT_VERSION:
        return False, f"导出版本号 ({version}) 高于当前支持 ({EXPORT_VERSION})，请升级应用"

    try:
        # LLM 配置
        llm_cfg = payload.get("llm", {}) or {}
        existing_cfg = load_config()
        if overwrite:
            existing_cfg.update(llm_cfg)
        else:
            for k, v in llm_cfg.items():
                if not existing_cfg.get(k):
                    existing_cfg[k] = v
        # 保留已有 OCR（下面会覆盖）
        save_config(existing_cfg)

        # OCR 配置
        ocr_cfg = payload.get("ocr", {}) or {}
        if ocr_cfg:
            merged = existing_cfg
            merged["ocr"] = ocr_cfg if overwrite else {**existing_cfg.get("ocr", {}), **ocr_cfg}
            save_config(merged)

        # 用户信息
        profile = payload.get("profile", {}) or {}
        if profile:
            if overwrite:
                save_profile(profile)
            else:
                existing = load_profile()
                for k, v in profile.items():
                    if not existing.get(k):
                        existing[k] = v
                save_profile(existing)

        # AI 提示词
        prompt = payload.get("prompt", {}) or {}
        if prompt:
            if overwrite:
                save_prompt(prompt)
            else:
                existing = load_prompt()
                for k, v in prompt.items():
                    if not existing.get(k):
                        existing[k] = v
                save_prompt(existing)

        exported_at = payload.get("exported_at", "?")
        return True, f"导入成功（导出时间：{exported_at}）"
    except Exception as e:
        return False, f"导入失败：{e}"


def backup_to_dir(dest_dir: Path) -> Tuple[bool, str]:
    """把配置目录整体备份到指定目录（用于安全升级前的快照）。"""
    try:
        dest_dir = dest_dir / f"config-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        if CONFIG_DIR.exists():
            shutil.copytree(CONFIG_DIR, dest_dir)
            return True, f"已备份到：{dest_dir}"
        return False, "配置目录不存在，无需备份"
    except Exception as e:
        return False, f"备份失败：{e}"
