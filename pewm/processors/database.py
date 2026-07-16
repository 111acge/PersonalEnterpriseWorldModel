"""SQLite 数据层。

所有数据保存在项目根目录的 data/world-model.db 中。
"""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pewm.paths as paths
from pewm.processors.log_config import get_logger

logger = get_logger(__name__)


_thread_local = threading.local()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def db_connection():
    """线程安全的 SQLite 连接上下文管理器。

    同一线程内复用同一连接，避免频繁 open/close；不同线程各自拥有连接。
    """
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(paths.DB_PATH))
        conn.row_factory = sqlite3.Row
        _thread_local.conn = conn
    try:
        yield conn
    except Exception:
        logger.exception("数据库事务回滚")
        conn.rollback()
        raise


def close_connection() -> None:
    """显式关闭当前线程的数据库连接。"""
    conn = getattr(_thread_local, "conn", None)
    if conn is not None:
        conn.close()
        _thread_local.conn = None


def _to_rel(path: str) -> str:
    """存储时优先使用相对路径，便于项目迁移。"""
    p = Path(path)
    if p.is_absolute():
        try:
            return str(p.relative_to(paths.ROOT))
        except ValueError:
            pass
    return path


def _to_abs(path: str) -> Path:
    """读取时把相对路径转回绝对路径。"""
    p = Path(path)
    if p.is_absolute():
        return p
    return paths.ROOT / p


def init_db() -> None:
    """初始化数据库表和 FTS5 索引。"""
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inbox (
                path TEXT PRIMARY KEY,
                mtime TEXT NOT NULL,
                processed_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                updated_at TEXT NOT NULL,
                deleted_at TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_session
            ON conversations(session_id, created_at)
        """)
        # 兼容旧库：如果旧表没有 deleted_at 字段就补上
        try:
            conn.execute("ALTER TABLE documents ADD COLUMN deleted_at TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        # FTS5 全文索引
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_documents USING fts5(
                title, content,
                content='documents',
                content_rowid='id'
            )
        """)
        # 触发器：保持 FTS 索引同步
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                INSERT INTO fts_documents(rowid, title, content)
                VALUES (NEW.id, NEW.title, NEW.content);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                INSERT INTO fts_documents(fts_documents, rowid, title, content)
                VALUES ('delete', OLD.id, OLD.title, OLD.content);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents
            WHEN OLD.title != NEW.title OR OLD.content != NEW.content
            BEGIN
                INSERT INTO fts_documents(fts_documents, rowid, title, content)
                VALUES ('delete', OLD.id, OLD.title, OLD.content);
                INSERT INTO fts_documents(rowid, title, content)
                VALUES (NEW.id, NEW.title, NEW.content);
            END
        """)
        conn.commit()


def is_inbox_processed(path: str, mtime: str) -> bool:
    """检查 Inbox 文件是否已处理且未修改。"""
    with db_connection() as conn:
        row = conn.execute(
            "SELECT mtime FROM inbox WHERE path = ?", (_to_rel(path),)
        ).fetchone()
        return row is not None and row["mtime"] == mtime


def mark_inbox_processed(path: str, mtime: str) -> None:
    """标记 Inbox 文件已处理。"""
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO inbox (path, mtime, processed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                mtime = excluded.mtime,
                processed_at = excluded.processed_at
            """,
            (_to_rel(path), mtime, now_iso()),
        )
        conn.commit()


def load_processed() -> Dict[str, str]:
    """返回 {path: mtime} 映射。"""
    with db_connection() as conn:
        rows = conn.execute("SELECT path, mtime FROM inbox").fetchall()
        return {row["path"]: row["mtime"] for row in rows}


