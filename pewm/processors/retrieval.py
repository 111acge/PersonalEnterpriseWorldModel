"""混合检索：FTS5 关键词 + 向量语义，使用 RRF 重排序。"""
from typing import Dict, List, Optional

from pewm.processors.database import search_documents
from pewm.processors.vector_db import VectorDB


def _normalize_scores(results: List[Dict], score_key: str = "score",
                      reverse: bool = True) -> List[Dict]:
    """把结果按分数/位置排序，并补 rank。"""
    if reverse:
        results = sorted(results, key=lambda x: x.get(score_key, 0), reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return results


def reciprocal_rank_fusion(fts_results: List[Dict], vec_results: List[Dict],
                           k: int = 60, top_k: int = 10) -> List[Dict]:
    """RRF 融合 FTS5 与向量检索结果。

    公式：score = Σ 1 / (k + rank)
    """
    fts_results = _normalize_scores(fts_results, score_key="score", reverse=False)
    vec_results = _normalize_scores(vec_results, score_key="score", reverse=True)

    fused: Dict[str, Dict] = {}

    def _ensure(path: str, r: Dict):
        if path not in fused:
            fused[path] = {
                "path": path,
                "content": r.get("content", ""),
                "title": r.get("title", ""),
                "entity_type": r.get("entity_type", ""),
                "updated_at": r.get("updated_at", ""),
                "sources": set(),
                "rrf_score": 0.0,
            }

    for r in fts_results:
        path = r.get("path", "")
        if not path:
            continue
        _ensure(path, r)
        fused[path]["rrf_score"] += 1.0 / (k + r["rank"])
        fused[path]["sources"].add("fts5")

    for r in vec_results:
        path = r.get("path", "")
        if not path:
            continue
        _ensure(path, r)
        fused[path]["rrf_score"] += 1.0 / (k + r["rank"])
        fused[path]["sources"].add("vector")
        if "score" in r:
            fused[path]["vector_score"] = r["score"]

    output = []
    for path, data in fused.items():
        data["sources"] = sorted(data["sources"])
        data["source"] = "+".join(data["sources"])
        output.append(data)

    output.sort(key=lambda x: -x["rrf_score"])
    return output[:top_k]


def hybrid_search(query: str, entity_type: Optional[str] = None,
                  top_k: int = 10, vec_k: int = 20) -> List[Dict]:
    """对外暴露的混合检索接口。"""
    fts_hits = search_documents(query, entity_type=entity_type, limit=top_k * 2)
    vdb = VectorDB()
    vec_hits = vdb.search(query, entity_type=entity_type, top_k=vec_k)
    return reciprocal_rank_fusion(fts_hits, vec_hits, top_k=top_k)
