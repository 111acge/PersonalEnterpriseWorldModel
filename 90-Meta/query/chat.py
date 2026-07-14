#!/usr/bin/env python3
"""对话式问答入口（RAG 版）。"""
import argparse
import sys
from pathlib import Path

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".pipeline" / "processors"))

from database import init_db
from rag import rag_answer


def chat(query: str, layer: str = None, entity_type: str = None,
         confidence: str = None, top_k: int = 5,
         api_key: str = None, provider: str = None, model: str = None) -> str:
    """对外暴露的对话接口，返回回答字符串。兼容旧签名。"""
    init_db()
    etype = entity_type or layer
    result = rag_answer(
        query=query,
        entity_type=etype,
        top_k=top_k,
        api_key=api_key,
        provider=provider,
        model=model,
    )
    answer = result.get("answer", "")
    sources = result.get("sources", [])
    mode = result.get("mode", "")

    # 附加来源
    if sources:
        src_lines = "\n".join(f"  - {s}" for s in sources[:5])
        answer += f"\n\n引用来源：\n{src_lines}"

    if mode == "retrieval_only":
        answer += "\n\n提示：在「API 配置」中填写 LLM API Key 可启用生成式问答。"

    return answer


def main():
    parser = argparse.ArgumentParser(description="RAG 对话式问答")
    parser.add_argument("query", help="你的问题")
    parser.add_argument("--layer", help="限定层级")
    parser.add_argument("--type", dest="entity_type", help="限定实体类型")
    parser.add_argument("--confidence", help="限定置信度（当前仅占位）")
    parser.add_argument("--top-k", type=int, default=5, help="引用片段数量")
    parser.add_argument("--api-key", help="覆盖 API Key")
    parser.add_argument("--provider", help="覆盖提供商（deepseek/kimi/minimax）")
    parser.add_argument("--model", help="覆盖模型名")
    args = parser.parse_args()

    print(chat(
        query=args.query,
        layer=args.layer,
        entity_type=args.entity_type,
        confidence=args.confidence,
        top_k=args.top_k,
        api_key=args.api_key,
        provider=args.provider,
        model=args.model,
    ))


if __name__ == "__main__":
    main()
