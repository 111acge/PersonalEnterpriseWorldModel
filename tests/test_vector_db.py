"""测试向量库。"""
import pytest

from pewm.processors.vector_db import VectorDB


def test_vector_db_add_and_search(temp_project):
    vdb = VectorDB()
    vdb.add("test/a.md", "term", "RAG 叫检索增强生成，是一种结合检索与生成的技术")
    vdb.add("test/b.md", "case", "订单服务 OOM 故障，根因是连接池泄漏")
    results = vdb.search("检索增强", top_k=5)
    assert len(results) > 0
    paths = [r["path"] for r in results]
    assert "test/a.md" in paths


def test_vector_db_soft_delete(temp_project):
    vdb = VectorDB()
    vdb.add("test/a.md", "term", "RAG 叫检索增强生成")
    assert vdb.soft_delete("test/a.md") is True
    results = vdb.search("检索增强", top_k=5)
    paths = [r["path"] for r in results]
    assert "test/a.md" not in paths
