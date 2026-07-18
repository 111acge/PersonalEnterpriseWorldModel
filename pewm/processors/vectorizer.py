"""SQLite 全文索引 + 向量索引管理。"""
from typing import Dict, List

from pewm.processors.database import _to_rel, add_document, db_connection
from pewm.processors.log_config import get_logger
from pewm.processors.vector_db import VectorDB

logger = get_logger(__name__)


def strip_frontmatter(content: str) -> str:
    """剥离实体文件头部的 YAML frontmatter 块，返回正文。"""
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            return content[end + 4:].lstrip("\r\n")
    return content


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
        logger.info("没有文档需要索引。")
        return

    # 1. FTS5 索引（剥离 frontmatter，仅索引正文）
    stripped = []
    for doc in documents:
        body = strip_frontmatter(doc["content"])
        stripped.append({**doc, "content": body})
        title = extract_title(body)
        add_document(
            entity_type=doc["entity_type"],
            title=title,
            content=body,
            source=doc["source"],
            path=str(doc["path"]),
        )
    logger.info("已索引 %d 个文档到 SQLite FTS5。", len(documents))

    # 2. 向量索引（可选，首次运行会下载 embedding 模型）
    if build_vector:
        try:
            vdb = VectorDB()
            # 与 documents 表保持相同的路径口径（相对路径），否则删除/去重会失效
            batch = [
                (_to_rel(str(doc["path"])), doc["entity_type"], doc["content"])
                for doc in stripped
            ]
            vdb.add_batch(batch)
            logger.info("已写入 %d 个文档到向量库。", len(documents))
        except Exception as e:
            logger.warning("向量索引失败（不影响 FTS5）: %s", e)


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
    logger.info("已重建 %d 条向量索引。", len(rows))
