"""SQLite 数据层：替代 processed.json 与 ChromaDB 向量索引。

所有数据保存在项目根目录的 data/world-model.db 中。
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def _resolve_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


ROOT = _resolve_root()
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "world-model.db"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """初始化数据库表和 FTS5 索引。"""
    conn = get_connection()
    try:
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
        # 兼容旧库：如果旧表没有 deleted_at 字段就补上
        try:
            conn.execute("ALTER TABLE documents ADD COLUMN deleted_at TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 字段已存在
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
            CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                INSERT INTO fts_documents(fts_documents, rowid, title, content)
                VALUES ('delete', OLD.id, OLD.title, OLD.content);
                INSERT INTO fts_documents(rowid, title, content)
                VALUES (NEW.id, NEW.title, NEW.content);
            END
        """)
        conn.commit()
    finally:
        conn.close()


def is_inbox_processed(path: str, mtime: str) -> bool:
    """检查 Inbox 文件是否已处理且未修改。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT mtime FROM inbox WHERE path = ?", (path,)
        ).fetchone()
        return row is not None and row["mtime"] == mtime
    finally:
        conn.close()


def mark_inbox_processed(path: str, mtime: str) -> None:
    """标记 Inbox 文件已处理。"""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO inbox (path, mtime, processed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                mtime = excluded.mtime,
                processed_at = excluded.processed_at
            """,
            (path, mtime, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def load_processed() -> Dict[str, str]:
    """返回 {path: mtime} 映射。"""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT path, mtime FROM inbox").fetchall()
        return {row["path"]: row["mtime"] for row in rows}
    finally:
        conn.close()


def add_document(entity_type: str, title: str, content: str, source: str, path: str) -> None:
    """添加或更新知识文档。更新时自动把 deleted_at 置空（恢复为未删除状态）。"""
    conn = get_connection()
    try:
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
            (entity_type, title, content, source, path, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def soft_delete_document(path: str) -> bool:
    """软删除：标记 deleted_at 为当前时间，从 FTS 索引中移除，但保留原文档。

    返回 True 表示确实有文档被标记；如果已是软删除状态则返回 False（避免重复发 FTS delete）。
    """
    conn = get_connection()
    try:
        # 只处理 deleted_at 为空的记录，避免重复向 FTS 发 delete 命令
        row = conn.execute(
            "SELECT id FROM documents WHERE path = ? AND (deleted_at IS NULL OR deleted_at = '')",
            (path,),
        ).fetchone()
        if not row:
            return False
        ts = now_iso()
        conn.execute(
            "UPDATE documents SET deleted_at = ? WHERE id = ?",
            (ts, row["id"]),
        )
        # 手动从 FTS 索引中移除（DELETE 触发器不会在 UPDATE 时触发）
        conn.execute(
            "INSERT INTO fts_documents(fts_documents, rowid, title, content) "
            "SELECT 'delete', id, title, content FROM documents WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def restore_document(path: str) -> bool:
    """恢复软删除的文档：把 deleted_at 置空，重新加入 FTS 索引。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, title, content FROM documents WHERE path = ? AND deleted_at != ''",
            (path,),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE documents SET deleted_at = '' WHERE id = ?",
            (row["id"],),
        )
        conn.execute(
            "INSERT INTO fts_documents(rowid, title, content) VALUES (?, ?, ?)",
            (row["id"], row["title"], row["content"]),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def hard_delete_document(path: str) -> bool:
    """硬删除：永久抹掉记录。DELETE 触发器会自动从 FTS 索引中移除。"""
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM documents WHERE path = ?", (path,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_documents(include_deleted: bool = False,
                   entity_type: str = None,
                   limit: int = 1000) -> List[Dict]:
    """列出所有文档（默认只返回未删除的）。"""
    conn = get_connection()
    try:
        where = []
        params = []
        if not include_deleted:
            where.append("(deleted_at IS NULL OR deleted_at = '')")
        else:
            # 返回所有（含已删除）
            pass
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
    finally:
        conn.close()


def get_document(path: str) -> Optional[Dict]:
    """按 path 精确获取单篇文档（含已删除的）。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, entity_type, title, content, source, path, updated_at, deleted_at "
            "FROM documents WHERE path = ?",
            (path,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def search_documents(query: str, entity_type: Optional[str] = None,
                     limit: int = 10) -> List[Dict]:
    """检索知识文档。优先使用 LIKE 对中文友好，FTS5 作为补充。自动跳过软删除的文档。"""
    like_results = _search_like(query, entity_type, limit)
    like_ids = {r["id"] for r in like_results}

    # 再尝试 FTS5 补充结果
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

            conn = get_connection()
            try:
                rows = conn.execute(sql, params).fetchall()
                for row in rows:
                    row_dict = dict(row)
                    if row_dict["id"] not in like_ids:
                        like_results.append(row_dict)
                        like_ids.add(row_dict["id"])
            finally:
                conn.close()
    except sqlite3.OperationalError:
        pass

    return like_results[:limit]


def _search_like(query: str, entity_type: Optional[str], limit: int) -> List[Dict]:
    conn = get_connection()
    try:
        # 收集搜索词：空格分词 + 整句 + 2-gram
        search_terms = set()
        parts = [t.strip() for t in query.split() if len(t.strip()) >= 2]
        for part in parts:
            search_terms.add(part)
        # 整句也加入
        clean_query = query.replace(" ", "").strip()
        if len(clean_query) >= 2:
            search_terms.add(clean_query)
        # 2-gram 覆盖中文短词
        for part in list(search_terms):
            if len(part) >= 3:
                for i in range(len(part) - 1):
                    search_terms.add(part[i:i+2])

        search_terms = [t for t in search_terms if len(t) >= 2]
        if not search_terms:
            return []

        # OR 语义：匹配任意一个词即返回
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
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_stats() -> Dict:
    """返回数据库统计信息（区分未删除/已删除文档）。"""
    conn = get_connection()
    try:
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
            "db_path": str(DB_PATH),
        }
    finally:
        conn.close()
