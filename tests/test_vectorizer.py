"""向量索引模块测试。"""
from unittest.mock import patch

from pewm.processors.vectorizer import index_documents


def test_index_documents_with_empty_list(temp_project):
    """空文档列表不应抛异常。"""
    index_documents([])


def test_index_documents_adds_to_vector_db(temp_project):
    """应同时写入 FTS5 与向量库。"""
    from pewm.processors.database import init_db
    init_db()
    docs = [
        {
            "source": "inbox/test.md",
            "entity_type": "note",
            "path": "/path/test.md",
            "content": "这是一个测试文档",
        }
    ]
    with patch("pewm.processors.vectorizer.VectorDB") as MockVDB:
        instance = MockVDB.return_value
        instance.add_batch = lambda batch: None
        index_documents(docs)
        # 至少被实例化一次
        MockVDB.assert_called_once()


def test_index_documents_vector_failure_ignored(temp_project):
    """向量索引失败不应中断 FTS5 索引。"""
    from pewm.processors.database import init_db
    init_db()
    docs = [
        {
            "source": "inbox/test.md",
            "entity_type": "note",
            "path": "/path/test.md",
            "content": "测试",
        }
    ]
    with patch("pewm.processors.vectorizer.VectorDB") as MockVDB:
        instance = MockVDB.return_value
        instance.add_batch.side_effect = RuntimeError("model failed")
        # 不应抛异常
        index_documents(docs)
        MockVDB.assert_called_once()
