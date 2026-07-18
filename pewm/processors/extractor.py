"""基于 LLM + 规则的知识提取器。

优先调用 LLM 分析笔记内容，智能识别实体类型并对输出做 Pydantic 校验。
LLM 失败时回退到基于触发词的规则提取，整篇未命中时作为 note 兜底。
合并策略遵循 schema 中的 auto_merge 与 merge-policy.yaml。
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

import pewm.paths as paths
from pewm.processors.log_config import get_logger
from pewm.processors.merge import merge_entity
from pewm.processors.utils import load_yaml, now_iso, sanitize_filename
from pewm.processors.llm_client import chat_completion, load_config

logger = get_logger(__name__)


class ExtractedEntity(BaseModel):
    """LLM 返回实体的通用校验模型。"""
    model_config = ConfigDict(extra="allow")

    entity_type: str = Field(..., description="实体类型")
    confidence: str = Field("中", description="置信度：高/中/低")


# 由管线自动填充的系统字段，不要求 LLM 返回
SYSTEM_FIELDS = {"source", "updated_at", "confidence", "case_id", "process_id", "skill_id"}


def load_schemas() -> Dict[str, Dict[str, Any]]:
    schemas = {}
    for p in paths.SCHEMAS_DIR.glob("*.yaml"):
        data = load_yaml(p)
        if data and "entity_type" in data:
            schemas[data["entity_type"]] = data
    return schemas


def load_rules() -> List[Dict[str, Any]]:
    data = load_yaml(paths.CONFIG_DIR / "extraction-rules.yaml")
    return data.get("rules", [])


# ========== 模板渲染 ==========

def render_template(template: str, context: Dict[str, Any]) -> str:
    """轻量模板渲染，支持 {{ var }}、{{ var | default('x') }}、{{ var | join(',') }}。"""
    def repl(match):
        expr = match.group(1).strip()
        filter_name = None
        filter_arg = None
        if "|" in expr:
            key, filter_expr = expr.split("|", 1)
            key = key.strip()
            filter_expr = filter_expr.strip()
            if filter_expr.startswith("default(") and filter_expr.endswith(")"):
                filter_name = "default"
                filter_arg = filter_expr[8:-1].strip("\"' ")
            elif filter_expr.startswith("join(") and filter_expr.endswith(")"):
                filter_name = "join"
                filter_arg = filter_expr[5:-1].strip("\"' ")
        else:
            key = expr

        val = context.get(key)
        if filter_name == "default":
            if val is None or val == "":
                return filter_arg if filter_arg is not None else ""
        if filter_name == "join":
            if isinstance(val, list):
                sep = filter_arg if filter_arg is not None else ", "
                return sep.join(str(v) for v in val if v)
            return str(val) if val is not None else ""

        if val is None:
            return ""
        if isinstance(val, list):
            return ", ".join(str(v) for v in val)
        return str(val)

    return re.sub(r"\{\{(.+?)\}\}", repl, template)


# ========== LLM 提取 ==========

def _build_schemas_prompt(schemas: Dict[str, Dict[str, Any]]) -> str:
    """把所有 schema 拼成一段说明文本，供 LLM 理解每种实体类型。"""
    parts = []
    for etype, schema in schemas.items():
        fm = schema.get("required_frontmatter", [])
        parts.append(
            f"- {etype}: 必填字段 = [{', '.join(fm)}]。"
            f"存储路径: {schema.get('storage_path', '?')}"
        )
    return "\n".join(parts)


_LLM_SYSTEM_PROMPT = """你是一个企业知识库的知识提取助手。

你的任务是把用户的随手记内容拆分成结构化的知识条目。可用的实体类型如下：

{schemas_desc}

**关键：字段名必须严格使用上面列出的英文键名，不要用中文键名！**

各实体类型字段含义速查：
- term（术语）：term=术语名, definition=定义, aliases=别名列表, related=相关概念
- constant（常量）：key=常量名, value=数值, unit=单位, context=上下文, related_processes=关联流程
- case（案例/故障）：title=标题, severity=严重级别(P0/P1/P2/低/中/高), occurred_at=发生日期(YYYY-MM-DD 格式), phenomenon=现象描述, root_cause=根因分析, handling_process=处理过程, lessons=复盘教训
- process（流程）：name=名称, owner=负责人, trigger=触发条件, steps=步骤, systems=涉及系统, related_constants=关联常量
- system（系统）：name=名称, aliases=别名列表, type=类型, responsibility=职责, dependencies=依赖, interfaces=接口
- skill（技能）：name=名称, trigger=触发场景, prompt_or_checklist=提示词或检查清单, examples=示例
- note（笔记）：title=标题, content=完整原文, keywords=关键词(用、分隔)

