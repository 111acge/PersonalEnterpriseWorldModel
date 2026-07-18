"""配置导出/导入模块：把 API Key、用户信息、AI 提示词统一打包成一个 JSON 文件。

导出文件结构：
{
    "version": 1,
    "exported_at": "2026-07-13T10:00:00",
    "app": "个人企业世界模型",
    "contains_api_keys": false,
    "llm": {...},         # config.json 的 LLM 段（不含 OCR 段）
    "ocr": {...},         # config.json 的 OCR 段
    "profile": {...},     # profile.json
    "prompt": {...},      # prompt.json
}
"""
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from pewm.processors.llm_client import CONFIG_DIR, CONFIG_FILE, load_config
from pewm.processors.log_config import get_logger
from pewm.processors.user_profile import load_profile, PROFILE_FILE
from pewm.processors.prompt_config import load_prompt, PROMPT_FILE, PROMPT_FIELDS

logger = get_logger(__name__)

EXPORT_VERSION = 1

# 脱敏占位符：导出时替换真实密钥，导入时识别并跳过（保留本地原值）
MASK_PLACEHOLDER = "***"


def _atomic_write_json(file_path: Path, data: Dict) -> None:
    """原子写 JSON：先写同目录临时文件，再 os.replace 替换，避免半截文件。"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(file_path.parent), prefix=file_path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2))
        os.replace(tmp_name, file_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _save_config_atomic(cfg: Dict) -> None:
    _atomic_write_json(CONFIG_FILE, cfg)


def _save_profile_atomic(profile: Dict) -> None:
    _atomic_write_json(PROFILE_FILE, profile)


def _save_prompt_atomic(prompt: Dict) -> None:
    # 与 prompt_config.save_prompt 保持一致：只保存已知字段
    filtered = {f["key"]: prompt.get(f["key"], f["default"]) for f in PROMPT_FIELDS}
    _atomic_write_json(PROMPT_FILE, filtered)


def export_all(dest_path: Path, include_api_keys: bool = False) -> Tuple[bool, str]:
    """导出全部配置到单个 JSON 文件。

    Args:
        dest_path: 目标文件路径
        include_api_keys: 是否包含 API Key（默认 False，脱敏导出）。

    Returns:
        (成功与否, 提示消息)
    """
    try:
        cfg = load_config()
        llm_cfg = {k: v for k, v in cfg.items() if k != "ocr"}
        if not include_api_keys:
            llm_cfg.pop("api_key", None)
        ocr_cfg = dict(cfg.get("ocr", {}))
        if not include_api_keys:
            # 脱敏凭证
            creds = ocr_cfg.get("credentials", {})
            ocr_cfg["credentials"] = {
                k: (MASK_PLACEHOLDER if v else "") for k, v in creds.items()
            }

        profile = load_profile()
        prompt = load_prompt()

        payload = {
            "version": EXPORT_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "app": "个人企业世界模型",
            "contains_api_keys": bool(include_api_keys),
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
        msg = f"已导出到：{dest_path}"
        if include_api_keys:
            msg += "（包含明文 API Key，请妥善保管，勿外发）"
        return True, msg
    except Exception as e:
        logger.warning("配置导出失败：%s", e)
        return False, f"导出失败：{e}"


def _validate_payload(payload: Dict) -> Optional[str]:
    """整体校验导入文件四段结构，返回错误消息；全部通过返回 None。"""
    for section in ("llm", "ocr", "profile", "prompt"):
        if section in payload and payload[section] is not None \
                and not isinstance(payload[section], dict):
            return f"配置段 {section} 结构不正确（应为对象）"

    llm = payload.get("llm") or {}
    for key in ("provider", "api_key", "model", "base_url"):
        if key in llm and not isinstance(llm[key], str):
            return f"llm.{key} 类型不正确（应为字符串）"

    ocr = payload.get("ocr") or {}
    for key in ("mode", "provider"):
        if key in ocr and not isinstance(ocr[key], str):
            return f"ocr.{key} 类型不正确（应为字符串）"
    if "credentials" in ocr and not isinstance(ocr["credentials"], dict):
        return "ocr.credentials 类型不正确（应为对象）"
    for k, v in (ocr.get("credentials") or {}).items():
        if not isinstance(v, str):
            return f"ocr.credentials.{k} 类型不正确（应为字符串）"

    for k, v in (payload.get("profile") or {}).items():
        if not isinstance(v, str):
            return f"profile.{k} 类型不正确（应为字符串）"

    for k, v in (payload.get("prompt") or {}).items():
        if not isinstance(v, str):
            return f"prompt.{k} 类型不正确（应为字符串）"

    return None


def import_from(src_path: Path, overwrite: bool = True) -> Tuple[bool, str]:
    """从导出文件导入配置。

    导入前先整体校验四段结构，全部通过后才落盘（各文件原子写）。

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
        logger.warning("读取导入文件失败：%s", e)
        return False, f"读取文件失败：{e}"

    if not isinstance(payload, dict) or payload.get("app") != "个人企业世界模型":
        return False, "文件格式不正确：不是本应用导出的配置"

    version = payload.get("version", 0)
    if version > EXPORT_VERSION:
        return False, f"导出版本号 ({version}) 高于当前支持 ({EXPORT_VERSION})，请升级应用"

    # 第一步：整体校验四段结构，任何一段不合格都不落盘
    error = _validate_payload(payload)
    if error:
        logger.warning("导入文件校验失败：%s", error)
        return False, f"导入文件校验失败：{error}"

    try:
        existing_cfg = load_config()

        # LLM 配置（'***' 占位符跳过，保留本地原值）
        llm_cfg = {
            k: v for k, v in (payload.get("llm") or {}).items()
            if v != MASK_PLACEHOLDER
        }
        if overwrite:
            existing_cfg.update(llm_cfg)
        else:
            for k, v in llm_cfg.items():
                if not existing_cfg.get(k):
                    existing_cfg[k] = v

        # OCR 配置：credentials 逐字段合并，'***' 占位符保留本地原值
        ocr_cfg = payload.get("ocr") or {}
        if ocr_cfg:
            existing_ocr = existing_cfg.get("ocr", {}) or {}
            existing_creds = existing_ocr.get("credentials", {}) or {}
            if overwrite:
                merged_ocr = dict(ocr_cfg)
            else:
                merged_ocr = {**existing_ocr, **ocr_cfg}
            merged_creds = dict(existing_creds)
            for k, v in (ocr_cfg.get("credentials") or {}).items():
                if v == MASK_PLACEHOLDER:
                    continue  # 脱敏占位符，保留现有密钥
                if overwrite or not existing_creds.get(k):
                    merged_creds[k] = v
            merged_ocr["credentials"] = merged_creds
            existing_cfg["ocr"] = merged_ocr
        _save_config_atomic(existing_cfg)

        # 用户信息
        profile = payload.get("profile") or {}
        if profile:
            if overwrite:
                _save_profile_atomic(profile)
            else:
                existing = load_profile()
                for k, v in profile.items():
                    if not existing.get(k):
                        existing[k] = v
                _save_profile_atomic(existing)

        # AI 提示词
        prompt = payload.get("prompt") or {}
        if prompt:
            if overwrite:
                _save_prompt_atomic(prompt)
            else:
                existing = load_prompt()
                for k, v in prompt.items():
                    if not existing.get(k):
                        existing[k] = v
                _save_prompt_atomic(existing)

        exported_at = payload.get("exported_at", "?")
        return True, f"导入成功（导出时间：{exported_at}）"
    except Exception as e:
        logger.warning("配置导入失败：%s", e)
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
        logger.warning("配置备份失败：%s", e)
        return False, f"备份失败：{e}"


def restore_from_dir(backup_dir: Path) -> Dict:
    """从备份目录还原配置。

    还原前先对当前配置目录做一次 backup_to_dir 快照，再整体覆盖还原。

    Returns:
        {"success": bool, "message": str, "snapshot": 快照目录或 None}
    """
    try:
        backup_dir = Path(backup_dir)
        if not (backup_dir / "config.json").is_file():
            return {
                "success": False,
                "message": f"备份目录缺少 config.json，无法还原：{backup_dir}",
                "snapshot": None,
            }

        snapshot = None
        if CONFIG_DIR.exists():
            snap_ok, snap_msg = backup_to_dir(CONFIG_DIR.parent)
            if not snap_ok:
                return {
                    "success": False,
                    "message": f"还原前快照失败，已中止还原：{snap_msg}",
                    "snapshot": None,
                }
            snapshot = snap_msg.split("：", 1)[-1]

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(backup_dir, CONFIG_DIR, dirs_exist_ok=True)
        return {
            "success": True,
            "message": f"已从备份还原：{backup_dir}",
            "snapshot": snapshot,
        }
    except Exception as e:
        logger.warning("配置还原失败：%s", e)
        return {"success": False, "message": f"还原失败：{e}", "snapshot": None}
