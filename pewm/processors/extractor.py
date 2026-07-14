"""基于 LLM + 规则的知识提取器。

优先调用 LLM 分析笔记内容，智能识别实体类型并对输出做 Pydantic 校验。
LLM 失败时回退到基于触发词的规则提取，整篇未命中时作为 note 兜底。
合并策略遵循 schema 中的 auto_merge 与 merge-policy.yaml。
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from pewm.paths import CONFIG_DIR, ROOT, SCHEMAS_DIR
from pewm.processors.merge import merge_entity
from pewm.processors.utils import load_yaml, now_iso, sanitize_filename
from pewm.processors.llm_client import chat_completion, load_config

EXTRACTION_RULES = CONFIG_DIR / "extraction-rules.yaml"


class ExtractedEntity(BaseModel):
    """LLM 返回实体的通用校验模型。"""
    entity_type: str = Field(..., description="实体类型")
    confidence: str = Field("中", description="置信度：高/中/低")

    class Config:
        extra = "allow"


def load_schemas() -> Dict[str, Dict[str, Any]]:
    schemas = {}
    for p in SCHEMAS_DIR.glob("*.yaml"):
        data = load_yaml(p)
        if data and "entity_type" in data:
            schemas[data["entity_type"]] = data
    return schemas


def load_rules() -> List[Dict[str, Any]]:
    data = load_yaml(EXTRACTION_RULES)
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

示例输出：
[
  {{"entity_type": "term", "term": "RAG", "definition": "检索增强生成，是一种结合检索与生成的技术", "aliases": ["检索增强生成"], "related": "", "confidence": "高"}},
  {{"entity_type": "case", "title": "订单服务 OOM 故障", "severity": "高", "occurred_at": "2026-07-10", "phenomenon": "订单服务在高峰期出现 OOM", "root_cause": "数据库连接池泄漏", "handling_process": "定位问题后重启服务", "lessons": "连接池上限从 100 改为 50，并加入健康检查", "confidence": "高"}}
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
        truncated += "\n...(内容过长，已截断)"

    try:
        response = chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"来源文件：{source}\n\n内容：\n{truncated}"},
            ],
            temperature=0.2,
            max_tokens=2500,
        )
    except Exception as e:
        print(f"[extractor] LLM 调用失败：{e}")
        return []

    return _parse_and_validate_llm_json(response, schemas)


def _parse_and_validate_llm_json(text: str, schemas: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """解析 LLM 响应，并用 Pydantic + schema 必填字段双重校验。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if m:
        text = m.group(1)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        print("[extractor] LLM 响应中没有 JSON 数组")
        return []
    try:
        raw_items = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"[extractor] JSON 解析失败：{e}")
        return []
    if not isinstance(raw_items, list):
        print("[extractor] LLM 返回的不是数组")
        return []

    valid_items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            ExtractedEntity.model_validate(item)
        except ValidationError as e:
            print(f"[extractor] 实体校验失败：{e}")
            continue

        etype = item.get("entity_type")
        if etype not in schemas:
            print(f"[extractor] 未知实体类型：{etype}")
            continue

        # 必填字段校验
        schema = schemas[etype]
        missing = [f for f in schema.get("required_frontmatter", [])
                   if f not in item or item[f] in (None, "")]
        # 对 name/title/term/key 等主键字段必须存在
        identity_fields = ["title", "term", "name", "key"]
        if not any(item.get(f) for f in identity_fields):
            missing.append("identity_field")
        if missing:
            print(f"[extractor] 实体 {etype} 缺少字段：{missing}，跳过")
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

def extract_entities(text: str, source: str) -> List[Dict[str, Any]]:
    """提取实体：优先 LLM 智能分析，失败回退规则，再失败整篇存为 note。

    返回的每个元素包含 path/content/entity_type/frontmatter/merged。
    """
    schemas = load_schemas()

    # 1. 优先：LLM 提取
    llm_items = _llm_extract(text, source, schemas)
    if llm_items:
        converted = []
        for item in llm_items:
            c = _llm_item_to_entity(item, schemas, source)
            if c:
                converted.append(c)
        if converted:
            print(f"  [llm] 提取到 {len(converted)} 个实体")
            return converted

    # 2. 兜底：基于触发词的规则提取
    rules = load_rules()
    entities = _rule_extract(text, source, rules, schemas)
    if entities:
        print(f"  [rule] 提取到 {len(entities)} 个实体（LLM 未返回）")
        return entities

    # 3. 终极兜底：整篇作为 note
    if "note" in schemas and text.strip():
        note = build_note_entity(text, source, schemas["note"])
        if note:
            print(f"  [fallback] 整篇存为 note")
            return [note]
    return []


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

    output_path = Path(ROOT) / schema["storage_path"] / filename
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
        m = re.search(r"([\u4e00-\u9fa5\w\s]+?)(?:叫|称为|定义为|指的是)\s*([\u4e00-\u9fa5\w\s]+)", sentence)
        term = (m.group(2).strip() if m else sentence[:30]).strip()
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
    output_path = Path(ROOT) / target / filename
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
    output_path = Path(ROOT) / schema["storage_path"] / filename
    merged = merge_entity(output_path, context, "note", template, render_template, schema=schema)
    merged["frontmatter"] = context
    return merged
