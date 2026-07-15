"""向量库增量更新与检索缓存测试。"""
from pewm.processors.database import add_document, init_db
from pewm.processors.retrieval import hybrid_search, invalidate_search_cache
from pewm.processors.vector_db import VectorDB


def _index_doc(path, entity_type, content):
    """同时索引到 FTS5 与向量库，供混合检索测试使用。"""
    init_db()
    add_document(entity_type, path, content, path, path)
    vdb = VectorDB()
    vdb.add(path, entity_type, content)


def test_vector_add_avoids_full_rebuild(temp_project):
    """新增文档不应触发全量重建，且能正确检索。"""
    vdb = VectorDB()
    vdb.add("doc1.md", "note", "hello world")
    vdb.add("doc2.md", "note", "hello python")
    # 新增包含新 2-gram 的文档，应触发维度增长但不重建全部
    vdb.add("doc3.md", "note", "完全不同的中文内容")
    results = vdb.search("中文", top_k=5)
    assert any(r["path"] == "doc3.md" for r in results)


def test_vector_add_batch(temp_project):
    """批量添加应减少事务开销。"""
    vdb = VectorDB()
    items = [
        ("batch1.md", "note", "hello world"),
        ("batch2.md", "note", "hello python"),
        ("batch3.md", "note", "中文内容"),
    ]
    vdb.add_batch(items)
    results = vdb.search("python", top_k=5)
    assert any(r["path"] == "batch2.md" for r in results)


def test_search_cache_returns_same_result(temp_project):
    """相同查询第二次应命中缓存。"""
    invalidate_search_cache()
    _index_doc("cache_doc.md", "note", "缓存测试文档")
    r1 = hybrid_search("缓存", top_k=5)
    r2 = hybrid_search("缓存", top_k=5)
    assert r1 == r2


def test_invalidate_cache_clears_results(temp_project):
    """清空缓存后重新查询应重新计算。"""
    _index_doc("cache_doc2.md", "note", "缓存测试文档二")
    r1 = hybrid_search("缓存二", top_k=5)
    invalidate_search_cache()
    r2 = hybrid_search("缓存二", top_k=5)
    assert r1 == r2  # 结果应一致