def add_document(entity_type: str, title: str, content: str, source: str, path: str) -> None:
    """添加或更新知识文档。更新时自动把 deleted_at 置空（恢复为未删除状态）。"""
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO documents (entity_type, title, content, source, path, updated_at, deleted_at)
            VALUES (?, ?, ?, ?, ?, ?, '')
            ON CONFLICT(path) DO UPDATE SET
                entity_type = excluded.entity_type,
                title = excluded.title,
                content = excluded.content,
                source = excluded.source,
                updated_at = excluded.updated_at,
                deleted_at = ''
            """,
            (entity_type, title, content, _to_rel(source), _to_rel(path), now_iso()),
        )
        conn.commit()


def soft_delete_document(path: str) -> bool:
    """软删除：标记 deleted_at 为当前时间，从 FTS 索引中移除，但保留原文档。"""
    with db_connection() as conn:
        row = conn.execute(
            "SELECT id FROM documents WHERE path = ? AND (deleted_at IS NULL OR deleted_at = '')",
            (_to_rel(path),),
        ).fetchone()
        if not row:
            return False
        ts = now_iso()
        conn.execute(
            "UPDATE documents SET deleted_at = ? WHERE id = ?",
            (ts, row["id"]),
        )
        conn.execute(
            "INSERT INTO fts_documents(fts_documents, rowid, title, content) "
            "SELECT 'delete', id, title, content FROM documents WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
        return True


def restore_document(path: str) -> bool:
    """恢复软删除的文档：把 deleted_at 置空，UPDATE 触发器会自动同步 FTS 索引。"""
    with db_connection() as conn:
        row = conn.execute(
            "SELECT id FROM documents WHERE path = ? AND deleted_at != ''",
            (_to_rel(path),),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE documents SET deleted_at = '' WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
        return True


def hard_delete_document(path: str) -> bool:
    """硬删除：永久抹掉记录。DELETE 触发器会自动从 FTS 索引中移除。"""
    with db_connection() as conn:
        cur = conn.execute("DELETE FROM documents WHERE path = ?", (_to_rel(path),))
        conn.commit()
        return cur.rowcount > 0


def list_documents(include_deleted: bool = False,
                   entity_type: str = None,
                   limit: int = 1000) -> List[Dict]:
    """列出所有文档（默认只返回未删除的）。"""
    with db_connection() as conn:
        where = []
        params = []
        if not include_deleted:
            where.append("(deleted_at IS NULL OR deleted_at = '')")
        if entity_type:
            where.append("entity_type = ?")
            params.append(entity_type)
        sql = "SELECT id, entity_type, title, source, path, updated_at, deleted_at FROM documents"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_document(path: str) -> Optional[Dict]:
    """按 path 精确获取单篇文档（含已删除的）。"""
    with db_connection() as conn:
        row = conn.execute(
            "SELECT id, entity_type, title, content, source, path, updated_at, deleted_at "
            "FROM documents WHERE path = ?",
            (_to_rel(path),),
        ).fetchone()
        return dict(row) if row else None


def search_documents(query: str, entity_type: Optional[str] = None,
                     limit: int = 10) -> List[Dict]:
    """检索知识文档。优先使用 LIKE 对中文友好，FTS5 作为补充。自动跳过软删除的文档。"""
    like_results = _search_like(query, entity_type, limit)
    like_ids = {r["id"] for r in like_results}

    try:
        safe_query = " ".join(query.split())
        if safe_query:
            sql = """
                SELECT d.id, d.entity_type, d.title, d.content, d.source, d.path, d.updated_at
                FROM fts_documents f
                JOIN documents d ON d.id = f.rowid
                WHERE fts_documents MATCH ? AND (d.deleted_at IS NULL OR d.deleted_at = '')
            """
            params = [safe_query]
            if entity_type:
                sql += " AND d.entity_type = ?"
                params.append(entity_type)
            sql += " LIMIT ?"
            params.append(limit)

            with db_connection() as conn:
                rows = conn.execute(sql, params).fetchall()
                for row in rows:
                    row_dict = dict(row)
                    if row_dict["id"] not in like_ids:
                        like_results.append(row_dict)
                        like_ids.add(row_dict["id"])
    except sqlite3.OperationalError:
        pass

    return like_results[:limit]


def _search_like(query: str, entity_type: Optional[str], limit: int) -> List[Dict]:
    with db_connection() as conn:
        search_terms = set()
        parts = [t.strip() for t in query.split() if len(t.strip()) >= 2]
        for part in parts:
            search_terms.add(part)
        clean_query = query.replace(" ", "").strip()
        if len(clean_query) >= 2:
            search_terms.add(clean_query)
        for part in list(search_terms):
            if len(part) >= 3:
                for i in range(len(part) - 1):
                    search_terms.add(part[i:i+2])

        search_terms = [t for t in search_terms if len(t) >= 2]
        if not search_terms:
            return []

        where_clauses = []
        params = []
        for term in search_terms:
            where_clauses.append("(title LIKE ? OR content LIKE ?)")
            params.extend([f"%{term}%", f"%{term}%"])

        sql = f"""
            SELECT id, entity_type, title, content, source, path, updated_at
            FROM documents
            WHERE ({' OR '.join(where_clauses)}) AND (deleted_at IS NULL OR deleted_at = '')
        """
        if entity_type:
            sql += " AND entity_type = ?"
            params.append(entity_type)
        sql += " LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_stats() -> Dict:
    """返回数据库统计信息（区分未删除/已删除文档）。"""
    with db_connection() as conn:
        inbox_total = conn.execute("SELECT COUNT(*) AS c FROM inbox").fetchone()["c"]
        doc_count = conn.execute(
            "SELECT COUNT(*) AS c FROM documents WHERE deleted_at IS NULL OR deleted_at = ''"
        ).fetchone()["c"]
        deleted_count = conn.execute(
            "SELECT COUNT(*) AS c FROM documents WHERE deleted_at IS NOT NULL AND deleted_at != ''"
        ).fetchone()["c"]
        return {
            "inbox_total": inbox_total,
            "document_count": doc_count,
            "deleted_count": deleted_count,
            "db_path": str(paths.DB_PATH),
        }


def add_conversation_message(session_id: str, role: str, content: str) -> None:
    """保存一条对话消息。"""
    with db_connection() as conn:
        conn.execute(
            "INSERT INTO conversations (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, now_iso()),
        )
        conn.commit()


def get_conversation_history(session_id: str, limit: int = 20) -> List[Dict]:
    """获取最近 N 条对话历史。"""
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM conversations "
            "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def clear_conversation_history(session_id: str) -> None:
    """清空某会话的历史。"""
    with db_connection() as conn:
        conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
        conn.commit()
