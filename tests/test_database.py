"""测试 SQLite 数据层。"""
import pytest

from pewm.processors.database import (
    add_conversation_message,
    add_document,
    get_conversation_history,
    get_document,
    get_stats,
    hard_delete_document,
    init_db,
    list_documents,
    soft_delete_document,
)


def test_init_and_add_document(temp_project):
    init_db()
    add_document("term", "RAG", "RAG 是检索增强生成", "test.md", "20-Ontology/dictionary/rag.md")
    doc = get_document("20-Ontology/dictionary/rag.md")
    assert doc is not None
    assert doc["entity_type"] == "term"


def test_soft_delete(temp_project):
    init_db()
    add_document("term", "RAG", "RAG 是检索增强生成", "test.md", "20-Ontology/dictionary/rag.md")
    assert soft_delete_document("20-Ontology/dictionary/rag.md") is True
    docs = list_documents(include_deleted=False)
    assert len(docs) == 0
    docs = list_documents(include_deleted=True)
    assert len(docs) == 1


def test_conversation_history(temp_project):
    init_db()
    add_conversation_message("s1", "user", "hello")
    add_conversation_message("s1", "assistant", "hi")
    history = get_conversation_history("s1")
    assert len(history) == 2
    assert history[0]["role"] == "user"