输出要求：
1. 返回严格的 JSON 数组，不要任何前后缀说明，不要用 ```json 代码块包裹
2. 数组中每个元素是一个对象，必须包含 "entity_type" 字段
3. 字段名用英文键名，不要自己发明中文键名
4. confidence 字段统一取值："高" / "中" / "低"
5. 内容中的每个独立知识点尽量拆成单独条目，但不要过度拆分
6. 如果整段内容实在无法归类，用 entity_type="note"，把完整原文放进 content 字段
7. 不要编造内容，所有字段都要从原文中提取
8. 字段值用纯字符串，不要用转义字符
9. 关联字段（related、related_processes、related_constants、systems、aliases）请尽量从原文找出相关概念填写，多个用「、」分隔；确实没有相关内容的填空字符串

示例输出：
[
  {{"entity_type": "term", "term": "RAG", "definition": "检索增强生成，是一种结合检索与生成的技术", "aliases": ["检索增强生成"], "related": "", "confidence": "高"}},
  {{"entity_type": "case", "title": "订单服务 OOM 故障", "severity": "高", "occurred_at": "2026-07-10", "phenomenon": "订单服务在高峰期出现 OOM", "root_cause": "数据库连接池泄漏", "handling_process": "定位问题后重启服务", "lessons": "连接池上限从 100 改为 50，并加入健康检查", "confidence": "高"}}
]
"""


_LLM_BATCH_PROMPT = _LLM_SYSTEM_PROMPT + """

本次输入包含多篇随手记，每篇以 [SOURCE:index] 开头。请在每个实体中增加一个字段 "source_index"，
表示该实体来自第几篇输入（从 0 开始计数）。其他字段要求不变。

示例输出：
[
  {{"entity_type": "term", "source_index": 0, "term": "RAG", "definition": "...", "confidence": "高"}},
  {{"entity_type": "case", "source_index": 1, "title": "...", "confidence": "中"}}
]
"""


def _llm_extract(text: str, source: str, schemas: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """调用 LLM 分析文本，返回校验后的实体列表。失败时返回 []，调用方走兜底逻辑。"""
    cfg = load_config()
    if not cfg.get("api_key"):
        return []

    schemas_desc = _build_schemas_prompt(schemas)
    system = _LLM_SYSTEM_PROMPT.format(schemas_desc=schemas_desc)

    truncated = text[:6000]
    if len(text) > 6000:
        logger.info("文本超长（%d 字符），截断至 6000 字符用于 LLM 提取，丢弃 %d 字符",
                    len(text), len(text) - 6000)
        truncated += "\n...(内容过长，已截断)"

    try:
        response = chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"来源文件：{source}\n\n内容：\n{truncated}"},
            ],
            temperature=0.2,
            max_tokens=8000,  # 推理型模型思维链占比较高，需要足够输出预算
        )
    except Exception as e:
        logger.warning("LLM 调用失败：%s", e)
        return []

    return _parse_and_validate_llm_json(response, schemas)


def _llm_extract_batch(items: List[tuple], schemas: Dict[str, Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """批量调用 LLM 提取多篇笔记中的实体。

    items: [(source, text), ...]
    返回：与 items 一一对应的实体列表，每个元素是该 source 提取到的实体 dict 列表。
    """
    cfg = load_config()
    if not cfg.get("api_key") or not items:
        return [[] for _ in items]

    schemas_desc = _build_schemas_prompt(schemas)
    system = _LLM_BATCH_PROMPT.format(schemas_desc=schemas_desc)

    parts = []
    total_len = 0
    included = []
    for idx, (source, text) in enumerate(items):
        if not text.strip():
            # 空文本直接跳过，不占批量额度（source_index 仍按原始 idx 对齐）
            logger.info("批量提取跳过空文本：%s", source)
            continue
        remain = max(0, 12000 - total_len)
        chunk = text[:min(2500, remain)]
        if not chunk:
            # 额度耗尽：后续文件交给逐文件兜底
            logger.info("批量提取额度耗尽，剩余 %d 篇跳过批量、转逐文件处理",
                        len(items) - len(included))
            break
        parts.append(f"[SOURCE:{idx}]\n来源文件：{source}\n内容：\n{chunk}\n")
        total_len += len(chunk)
        included.append(idx)
        if total_len >= 12000:
            skipped = len(items) - len(included)
            if skipped > 0:
                logger.info("批量提取达到长度上限，剩余 %d 篇转逐文件处理", skipped)
            break

    try:
        response = chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(parts)},
            ],
            temperature=0.2,
            max_tokens=8000,
        )
    except Exception as e:
        logger.warning("批量 LLM 调用失败：%s", e)
        return [[] for _ in items]

    raw_items = _parse_and_validate_llm_json(response, schemas)
    # 按 source_index 分组
    grouped: List[List[Dict]] = [[] for _ in items]
    for item in raw_items:
        idx = _parse_source_index(item.get("source_index"))
        if idx is None or not (0 <= idx < len(items)):
            # 缺失/非法/越界：不默认归 0，记 warning 后丢弃，由逐文件兜底重提取
            logger.warning("批量结果 source_index 无效（%r），该实体交由逐文件兜底",
                           item.get("source_index"))
            continue
        grouped[idx].append(item)
    return grouped


def _parse_source_index(value: Any) -> Optional[int]:
    """容错地把 LLM 返回的 source_index 转成 int；无法转换时返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _salvage_objects(text: str) -> list:
    """从被截断的 JSON 数组中抢救完整的顶层对象。

    推理型模型输出可能被 max_tokens 截断，数组没有闭合。
    按花括号配平逐个提取完整对象，尽量保留有效实体。
    """
    objects = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict):
                        objects.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1
    return objects


def _parse_and_validate_llm_json(text: str, schemas: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """解析 LLM 响应，并用 Pydantic + schema 主键双重校验。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if m:
        text = m.group(1)
    start = text.find("[")
    end = text.rfind("]")
    raw_items = None
    if start != -1 and end != -1:
        try:
            parsed = json.loads(text[start:end + 1])
            if isinstance(parsed, list):
                raw_items = parsed
        except json.JSONDecodeError:
            pass
    if raw_items is None:
        # 数组不完整（可能被 max_tokens 截断）：抢救完整对象
        raw_items = _salvage_objects(text)
        if raw_items:
            logger.info("JSON 数组不完整，抢救出 %d 个完整实体对象", len(raw_items))
        else:
            logger.warning("LLM 响应中没有 JSON 数组，响应开头：%s", text[:200])
            return []

    valid_items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            ExtractedEntity.model_validate(item)
        except ValidationError as e:
            logger.warning("实体校验失败：%s", e)
            continue

        etype = item.get("entity_type")
        if etype not in schemas:
            logger.warning("未知实体类型：%s", etype)
            continue

        # 主键字段必须存在（title/term/name/key 任一）；
        # 其余必填字段缺失时不丢弃，由 _llm_item_to_entity 填充默认值，避免误杀有效实体
        identity_fields = ["title", "term", "name", "key"]
        if not any(item.get(f) for f in identity_fields):
            logger.warning("实体 %s 缺少主键字段，跳过", etype)
            continue

        valid_items.append(item)

    return valid_items


# ========== 基于规则的提取（兜底） ==========

def _rule_extract(text: str, source: str,
                  rules: List[Dict[str, Any]],
                  schemas: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    entities = []
    sentences = re.split(r"[。！？\n]", text)
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        for rule in rules:
            for pattern in rule.get("trigger_patterns", []):
                if re.search(pattern, sent):
                    entity = build_entity(rule, sent, text, schemas, source)
                    if entity:
                        entities.append(entity)
                    break
    return entities


# ========== 统一入口 ==========

def extract_entities(text: str, source: str, offline: bool = False) -> List[Dict[str, Any]]:
    """提取实体：优先 LLM 智能分析；未配置 LLM 时回退规则；LLM 已配置但无结果时整篇存为 note。

    原则：本体建设必须经过 LLM。已配置 API Key 的情况下不再使用规则提取制造稀疏卡片。
    offline=True 时强制跳过 LLM，直接走规则提取 + note 兜底。
    返回的每个元素包含 path/content/entity_type/frontmatter/merged。
    """
    schemas = load_schemas()
    has_llm = bool(load_config().get("api_key")) and not offline

    # 1. 优先：LLM 提取
    llm_items = [] if offline else _llm_extract(text, source, schemas)
    if llm_items:
        converted = []
        for item in llm_items:
            c = _llm_item_to_entity(item, schemas, source)
            if c:
                converted.append(c)
        if converted:
            # 原文超 6000 字符时 LLM 只看到截断部分，强制追加整篇 note 保留全文
            if len(text) > 6000 and "note" in schemas:
                note = build_note_entity(text, source, schemas["note"])
                if note:
                    converted.append(note)
                    logger.info("[llm] 原文超长（%d 字符），已追加整篇 note 保留全文", len(text))
            logger.info("[llm] 提取到 %d 个实体", len(converted))
            return converted

    # 2. 兜底：仅当未配置 LLM（或离线模式）时才使用规则提取（离线/测试场景）
    if not has_llm:
        rules = load_rules()
        entities = _rule_extract(text, source, rules, schemas)
        if entities:
            logger.info("[rule] 提取到 %d 个实体（未配置 LLM）", len(entities))
            return entities

    # 3. 终极兜底：整篇作为 note
    if "note" in schemas and text.strip():
        note = build_note_entity(text, source, schemas["note"])
        if note:
            logger.info("[fallback] 整篇存为 note")
            return [note]
    return []


def extract_entities_batch(items: List[tuple], offline: bool = False) -> List[List[Dict[str, Any]]]:
    """批量提取实体。

    items: [(source, text), ...]
    offline=True 时跳过 LLM，逐文件走规则提取 + note 兜底。
    返回：与 items 一一对应的 entity 列表列表。
    """
    if not items:
        return []
    if offline:
        return [extract_entities(text, source, offline=True) for source, text in items]
    schemas = load_schemas()

    # 1. 优先：批量 LLM 提取
    grouped = _llm_extract_batch(items, schemas)
    any_llm = any(grouped)
    if any_llm:
        results = []
        for idx, (source, text) in enumerate(items):
            llm_items = grouped[idx]
            if llm_items:
                converted = []
                for item in llm_items:
                    c = _llm_item_to_entity(item, schemas, source)
                    if c:
                        converted.append(c)
                if converted:
                    if len(text) > 6000 and "note" in schemas:
                        note = build_note_entity(text, source, schemas["note"])
                        if note:
                            converted.append(note)
                            logger.info("[llm-batch] %s 原文超长（%d 字符），已追加整篇 note 保留全文",
                                        source, len(text))
                    logger.info("[llm-batch] %s 提取到 %d 个实体", source, len(converted))
                    results.append(converted)
                    continue
            # 单个文件 fallback
            results.append(extract_entities(text, source))
        return results

    # 2. 全部 fallback：逐文件规则提取
    return [extract_entities(text, source) for source, text in items]


def _llm_item_to_entity(item: Dict[str, Any], schemas: Dict[str, Dict[str, Any]],
                        source: str) -> Optional[Dict[str, Any]]:
    """把 LLM 返回的 JSON 对象转换成标准 entity 字典，并触发合并逻辑。"""
    etype = item.get("entity_type")
    if not etype or etype not in schemas:
        return None
    schema = schemas[etype]

    context = {
        "source": source,
        "updated_at": now_iso(),
        "confidence": item.get("confidence", "中"),
    }
    for k, v in item.items():
        if k == "entity_type":
            continue
        context[k] = v

    for field in schema.get("required_frontmatter", []):
        if field not in context or context[field] in (None, ""):
            if field == "confidence":
                context[field] = "llm-inferred"
            elif field == "source":
                context[field] = source
            elif field == "updated_at":
                context[field] = now_iso()
            elif field in ("case_id", "process_id", "skill_id"):
                # 系统生成 ID，LLM 不提供时自动补齐
                prefix = {"case_id": "CASE", "process_id": "PROC", "skill_id": "SKILL"}[field]
                context[field] = f"{prefix}-" + now_iso().replace(":", "").replace("-", "").replace("T", "-")[:16]
            elif field == "occurred_at":
                context[field] = now_iso()[:10]
            elif field == "owner":
                context[field] = "未指定"
            elif field == "severity":
                context[field] = "中"
            elif field == "aliases" and etype in ("term", "system"):
                context[field] = []
            else:
                context[field] = ""

    name_field = next(
        (f for f in ["title", "term", "name", "key", "case_id"] if context.get(f)),
        None,
    )
    name = str(context.get(name_field, "")) if name_field else ""
    if not name:
        name = "untitled"
    filename = sanitize_filename(name) + ".md"

    output_path = Path(paths.ROOT) / schema["storage_path"] / filename
    template = schema.get("content_template", "")

    merged = merge_entity(output_path, context, etype, template, render_template, schema=schema)
    merged["frontmatter"] = context
    return merged


# ========== 规则提取的具体构建逻辑（兜底） ==========

def build_entity(rule: Dict[str, Any], sentence: str, full_text: str,
                 schemas: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    target = rule["target_layer"]
    entity_type = None
    for etype, schema in schemas.items():
        if schema["storage_path"] == target:
            entity_type = etype
            break
    if not entity_type:
        return None

    schema = schemas[entity_type]
    now = now_iso()
    context = {"source": source, "updated_at": now, "confidence": "inferred"}

    if entity_type == "term":
        # 按连词区分语序：
        #   "我们叫/称为/简称/又称 X"   → 术语在连词之后（group 3）
        #   "X 定义为/指的是 ..."       → 术语在连词之前（group 1）
        m = re.search(
            r"([\u4e00-\u9fa5\w\s]+?)(叫|称为|简称|又称|定义为|指的是)\s*([\u4e00-\u9fa5\w\s]+)",
            sentence)
        if m:
            if m.group(2) in ("定义为", "指的是"):
                term = m.group(1).strip()
            else:
                term = m.group(3).strip()
        else:
            term = sentence[:30].strip()
        context.update({"term": term, "aliases": [], "definition": sentence, "related": ""})
        filename = sanitize_filename(term) + ".md"
    elif entity_type == "constant":
        m = re.search(r"(\d+)\s*(分钟|小时|天|%|次|个)", sentence)
        key = sentence[:20].strip() if not m else f"{sentence.split('，')[0][:20]}"
        value = m.group(1) if m else ""
        unit = m.group(2) if m else ""
        context.update({"key": key, "value": value, "unit": unit, "context": sentence, "related_processes": ""})
        filename = sanitize_filename(key) + ".md"
    elif entity_type == "case":
        title = sentence[:30]
        context.update({
            "case_id": "CASE-" + now.replace(":", "").replace("-", "").replace("T", "-")[:16],
            "title": title, "severity": "P1", "occurred_at": now[:10],
            "phenomenon": sentence, "root_cause": "", "handling_process": "", "lessons": "",
        })
        filename = sanitize_filename(title) + ".md"
    elif entity_type == "process":
        name = sentence[:30]
        context.update({
            "process_id": "PROC-" + sanitize_filename(name).upper()[:10],
            "name": name, "owner": "未指定", "trigger": sentence,
            "steps": sentence, "systems": "", "related_constants": "",
        })
        filename = sanitize_filename(name) + ".md"
    elif entity_type == "system":
        m = re.search(r"([\u4e00-\u9fa5\w]+(?:系统|平台|服务|模块|数据库|API))", sentence)
        name = m.group(1).strip() if m else sentence[:20]
        context.update({"name": name, "aliases": [], "type": "系统",
                        "responsibility": sentence, "dependencies": "", "interfaces": ""})
        filename = sanitize_filename(name) + ".md"
    elif entity_type == "skill":
        name = sentence[:30]
        context.update({"skill_id": "SKILL-" + sanitize_filename(name).upper()[:10],
                        "name": name, "trigger": sentence,
                        "prompt_or_checklist": sentence, "examples": ""})
        filename = sanitize_filename(name) + ".md"
    elif entity_type == "note":
        title = sentence[:30]
        context.update({"title": title, "content": sentence, "keywords": ""})
        filename = sanitize_filename(title) + ".md"
    else:
        return None

    template = schema["content_template"]
    output_path = Path(paths.ROOT) / target / filename
    merged = merge_entity(output_path, context, entity_type, template, render_template, schema=schema)
    merged["frontmatter"] = context
    return merged


def build_note_entity(text: str, source: str, schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """终极兜底：把整篇 Inbox 文件作为 note 实体存入。"""
    lines = text.strip().splitlines()
    title = ""
    for line in lines:
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            break
        if line and not title:
            title = line[:40].strip()
    if not title:
        title = Path(source).stem

    keywords = []
    for word in re.findall(r"[\u4e00-\u9fa5a-zA-Z]{2,8}", text):
        if word not in keywords and len(keywords) < 10:
            keywords.append(word)

    context = {
        "title": title,
        "source": source,
        "updated_at": now_iso(),
        "confidence": "raw",
        "content": text.strip(),
        "keywords": "、".join(keywords),
    }
    template = schema["content_template"]
    filename = sanitize_filename(title) + ".md"
    output_path = Path(paths.ROOT) / schema["storage_path"] / filename
    merged = merge_entity(output_path, context, "note", template, render_template, schema=schema)
    merged["frontmatter"] = context
    return merged
