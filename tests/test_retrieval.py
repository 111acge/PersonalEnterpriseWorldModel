"""测试 RRF 混合检索。"""
import pytest

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
