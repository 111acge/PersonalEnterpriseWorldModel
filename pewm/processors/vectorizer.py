"""SQLite 全文索引 + 向量索引管理。"""
from typing import Dict, List

from pewm.processors.database import add_document, db_connection
from pewm.processors.vector_db import VectorDB


def extract_title(content: str) -> str:
    """从 Markdown 内容中提取标题（第一行 # 标题）。"""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return content[:40].strip() or "未命名"


def index_documents(documents: List[Dict], build_vector: bool = True) -> None:
    """将文档同时索引到 SQLite FTS5 和向量库。"""
    if not documents:
        print("[info] 没有文档需要索引。")
        return

    # 1. FTS5 索引
    for doc in documents:
        title = extract_title(doc["content"])
        add_document(
            entity_type=doc["entity_type"],
            title=title,
            content=doc["content"],
            source=doc["source"],
            path=str(doc["path"]),
        )
    print(f"[info] 已索引 {len(documents)} 个文档到 SQLite FTS5。")

    # 2. 向量索引（可选，首次运行会下载 embedding 模型）
    if build_vector:
        try:
            vdb = VectorDB()
            batch = [
                (str(doc["path"]), doc["entity_type"], doc["content"])
                for doc in documents
            ]
            vdb.add_batch(batch)
            print(f"[info] 已写入 {len(documents)} 个文档到向量库。")
        except Exception as e:
            print(f"[warn] 向量索引失败（不影响 FTS5）: {e}")


def rebuild_vector() -> None:
    """重建全部向量索引（强制重新计算所有向量）。"""
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT entity_type, content, path FROM documents"
        ).fetchall()
    vdb = VectorDB()
    vdb.docs = []
    vdb.vectors = None
    for row in rows:
        vdb.add(
            path=row["path"],
            entity_type=row["entity_type"],
            content=row["content"],
        )
    print(f"[info] 已重建 {len(rows)} 条向量索引。")
