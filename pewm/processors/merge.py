"""实体合并与冲突解决。

支持两种配置来源：
1. schema 中的 auto_merge 字段（true/false）
2. merge-policy.yaml 中按 field + entity_type 定义的 strategy

合并仅在目标文件已存在时触发。若 auto_merge=false 且策略未命中，
默认生成带序号的新文件，保留旧版本。
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pewm.paths import CONFIG_DIR
from pewm.processors.log_config import get_logger
from pewm.processors.utils import load_yaml, now_iso, read_text, write_text

logger = get_logger(__name__)

MERGE_POLICY_FILE = CONFIG_DIR / "merge-policy.yaml"


def load_merge_policy() -> List[Dict[str, Any]]:
    """加载合并策略。"""
    data = load_yaml(MERGE_POLICY_FILE) or {}
    return data.get("merge_policy", [])


def _policy_for_field(policy: List[Dict[str, Any]], field: str,
                      entity_type: str) -> Optional[str]:
    """找到 field + entity_type 最匹配的策略。"""
    best = None
    for rule in policy:
        rule_field = rule.get("field", "*")
        rule_types = rule.get("entity_types", ["*"])
        if rule_field != field and rule_field != "*":
            continue
        if entity_type not in rule_types and "*" not in rule_types:
            continue
        # 越具体的规则优先级越高
        score = 0
        if rule_field == field:
            score += 2
        if entity_type in rule_types:
            score += 1
        if best is None or score > best["score"]:
            best = {"score": score, "strategy": rule.get("strategy", "append")}
    return best["strategy"] if best else "append"


def _parse_frontmatter(content: str) -> Dict[str, Any]:
    """从现有 markdown 中解析 YAML frontmatter（简单实现）。"""
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        import yaml
        return yaml.safe_load(parts[1]) or {}
    except Exception:
        logger.warning("YAML frontmatter 解析失败，返回空")
        return {}


def _merge_value(old: Any, new: Any, strategy: str, field: str) -> Any:
    """按策略合并单个字段。"""
    if strategy == "overwrite":
        return new if new not in (None, "") else old
    if strategy == "union":
        old_list = old if isinstance(old, list) else ([old] if old else [])
        new_list = new if isinstance(new, list) else ([new] if new else [])
        merged: List[Any] = []
        for item in old_list + new_list:
            if item not in merged:
                merged.append(item)
        return merged
    if strategy == "max":
        # 对 confidence 字段：高 > 中 > 低
        if field == "confidence":
            order = {"高": 3, "中": 2, "低": 1, "llm-inferred": 2,
                     "inferred": 1, "raw": 0}
            old_score = order.get(str(old).strip(), 0)
            new_score = order.get(str(new).strip(), 0)
            return new if new_score >= old_score else old
        try:
            return max(old, new)
        except Exception:
            logger.warning("max 策略合并失败，field=%s", field)
            return new if new not in (None, "") else old
    # 默认 append
    old_str = "" if old in (None, "") else str(old).strip()
    new_str = "" if new in (None, "") else str(new).strip()
    if not old_str:
        return new_str
    if not new_str:
        return old_str
    if new_str in old_str:
        return old_str
    if old_str in new_str:
        return new_str
    return old_str + "\n\n" + new_str


def _find_existing_path(output_path: Path) -> Optional[Path]:
    """查找已存在的同实体文件。

    优先返回 stem 本体；本体不存在时，仅匹配 ``stem-<数字>`` 形式的序号文件
    （避免 ``foo-bar.md`` 这类仅前缀相同的文件被误合并），命中多个时取序号最大者。
    """
    if output_path.exists():
        return output_path
    parent = output_path.parent
    if not parent.exists():
        return None
    stem = output_path.stem
    numbered = re.compile(r"^" + re.escape(stem) + r"-(\d+)$")
    best: Optional[Path] = None
    best_num = -1
    for p in parent.glob("*.md"):
        if p.stem == stem:
            return p
        m = numbered.match(p.stem)
        if m and int(m.group(1)) > best_num:
            best = p
            best_num = int(m.group(1))
    return best


def _next_available_path(output_path: Path) -> Path:
    """生成不冲突的新文件路径。"""
    if not output_path.exists():
        return output_path
    parent = output_path.parent
    stem = output_path.stem
    suffix = output_path.suffix
    counter = 1
    while True:
        candidate = parent / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _dump_frontmatter(context: Dict[str, Any], entity_type: str,
                      schema: Optional[Dict[str, Any]]) -> str:
    """把实体元数据序列化为 YAML frontmatter 字符串。

    优先按 schema 的 required_frontmatter 选字段（避免 note.content 这类长正文
    重复进 frontmatter）；schema 未声明时退化为全量上下文字段。
    额外补充 type / created_at 两个系统字段。
    """
    import yaml

    fields = None
    if schema:
        required = schema.get("required_frontmatter")
        if required:
            fields = [f for f in required if f in context]
    if fields is None:
        fields = [k for k in context if k != "frontmatter"]

    fm: Dict[str, Any] = {"type": entity_type}
    for f in fields:
        fm[f] = context[f]
    fm["created_at"] = context.get("created_at") or context.get("updated_at") or now_iso()
    return yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()


def _render_with_frontmatter(template: str, context: Dict[str, Any],
                             entity_type: str, schema: Optional[Dict[str, Any]],
                             render_template) -> str:
    """渲染模板，同时把元数据序列化结果注入 {{ frontmatter }} 占位符。"""
    render_context = dict(context)
    render_context["frontmatter"] = _dump_frontmatter(context, entity_type, schema)
    return render_template(template, render_context)


def _normalize_for_compare(text: str) -> str:
    """内容等值比较前抹掉时间戳、日期、自动生成 ID 等每次运行都会变化的成分。"""
    text = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "<ts>", text)
    text = re.sub(r"\b(?:CASE|PROC|SKILL)-\d{8}-\d{6}\b", "<id>", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "<date>", text)
    return text.strip()


def merge_entity(output_path: Path, new_context: Dict[str, Any],
                 entity_type: str, content_template: str,
                 render_template, schema: Dict[str, Any] = None) -> Dict[str, Any]:
    """合并实体到磁盘。

    返回 {"path": Path, "content": str, "merged": bool, "entity_type": str}
    """
    auto_merge = True
    if schema:
        auto_merge = schema.get("auto_merge", True)
    policy = load_merge_policy()

    existing_path = _find_existing_path(output_path)

    if not existing_path or not auto_merge:
        new_content = _render_with_frontmatter(
            content_template, new_context, entity_type, schema, render_template)
        if existing_path and not auto_merge:
            # auto_merge=false 的类型（note/case）：重复处理同一来源且内容未变时
            # 跳过新建，避免 --reset 重跑无限生成 -1/-2 副本
            old_content = read_text(existing_path)
            if _normalize_for_compare(old_content) == _normalize_for_compare(new_content):
                logger.info("内容未变化，跳过重复新建：%s", existing_path.name)
                return {
                    "path": existing_path,
                    "content": old_content,
                    "merged": False,
                    "entity_type": entity_type,
                }
        final_path = _next_available_path(output_path)
        return {
            "path": final_path,
            "content": new_content,
            "merged": False,
            "entity_type": entity_type,
        }

    # 读取旧实体并解析 frontmatter
    old_content = read_text(existing_path)
    old_fm = _parse_frontmatter(old_content)

    merged_context = dict(old_fm)
    merged_context.pop("frontmatter", None)
    for key, new_val in new_context.items():
        old_val = merged_context.get(key)
        strategy = _policy_for_field(policy, key, entity_type) or "append"
        merged_context[key] = _merge_value(old_val, new_val, strategy, key)

    merged_context.setdefault("source", new_context.get("source", ""))
    merged_context["updated_at"] = now_iso()

    return {
        "path": existing_path,
        "content": _render_with_frontmatter(
            content_template, merged_context, entity_type, schema, render_template),
        "merged": True,
        "entity_type": entity_type,
    }
