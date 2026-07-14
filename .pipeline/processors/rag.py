"""RAG 问答管道：混合检索 + LLM 生成。

支持用户身份注入和自定义提示词。
"""
from typing import Dict, List, Optional

try:
    from .database import search_documents
    from .vector_db import VectorDB
    from .llm_client import chat_completion, load_config
    from .user_profile import load_profile, profile_to_context
    from .prompt_config import load_prompt, render_system_prompt, get_no_result_reply
except ImportError:
    from database import search_documents
    from vector_db import VectorDB
    from llm_client import chat_completion, load_config
    from user_profile import load_profile, profile_to_context
    from prompt_config import load_prompt, render_system_prompt, get_no_result_reply


def _collect_context(
    query: str,
    entity_type: Optional[str] = None,
    fts_k: int = 5,
    vec_k: int = 5,
) -> List[Dict]:
    """混合检索：FTS5 + 向量，去重合并。"""
    seen_paths = set()
    results = []

    # 1. FTS5 关键词检索
    try:
        fts_hits = search_documents(query, entity_type=entity_type, limit=fts_k)
        for r in fts_hits:
            path = r.get("path", "")
            if path and path not in seen_paths:
                seen_paths.add(path)
                results.append({
                    "path": path,
                    "content": r.get("content", ""),
                    "source": "fts5",
                })
    except Exception as e:
        print(f"[rag] FTS5 检索失败: {e}")

    # 2. 向量语义检索
    try:
        vdb = VectorDB()
        vec_hits = vdb.search(query, entity_type=entity_type, top_k=vec_k)
        for r in vec_hits:
            path = r.get("path", "")
            if path and path not in seen_paths:
                seen_paths.add(path)
                results.append({
                    "path": path,
                    "content": r.get("content", ""),
                    "source": "vector",
                    "score": r.get("score", 0.0),
                })
    except Exception as e:
        print(f"[rag] 向量检索失败: {e}")

    return results


def _build_messages(query: str, context: List[Dict]) -> List[Dict]:
    """组装 RAG 提示词（注入用户身份 + 自定义系统提示词）。"""
    # 加载用户身份上下文
    user_context = profile_to_context()
    
    # 渲染系统提示词
    system_prompt = render_system_prompt(user_context)

    if not context:
        user_content = f"用户问题：{query}\n\n（知识库为空或未检索到相关内容）"
    else:
        ctx_parts = []
        for i, c in enumerate(context, 1):
            path = c.get("path", "unknown")
            content = c.get("content", "")[:1500]  # 每段最多 1500 字
            ctx_parts.append(f"[{i}] 来源: {path}\n{content}")
        ctx_text = "\n\n".join(ctx_parts)
        user_content = (
            f"以下是从知识库检索到的相关片段：\n\n{ctx_text}\n\n"
            f"---\n用户问题：{query}\n\n"
            "请基于以上上下文回答问题。如果上下文不足以回答，请明确说明。"
        )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def rag_answer(
    query: str,
    entity_type: Optional[str] = None,
    top_k: int = 5,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict:
    """RAG 问答入口，返回 {"answer": str, "sources": [...], "mode": str}。"""
    context = _collect_context(query, entity_type=entity_type, fts_k=top_k, vec_k=top_k)
    messages = _build_messages(query, context)

    # 检查 API 是否已配置
    cfg = load_config()
    has_api = bool(api_key or cfg.get("api_key"))

    if not has_api:
        # 没有 LLM，退化为纯检索结果
        if not context:
            no_result_text = get_no_result_reply()
            return {
                "answer": no_result_text,
                "sources": [],
                "mode": "no_api",
            }
        parts = []
        for i, c in enumerate(context, 1):
            preview = c.get("content", "")[:300].replace("\n", " ")
            parts.append(f"[{i}] {c.get('path', '')}\n    {preview}")
        answer = "（未配置 LLM API，以下为检索结果原文）\n\n" + "\n\n".join(parts)
        return {
            "answer": answer,
            "sources": [c["path"] for c in context],
            "mode": "retrieval_only",
        }

    # 调用 LLM 生成
    try:
        text = chat_completion(
            messages=messages,
            temperature=0.3,
            max_tokens=1500,
            provider=provider,
            api_key=api_key,
            model=model,
        )
        return {
            "answer": text.strip(),
            "sources": [c["path"] for c in context],
            "mode": "rag",
        }
    except Exception as e:
        return {
            "answer": f"LLM 调用失败: {e}\n\n（检索到的 {len(context)} 条结果仍可查看）",
            "sources": [c["path"] for c in context],
            "mode": "llm_error",
        }
