#!/usr/bin/env python3
"""语义检索入口（FTS5 + 向量混合检索）。"""
import argparse
import sys
from pathlib import Path

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".pipeline" / "processors"))

from database import init_db, search_documents
from vector_db import VectorDB


def format_result(row: dict, index: int) -> str:
    preview = row.get("content", "").replace("\n", " ")[:200]
    score = row.get("score")
    score_str = f" [score: {score:.2f}]" if score is not None else ""
    src = row.get("source", "fts5")
    return (
        f"{index}. [{src}]{score_str} {row.get('path', '')}\n"
        f"   \"{preview}...\""
    )


def search(query: str, layer: str = None, entity_type: str = None,
           confidence: str = None, top_k: int = 5) -> list:
    """混合检索接口，返回结果列表。"""
    init_db()
    etype = entity_type or layer
    seen = set()
    results = []

    # 1. FTS5 关键词检索
    try:
        for r in search_documents(query, entity_type=etype, limit=top_k):
            p = r.get("path")
            if p and p not in seen:
                r["source"] = "fts5"
                results.append(r)
                seen.add(p)
    except Exception as e:
        print(f"[search] FTS5 失败: {e}")

    # 2. 向量语义检索
    try:
        vdb = VectorDB()
        for r in vdb.search(query, entity_type=etype, top_k=top_k):
            p = r.get("path")
            if p and p not in seen:
                r["source"] = "vector"
                results.append(r)
                seen.add(p)
    except Exception as e:
        print(f"[search] 向量检索失败: {e}")

    return results[:top_k]


def main():
    parser = argparse.ArgumentParser(description="混合语义检索")
    parser.add_argument("query", help="检索问题或关键词")
    parser.add_argument("--layer", help="限定层级")
    parser.add_argument("--type", dest="entity_type", help="限定实体类型")
    parser.add_argument("--confidence", help="限定置信度（当前仅占位）")
    parser.add_argument("--top-k", type=int, default=5, help="返回结果数量")
    args = parser.parse_args()

    results = search(
        query=args.query,
        layer=args.layer,
        entity_type=args.entity_type,
        confidence=args.confidence,
        top_k=args.top_k,
    )

    if not results:
        print("未找到相关知识。")
        return

    for i, r in enumerate(results, 1):
        print(format_result(r, i))
        print()


if __name__ == "__main__":
    main()
