"""混合检索：FTS5 关键词 + 向量语义，使用 RRF 重排序 + embedding rerank。"""
import threading
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import numpy as np

from pewm.processors.database import search_documents
from pewm.processors.log_config import get_logger
from pewm.processors.metrics import timed
from pewm.processors.vector_db import VectorDB, _load_embedder

logger = get_logger(__name__)

# 检索结果缓存：最近 128 条查询
_search_cache = {}
_MAX_CACHE_SIZE = 128

# 进程级 VectorDB 单例：避免每次检索都全量加载向量库。
# 向量库写操作会调用 on_vector_store_changed() 把单例标记为过期，
# 下次检索时惰性 refresh。
_vdb_holder: Dict = {"instance": None, "stale": True}
_vdb_lock = threading.Lock()


def _get_vector_db() -> VectorDB:
    """获取进程级 VectorDB 单例；过期时先 refresh 再返回。"""
    with _vdb_lock:
        inst = _vdb_holder["instance"]
        if inst is None:
            inst = VectorDB()
            _vdb_holder["instance"] = inst
            _vdb_holder["stale"] = False
        elif _vdb_holder["stale"]:
            try:
                inst.refresh()
            except Exception as e:
                logger.warning("VectorDB 单例刷新失败，重建实例：%s", e)
                inst = VectorDB()
                _vdb_holder["instance"] = inst
            _vdb_holder["stale"] = False
        return inst


def _cache_key(query: str, entity_type: Optional[str], top_k: int, vec_k: int, rerank: bool) -> Tuple:
    return (query, entity_type or "", top_k, vec_k, rerank)


def _get_cached(key: Tuple):
    return _search_cache.get(key)


def _set_cached(key: Tuple, value: List[Dict]) -> None:
    if len(_search_cache) >= _MAX_CACHE_SIZE:
        # 简单 LRU：清空一半
        keys = list(_search_cache.keys())[: _MAX_CACHE_SIZE // 2]
        for k in keys:
            _search_cache.pop(k, None)
    _search_cache[key] = value


def invalidate_search_cache() -> None:
    """文档增删改后调用，清空检索缓存。"""
    _search_cache.clear()


def on_vector_store_changed() -> None:
    """向量库写操作后的钩子：清检索缓存并把进程级单例标记为过期。

    由 vector_db 的写路径（add/add_batch/soft_delete/restore/hard_delete/
    reconcile/rebuild）调用；标记为惰性过期，下次检索时才真正 refresh，
    避免批量写入期间反复全量加载。
    """
    _search_cache.clear()
    with _vdb_lock:
        _vdb_holder["stale"] = True


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


def _embedding_rerank(query: str, candidates: List[Dict]) -> List[Dict]:
    """用 embedding 模型对候选结果做最终重排序。

    仅在有 sentence-transformers 时启用；否则保持 RRF 顺序。
    """
    if not candidates:
        return candidates
    embedder, kind = _load_embedder()
    if kind != "transformer" or embedder is None:
        return candidates

    texts = [query] + [r.get("content", "") for r in candidates]
    try:
        vecs = embedder.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        qvec = vecs[0]
        dvecs = vecs[1:]
        scores = dvecs @ qvec
        for r, score in zip(candidates, scores):
            r["rerank_score"] = round(float(score), 4)
        # 综合得分：70% rerank + 30% rrf（都已归一化到 0~1 附近）
        for r in candidates:
            r["final_score"] = round(r.get("rerank_score", 0) * 0.7 + r.get("rrf_score", 0) * 0.3, 4)
        candidates.sort(key=lambda x: -x["final_score"])
    except Exception as e:
        logger.warning("rerank 失败：%s", e)
    return candidates


@timed("retrieval.hybrid_search")
def hybrid_search(query: str, entity_type: Optional[str] = None,
                  top_k: int = 10, vec_k: int = 20, rerank: bool = True) -> List[Dict]:
    """对外暴露的混合检索接口。"""
    key = _cache_key(query, entity_type, top_k, vec_k, rerank)
    cached = _get_cached(key)
    if cached is not None:
        return cached

    fts_hits = search_documents(query, entity_type=entity_type, limit=top_k * 2)
    vdb = _get_vector_db()
    vec_hits = vdb.search(query, entity_type=entity_type, top_k=vec_k)
    fused = reciprocal_rank_fusion(fts_hits, vec_hits, top_k=max(top_k, 20))
    if rerank:
        fused = _embedding_rerank(query, fused)
    result = fused[:top_k]
    _set_cached(key, result)
    return result
