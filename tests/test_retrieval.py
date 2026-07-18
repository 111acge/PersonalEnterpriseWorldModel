"""测试 RRF 混合检索。"""
import pytest

import pewm.processors.retrieval as retrieval_mod
from pewm.processors.retrieval import reciprocal_rank_fusion


def test_rrf_basic():
    fts = [
        {"path": "a.md", "content": "abc", "title": "A", "score": 1.0},
        {"path": "b.md", "content": "def", "title": "B", "score": 0.8},
    ]
    vec = [
        {"path": "b.md", "content": "def", "title": "B", "score": 0.95},
        {"path": "c.md", "content": "ghi", "title": "C", "score": 0.7},
    ]
    results = reciprocal_rank_fusion(fts, vec, top_k=10)
    paths = [r["path"] for r in results]
    assert "a.md" in paths
    assert "b.md" in paths
    assert "c.md" in paths
    # b 在两个列表都出现，应该排第一
    assert paths[0] == "b.md"


def test_rrf_empty():
    results = reciprocal_rank_fusion([], [])
    assert results == []


def test_hybrid_search_uses_process_singleton(temp_project):
    """hybrid_search 应复用进程级 VectorDB 单例，写操作后惰性 refresh（#38）。"""
    from pewm.processors.database import add_document, init_db
    from pewm.processors.retrieval import hybrid_search, on_vector_store_changed
    from pewm.processors.vector_db import VectorDB

    init_db()
    add_document("note", "单例", "单例测试文档", "s.md", "single.md")
    vdb = VectorDB()
    vdb.add("single.md", "note", "单例测试文档")

    retrieval_mod.invalidate_search_cache()
    hybrid_search("单例", top_k=5)
    inst1 = retrieval_mod._vdb_holder["instance"]
    assert inst1 is not None
    # 第二次检索复用同一实例（不新建、不全量加载）
    hybrid_search("单例其他", top_k=5)
    assert retrieval_mod._vdb_holder["instance"] is inst1

    # 向量库写操作把单例标记为过期，下次检索时 refresh 而非新建
    vdb.add("single2.md", "note", "第二篇单例测试文档")
    assert retrieval_mod._vdb_holder["stale"] is True
    results = hybrid_search("第二篇", top_k=5)
    assert retrieval_mod._vdb_holder["instance"] is inst1
    assert retrieval_mod._vdb_holder["stale"] is False
    assert any(r["path"] == "single2.md" for r in results)


def test_on_vector_store_changed_clears_cache(temp_project):
    """写钩子应同时清查询缓存并标记单例过期（#11/#38）。"""
    retrieval_mod._set_cached(("q", "", 5, 10, True), [{"path": "x"}])
    retrieval_mod.on_vector_store_changed()
    assert retrieval_mod._search_cache == {}
    assert retrieval_mod._vdb_holder["stale"] is True
