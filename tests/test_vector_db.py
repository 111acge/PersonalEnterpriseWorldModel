"""测试向量库。"""
from pathlib import Path

import numpy as np
import pytest

import pewm.paths as paths
import pewm.processors.vector_db as vdb_mod
from pewm.processors.vector_db import VectorDB


def test_vector_db_add_and_search(temp_project):
    vdb = VectorDB()
    vdb.add("test/a.md", "term", "RAG 叫检索增强生成，是一种结合检索与生成的技术")
    vdb.add("test/b.md", "case", "订单服务 OOM 故障，根因是连接池泄漏")
    results = vdb.search("检索增强", top_k=5)
    assert len(results) > 0
    paths_ = [r["path"] for r in results]
    assert "test/a.md" in paths_


def test_vector_db_soft_delete(temp_project):
    vdb = VectorDB()
    vdb.add("test/a.md", "term", "RAG 叫检索增强生成")
    assert vdb.soft_delete("test/a.md") is True
    results = vdb.search("检索增强", top_k=5)
    paths_ = [r["path"] for r in results]
    assert "test/a.md" not in paths_


def test_tfidf_vocab_continuous_indices(temp_project, monkeypatch):
    """TF-IDF vocab 值必须是连续列下标，IDF 另存字典（#12）。"""
    # 强制 TF-IDF 回退模式（本环境装有 sentence-transformers 时会默认走 transformer）
    monkeypatch.setattr(vdb_mod, "_load_embedder", lambda: (None, "tfidf"))
    vdb = VectorDB()
    vdb.add("v1.md", "note", "检索增强生成 是一种架构")
    vdb.add("v2.md", "note", "连接池泄漏 导致内存溢出")
    assert vdb.kind == "tfidf"
    assert set(vdb.vocab.values()) == set(range(len(vdb.vocab)))
    assert isinstance(vdb._idf, dict) and vdb._idf
    # 非空文本向量 L2 范数 > 0
    norms = np.linalg.norm(vdb.vectors, axis=1)
    assert (norms > 0).all()


def test_vector_dim_shrink_compatible(temp_project):
    """新向量维度小于现有矩阵时右侧补零，不应崩溃（#13）。"""
    vdb = VectorDB()
    vdb.add("a.md", "note", "第一段测试内容")
    wide = vdb.vectors.shape[1]
    # 模拟旧矩阵维度大于新向量维度（如模型回退后）
    vdb.vectors = np.pad(vdb.vectors, ((0, 0), (0, 10)), mode="constant")
    vdb.add("b.md", "note", "第二段测试内容")
    assert vdb.vectors.shape == (2, wide + 10)
    results = vdb.search("第二段", top_k=5)
    assert any(r["path"] == "b.md" for r in results)


def test_embedder_kind_switch_triggers_reencode(temp_project, monkeypatch):
    """embedder 类型切换应记 warning 并全量重编码旧向量（#13）。"""
    # 先强制 TF-IDF 模式建立旧向量
    monkeypatch.setattr(vdb_mod, "_load_embedder", lambda: (None, "tfidf"))
    vdb = VectorDB()
    vdb.add("a.md", "note", "测试内容一")
    vdb.add("b.md", "note", "测试内容二")
    assert vdb.kind == "tfidf"

    class FakeEmbedder:
        def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
            return np.ones((len(texts), 8), dtype=np.float32)

    # 模拟切换为 transformer embedder
    monkeypatch.setattr(vdb_mod, "_load_embedder", lambda: (FakeEmbedder(), "transformer"))
    vdb.add("c.md", "note", "测试内容三")
    assert vdb.kind == "transformer"
    # 旧文档也被重编码为新维度
    assert vdb.vectors.shape == (3, 8)
    # 重编码已持久化
    vdb2 = VectorDB()
    assert vdb2.kind == "transformer"
    assert vdb2.vectors.shape == (3, 8)


def test_add_normalizes_absolute_path_and_migration(temp_project):
    """写入入口统一相对化；存量绝对路径在初始化时被迁移（#4）。"""
    vdb = VectorDB()
    abs_path = str(paths.ROOT / "10-Theory" / "abs.md")
    vdb.add(abs_path, "note", "绝对路径测试内容")
    stored = vdb.docs[0]["path"]
    assert not Path(stored).is_absolute()

    # 模拟存量库残留的绝对路径
    conn = vdb_mod._db_connection()
    conn.execute("UPDATE vectors SET path = ?", (abs_path,))
    conn.commit()

    # 重新初始化触发一次性迁移
    vdb2 = VectorDB()
    assert vdb2.docs[0]["path"] == stored
    # 迁移后可用相对路径完成软删/硬删
    assert vdb2.soft_delete(stored) is True
    assert vdb2.hard_delete(stored) is True


def test_index_documents_path_sets_consistent(temp_project):
    """documents 表与 vectors 表的 path 集合必须一致（#4）。"""
    from pewm.processors.database import init_db, list_documents
    from pewm.processors.vectorizer import index_documents

    init_db()
    docs = [
        {"entity_type": "note", "content": "# 测试一\n内容一",
         "source": "s1.md", "path": paths.ROOT / "10-Theory" / "a.md"},
        {"entity_type": "note", "content": "# 测试二\n内容二",
         "source": "s2.md", "path": paths.ROOT / "10-Theory" / "b.md"},
    ]
    index_documents(docs, build_vector=True)
    db_paths = {d["path"] for d in list_documents()}
    vdb = VectorDB()
    vec_paths = {d["path"] for d in vdb.list_docs()}
    assert db_paths == vec_paths
    assert all(not Path(p).is_absolute() for p in vec_paths)


def test_path_index_consistency(temp_project):
    """{path: index} 索引应与 docs 保持一致（#40）。"""
    vdb = VectorDB()
    vdb.add_batch([
        ("p1.md", "note", "内容一"),
        ("p2.md", "note", "内容二"),
    ])
    assert vdb._path_index == {"p1.md": 0, "p2.md": 1}
    vdb.hard_delete("p1.md")
    assert vdb._path_index == {"p2.md": 0}


def test_dead_module_aliases_removed(temp_project):
    """坏死的模块级 @property 别名已删除（#41）。"""
    for name in ("VECTOR_DIR", "DB_FILE", "MODEL_CACHE_DIR"):
        assert not hasattr(vdb_mod, name)


def test_load_embedder_cached(temp_project, monkeypatch):
    """embedder 加载决策（含 TF-IDF 回退）只执行一次，且有并发锁（#51）。"""
    assert hasattr(vdb_mod, "_EMBEDDER_LOCK")
    monkeypatch.setattr(vdb_mod, "_EMBEDDER", None)
    monkeypatch.setattr(vdb_mod, "_EMBEDDER_KIND", None)
    monkeypatch.setattr(vdb_mod, "_EMBEDDER_LOADED", False)
    e1, k1 = vdb_mod._load_embedder()
    assert vdb_mod._EMBEDDER_LOADED is True
    e2, k2 = vdb_mod._load_embedder()
    assert (e2, k2) == (e1, k1)


def test_vector_db_wal_and_busy_timeout(temp_project):
    """向量库连接应开启 WAL 与 busy_timeout（#39）。"""
    VectorDB()
    conn = vdb_mod._db_connection()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert str(mode).lower() == "wal"
    assert busy == 10000
