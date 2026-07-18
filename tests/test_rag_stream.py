"""RAG 流式输出测试。"""
from unittest.mock import patch

import pytest

from pewm.processors.database import init_db
from pewm.processors.rag import rag_answer, rag_answer_stream


def test_rag_answer_stream_no_api_fallback(temp_project):
    """未配置 API 时，流式接口应直接返回检索结果或空回复。"""
    init_db()
    with patch("pewm.processors.rag.load_config", return_value={"api_key": ""}):
        chunks = list(rag_answer_stream("test query"))
    assert len(chunks) >= 1
    final = chunks[-1]
    assert final["done"] is True
    assert final["mode"] in ("no_api", "retrieval_only")


def test_rag_answer_stream_yields_done(temp_project):
    """流式接口最后一个 chunk 必须包含 done=True。"""
    chunks = list(rag_answer_stream("test query"))
    assert chunks[-1]["done"] is True


def test_rag_stream_endpoint_exists(temp_project):
    """Flask 应注册 /api/chat/stream 端点。"""
    from pewm.web.app import create_app
    app = create_app()
    client = app.test_client()
    # #15 修复后 /api/* 需要访问令牌，先取 token
    token = client.get('/api/auth/token').get_json()["data"]["token"]
    headers = {"X-Token": token}
    # 未传问题时应返回 400
    resp = client.post('/api/chat/stream', json={}, headers=headers)
    assert resp.status_code == 400


def test_rag_answer_no_api_fallback(temp_project):
    """非流式接口在未配置 API 时应返回检索结果。"""
    init_db()
    with patch("pewm.processors.rag.load_config", return_value={"api_key": ""}):
        result = rag_answer("test query")
    assert result["mode"] in ("no_api", "retrieval_only")


def test_rag_answer_sources_are_paths(temp_project):
    """RAG 返回的 sources 应为文档路径列表。"""
    init_db()
    with patch("pewm.processors.rag.load_config", return_value={"api_key": ""}):
        result = rag_answer("test query")
    assert isinstance(result.get("sources"), list)


def test_rag_answer_retrieval_error(temp_project):
    """检索异常时应返回 mode='retrieval_error' 而非伪装空库（#42）。"""
    init_db()
    with patch("pewm.processors.rag.hybrid_search", side_effect=RuntimeError("boom")):
        result = rag_answer("test query")
    assert result["mode"] == "retrieval_error"
    assert "boom" in result["answer"]
    assert result["sources"] == []


def test_rag_answer_stream_retrieval_error(temp_project):
    """流式接口在检索异常时应产出 retrieval_error 且 done=True（#42）。"""
    init_db()
    with patch("pewm.processors.rag.hybrid_search", side_effect=RuntimeError("boom")):
        chunks = list(rag_answer_stream("test query"))
    assert chunks[-1]["mode"] == "retrieval_error"
    assert chunks[-1]["done"] is True
