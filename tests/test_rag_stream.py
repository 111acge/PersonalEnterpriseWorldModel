"""RAG 流式输出测试。"""
from unittest.mock import patch

import pytest

from pewm.processors.database import init_db
from pewm.processors.rag import rag_answer_stream


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
    # 未传问题时应返回 400
    resp = client.post('/api/chat/stream', json={})
    assert resp.status_code == 400
