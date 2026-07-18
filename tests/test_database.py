"""测试 SQLite 数据层。"""
import pytest

import pewm.processors.database as db_mod
from pewm.processors.database import (
    add_conversation_message,
    add_document,
    db_connection,
    get_conversation_history,
    get_document,
    get_stats,
    hard_delete_document,
    init_db,
    list_documents,
    rebuild_fts,
    restore_document,
    search_documents,
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


def test_restore_document(temp_project):
    init_db()
    add_document("term", "RAG", "RAG 是检索增强生成", "test.md", "20-Ontology/dictionary/rag.md")
    soft_delete_document("20-Ontology/dictionary/rag.md")
    assert restore_document("20-Ontology/dictionary/rag.md") is True
    docs = list_documents(include_deleted=False)
    assert len(docs) == 1


def test_hard_delete_document(temp_project):
    init_db()
    add_document("term", "RAG", "RAG 是检索增强生成", "test.md", "20-Ontology/dictionary/rag.md")
    assert hard_delete_document("20-Ontology/dictionary/rag.md") is True
    assert get_document("20-Ontology/dictionary/rag.md") is None


def test_get_document_nonexistent(temp_project):
    init_db()
    assert get_document("no-such-doc.md") is None


def test_search_documents(temp_project):
    init_db()
    add_document("term", "RAG", "RAG 是检索增强生成", "test.md", "20-Ontology/dictionary/rag.md")
    results = search_documents("RAG")
    assert len(results) >= 1


def test_get_stats(temp_project):
    init_db()
    add_document("term", "RAG", "RAG 是检索增强生成", "test.md", "20-Ontology/dictionary/rag.md")
    stats = get_stats()
    assert stats["document_count"] >= 1


def test_conversation_history(temp_project):
    init_db()
    add_conversation_message("s1", "user", "hello")
    add_conversation_message("s1", "assistant", "hi")
    history = get_conversation_history("s1")
    assert len(history) == 2
    assert history[0]["role"] == "user"


def test_soft_delete_then_hard_delete_no_crash(temp_project):
    """软删后再硬删不应触发 FTS 损坏 "database disk image is malformed"（#3）。"""
    init_db()
    add_document("term", "Alpha", "hello world alpha", "test.md", "docs/alpha.md")
    assert soft_delete_document("docs/alpha.md") is True
    # 软删后 FTS 索引保留，但检索结果过滤
    assert all(r["path"] != "docs/alpha.md" for r in search_documents("hello"))
    assert hard_delete_document("docs/alpha.md") is True
    assert get_document("docs/alpha.md") is None
    # AFTER DELETE 触发器已同步清理 FTS
    with db_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM fts_documents WHERE fts_documents MATCH '\"hello\"'"
        ).fetchone()
    assert row is None
    # 库仍然健康，可以继续写入检索
    add_document("term", "Alpha2", "hello world again", "test.md", "docs/alpha2.md")
    assert any(r["path"] == "docs/alpha2.md" for r in search_documents("hello"))


def test_restore_then_fts_hit(temp_project):
    """恢复软删文档后 FTS 可命中（#10）。"""
    init_db()
    add_document("term", "Beta", "hello world beta", "test.md", "docs/beta.md")
    soft_delete_document("docs/beta.md")
    assert all(r["path"] != "docs/beta.md" for r in search_documents("hello"))
    assert restore_document("docs/beta.md") is True
    assert any(r["path"] == "docs/beta.md" for r in search_documents("hello"))
    # 直接断言 FTS 索引中可见
    with db_connection() as conn:
        row = conn.execute(
            "SELECT rowid FROM fts_documents WHERE fts_documents MATCH '\"beta\"'"
        ).fetchone()
    assert row is not None


def test_rebuild_fts(temp_project):
    """rebuild_fts 维护函数可重建索引且不破坏现有数据（#3）。"""
    init_db()
    add_document("term", "Gamma", "hello gamma content", "test.md", "docs/gamma.md")
    rebuild_fts()
    with db_connection() as conn:
        row = conn.execute(
            "SELECT rowid FROM fts_documents WHERE fts_documents MATCH '\"gamma\"'"
        ).fetchone()
    assert row is not None
    assert any(r["path"] == "docs/gamma.md" for r in search_documents("hello"))


def test_search_like_ordered_by_updated_desc(temp_project, monkeypatch):
    """LIKE 检索应按 updated_at DESC 排序（#14）。"""
    init_db()
    times = iter(["2026-01-01T00:00:00", "2026-01-02T00:00:00"])
    monkeypatch.setattr(db_mod, "now_iso", lambda: next(times))
    add_document("term", "shared keyword one", "shared keyword alpha", "s.md", "docs/old.md")
    add_document("term", "shared keyword two", "shared keyword beta", "s.md", "docs/new.md")
    results = db_mod._search_like("shared keyword", None, 10)
    assert len(results) == 2
    assert results[0]["path"] == "docs/new.md"


def test_fts_returns_bm25_score(temp_project):
    """FTS 检索应返回 bm25 score（#14）。"""
    init_db()
    add_document("term", "bravo title", "bravo content", "s.md", "docs/bravo.md")
    hits = db_mod._search_fts("bravo", None, 10)
    assert len(hits) == 1
    assert "score" in hits[0]
    assert hits[0]["path"] == "docs/bravo.md"


def test_fts_query_escaped_no_crash(temp_project):
    """含引号/FTS 语法符号的查询不应抛错或注入（#36）。"""
    init_db()
    add_document("term", "T", 'content with "quotes" inside', "s.md", "docs/t.md")
    for nasty in ['"quotes"', '"unclosed', 'OR AND NEAR', '"a" OR "b" (', '***']:
        results = search_documents(nasty)
        assert isinstance(results, list)


def test_wal_and_busy_timeout_enabled(temp_project):
    """建连后应开启 WAL 与 busy_timeout（#39）。"""
    init_db()
    with db_connection() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert str(mode).lower() == "wal"
    assert busy == 10000
