"""RAG 问答管道：混合检索 + RRF 重排序 + LLM 生成。

支持用户身份注入和自定义提示词。
"""
from typing import Dict, Iterator, List, Optional

from pewm.processors.log_config import get_logger
from pewm.processors.metrics import timed
from pewm.processors.retrieval import hybrid_search
from pewm.processors.llm_client import chat_completion, chat_completion_stream, load_config
from pewm.processors.user_profile import load_profile, profile_to_context
from pewm.processors.prompt_config import render_system_prompt, get_no_result_reply

logger = get_logger(__name__)


# RAG 上下文总预算（按字符估算，中文约 1.5 字符/token）
DEFAULT_CONTEXT_BUDGET = 6000


def _collect_context(
    query: str,
    entity_type: Optional[str] = None,
    top_k: int = 5,
):
    """使用 RRF 融合的混合检索收集上下文。

    返回 (results, error)：检索异常时 results 为 []、error 为错误信息，
    由调用方以 mode='retrieval_error' 明示上层，而不是伪装成空库。
    """
    try:
        return hybrid_search(query, entity_type=entity_type, top_k=top_k, vec_k=top_k * 2), None
    except Exception as e:
        logger.warning("混合检索失败: %s", e)
        return [], str(e)


def _allocate_context_budget(results: List[Dict], total_budget: int) -> List[Dict]:
    """按 RRF 得分为每个结果分配上下文长度预算。"""
    if not results:
        return []

    total_score = sum(max(r.get("rrf_score", 0), 0.01) for r in results)
    min_chunk = 300
    max_chunk = 1500

    allocated = []
    remaining = total_budget
    for r in results:
        score = max(r.get("rrf_score", 0.01), 0.01)
        share = score / total_score
        budget = int(total_budget * share)
        budget = max(min_chunk, min(budget, max_chunk))
        budget = min(budget, remaining)
        remaining -= budget
        content = r.get("content", "")
        r["used_content"] = content[:budget]
        allocated.append(r)
    return allocated


def _build_messages(query: str, context: List[Dict],
                    history: List[Dict] = None,
                    budget: int = DEFAULT_CONTEXT_BUDGET) -> List[Dict]:
    """组装 RAG 提示词（注入用户身份 + 自定义系统提示词 + 对话历史）。"""
    user_context = profile_to_context()
    system_prompt = render_system_prompt(user_context)

    messages = [{"role": "system", "content": system_prompt}]

    # 注入简短对话历史
    if history:
        for h in history[-6:]:
            messages.append({"role": h["role"], "content": h["content"]})

    if not context:
        user_content = f"用户问题：{query}\n\n（知识库为空或未检索到相关内容）"
    else:
        context = _allocate_context_budget(context, budget)
        ctx_parts = []
        for i, c in enumerate(context, 1):
            path = c.get("path", "unknown")
            content = c.get("used_content", "")
            score = c.get("rrf_score")
            score_info = f" [rrf:{score:.4f}]" if score is not None else ""
            ctx_parts.append(f"[{i}]{score_info} 来源: {path}\n{content}")
        ctx_text = "\n\n".join(ctx_parts)
        user_content = (
            f"以下是从知识库检索到的相关片段：\n\n{ctx_text}\n\n"
            f"---\n用户问题：{query}\n\n"
            "请基于以上上下文回答问题。如果上下文不足以回答，请明确说明。"
        )
    messages.append({"role": "user", "content": user_content})
    return messages


@timed("rag.answer")
def rag_answer(
    query: str,
    entity_type: Optional[str] = None,
    top_k: int = 5,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    history: List[Dict] = None,
) -> Dict:
    """RAG 问答入口，返回 {"answer": str, "sources": [...], "mode": str}。"""
    context, retrieval_error = _collect_context(query, entity_type=entity_type, top_k=top_k)
    if retrieval_error is not None:
        return {
            "answer": f"检索失败：{retrieval_error}",
            "sources": [],
            "mode": "retrieval_error",
        }
    messages = _build_messages(query, context, history=history)

    cfg = load_config()
    has_api = bool(api_key or cfg.get("api_key"))

    if not has_api:
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
            score = c.get("rrf_score")
            score_info = f" [rrf:{score:.4f}]" if score is not None else ""
            parts.append(f"[{i}]{score_info} {c.get('path', '')}\n    {preview}")
        answer = "（未配置 LLM API，以下为检索结果原文）\n\n" + "\n\n".join(parts)
        return {
            "answer": answer,
            "sources": [c["path"] for c in context],
            "mode": "retrieval_only",
        }

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


def rag_answer_stream(
    query: str,
    entity_type: Optional[str] = None,
    top_k: int = 5,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    history: List[Dict] = None,
) -> Iterator[Dict]:
    """RAG 问答流式入口，逐段返回 {"delta": str, "sources": [...], "mode": str, "done": bool}。"""
    context, retrieval_error = _collect_context(query, entity_type=entity_type, top_k=top_k)
    if retrieval_error is not None:
        yield {
            "delta": f"检索失败：{retrieval_error}",
            "sources": [],
            "mode": "retrieval_error",
            "done": True,
        }
        return
    messages = _build_messages(query, context, history=history)

    cfg = load_config()
    has_api = bool(api_key or cfg.get("api_key"))

    if not has_api:
        if not context:
            no_result_text = get_no_result_reply()
            yield {"delta": no_result_text, "sources": [], "mode": "no_api", "done": True}
        else:
            parts = []
            for i, c in enumerate(context, 1):
                preview = c.get("content", "")[:300].replace("\n", " ")
                score = c.get("rrf_score")
                score_info = f" [rrf:{score:.4f}]" if score is not None else ""
                parts.append(f"[{i}]{score_info} {c.get('path', '')}\n    {preview}")
            answer = "（未配置 LLM API，以下为检索结果原文）\n\n" + "\n\n".join(parts)
            yield {"delta": answer, "sources": [c["path"] for c in context], "mode": "retrieval_only", "done": True}
        return

    try:
        yield {"delta": "", "sources": [c["path"] for c in context], "mode": "rag", "done": False}
        for delta in chat_completion_stream(
            messages=messages,
            temperature=0.3,
            max_tokens=1500,
            provider=provider,
            api_key=api_key,
            model=model,
        ):
            yield {"delta": delta, "sources": [c["path"] for c in context], "mode": "rag", "done": False}
        yield {"delta": "", "sources": [c["path"] for c in context], "mode": "rag", "done": True}
    except Exception as e:
        yield {
            "delta": f"\n\nLLM 调用失败: {e}\n\n（检索到的 {len(context)} 条结果仍可查看）",
            "sources": [c["path"] for c in context],
            "mode": "llm_error",
            "done": True,
        }
