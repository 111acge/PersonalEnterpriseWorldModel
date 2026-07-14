#!/usr/bin/env python3
"""语义检索入口（FTS5 + 向量混合检索，RRF 重排序）。"""
import argparse
import sys
from pathlib import Path

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pewm" / "processors"))

from pewm.processors.database import init_db
from pewm.processors.retrieval import hybrid_search


def format_result(row: dict, index: int) -> str:
    preview = row.get("content", "").replace("\n", " ")[:200]
    score = row.get("rrf_score")
    score_str = f" [rrf: {score:.4f}]" if score is not None else ""
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
    return hybrid_search(query, entity_type=etype, top_k=top_k)


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
