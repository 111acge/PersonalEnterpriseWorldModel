"""轻量级向量数据库。

优先使用 sentence-transformers (bge-small-zh-v1.5) 做语义检索，
如未安装则自动回退到字符 2-gram TF-IDF + numpy 余弦相似度。

数据持久化在 data/vector/vectors.db（SQLite）。
"""
import hashlib
import json
import math
import re
import sqlite3
import sys
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

import pewm.paths as paths
from pewm.processors.log_config import get_logger
from pewm.processors.metrics import timed

logger = get_logger(__name__)


def _vector_dir() -> Path:
    return paths.ROOT / "data" / "vector"


def _db_file() -> Path:
    return _vector_dir() / "vectors.db"


def _model_cache_dir() -> Path:
    return _vector_dir() / "embedding_model"


# 兼容旧代码的模块级别名（惰性解析）
@property
def VECTOR_DIR() -> Path:
    return _vector_dir()


@property
def DB_FILE() -> Path:
    return _db_file()


@property
def MODEL_CACHE_DIR() -> Path:
    return _model_cache_dir()

# TF-IDF 模式下固定最大维度，避免新增文档触发全量重建
_MAX_TFIDF_DIM = 65536

_thread_local = threading.local()

# 全局单例：embedding 模型（懒加载，避免重复加载耗时）
_EMBEDDER = None
_EMBEDDER_KIND: Optional[str] = None  # "transformer" | "tfidf"


def _resource_path(*parts: str) -> Path:
    """返回资源文件的绝对路径。PyInstaller 打包后从 sys._MEIPASS 解析，源码模式从 ROOT 解析。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS).joinpath(*parts)
    return ROOT.joinpath(*parts)


def _db_connection():
    """获取线程本地 SQLite 连接。"""
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        _vector_dir().mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_db_file()))
        conn.row_factory = sqlite3.Row
        _thread_local.conn = conn
    return conn


def _close_db():
    conn = getattr(_thread_local, "conn", None)
    if conn is not None:
        conn.close()
        _thread_local.conn = None


def _init_db():
    """初始化向量库 SQLite 表。"""
    conn = _db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vectors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            entity_type TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            content TEXT NOT NULL,
            vector BLOB NOT NULL,
            deleted_at TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()


def _load_embedder(download_progress_cb=None):
    """加载 embedding 模型，优先 sentence-transformers，回退 TF-IDF。

    加载顺序：
    1. 项目内 bge-model/（PyInstaller 打包后通过 sys._MEIPASS 定位）
    2. 用户主目录 ~/.fastembed_cache/models/Xorbits--bge-small-zh-v1.5/snapshots/master/
    3. 在线下载（HF_HUB_OFFLINE 默认关闭，允许联网）
    4. 回退到 TF-IDF
    """
    global _EMBEDDER, _EMBEDDER_KIND
    if _EMBEDDER is not None:
        return _EMBEDDER, _EMBEDDER_KIND

    try:
        from sentence_transformers import SentenceTransformer

        # 候选本地路径（按优先级）
        candidates = [
            _resource_path("bge-model"),
            Path.home() / ".fastembed_cache" / "models" / "Xorbits--bge-small-zh-v1.5" / "snapshots" / "master",
        ]
        local_path = None
        for p in candidates:
            if p.exists() and (p / "config.json").exists() and (p / "pytorch_model.bin").exists():
                local_path = p
                break

        if local_path:
            logger.info("加载本地 bge 模型：%s", local_path)
            _EMBEDDER = SentenceTransformer(str(local_path))
            _EMBEDDER_KIND = "transformer"
            if download_progress_cb:
                download_progress_cb(100, 100, "本地 bge 模型加载完成")
            return _EMBEDDER, _EMBEDDER_KIND

        # 没有本地模型：在线下载到 MODEL_CACHE_DIR
        cache_dir = _model_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        import threading, time
        EST_TOTAL_MB = 100

        def _watch_progress():
            if download_progress_cb is None:
                return
            while not _load_embedder._done_flag:
                try:
                    total_bytes = sum(p.stat().st_size for p in cache_dir.rglob("*") if p.is_file())
                    loaded_mb = round(total_bytes / (1024 * 1024), 1)
                    download_progress_cb(loaded_mb, EST_TOTAL_MB,
                                         f"正在下载 bge-small-zh 模型... {loaded_mb}/{EST_TOTAL_MB} MB")
                except Exception:
                    pass
                time.sleep(0.5)
            if download_progress_cb:
                try:
                    download_progress_cb(EST_TOTAL_MB, EST_TOTAL_MB, "模型下载完成，正在加载...")
                except Exception:
                    pass

        _load_embedder._done_flag = False
        watcher = threading.Thread(target=_watch_progress, daemon=True)
        watcher.start()
        try:
            _EMBEDDER = SentenceTransformer(
                "BAAI/bge-small-zh-v1.5",
                cache_folder=str(cache_dir),
            )
            _EMBEDDER_KIND = "transformer"
        finally:
            _load_embedder._done_flag = True
            watcher.join(timeout=2)

        logger.info("已加载语义 embedding 模型 (transformer)")
        return _EMBEDDER, _EMBEDDER_KIND
    except ImportError:
        logger.info("sentence-transformers 未安装，回退到 TF-IDF 模式")
    except Exception as e:
        logger.warning("加载 embedding 失败，回退 TF-IDF: %s", e)

    _EMBEDDER = None
    _EMBEDDER_KIND = "tfidf"
    return _EMBEDDER, _EMBEDDER_KIND


# 用于 watcher 线程的结束标志
_load_embedder._done_flag = False


def _encode_transformer(embedder, texts: List[str]) -> np.ndarray:
    vecs = embedder.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


def _ngrams(text: str, n: int = 2) -> List[str]:
    clean = re.sub(r"\s+", "", text.lower())
    if len(clean) < n:
        return list(clean) if clean else []
    return [clean[i : i + n] for i in range(len(clean) - n + 1)]


def _build_tfidf_vocab(texts: List[str]) -> Dict[str, int]:
    vocab: Dict[str, int] = {}
    n = len(texts)
    df: Dict[str, int] = defaultdict(int)
    for t in texts:
        for g in set(_ngrams(t, 2)):
            df[g] += 1
    for g, freq in df.items():
        # 平滑 IDF
        vocab[g] = int(math.log((1 + n) / (1 + freq)) * 1000)  # 缩放为整数便于存储
    return vocab


def _encode_tfidf(text: str, vocab: Dict[str, int], dim: int) -> np.ndarray:
    vec = np.zeros(dim, dtype=np.float32)
    grams = _ngrams(text, 2)
    if not grams:
        return vec
    tf: Dict[str, int] = defaultdict(int)
    for g in grams:
        tf[g] += 1
    for g, cnt in tf.items():
        if g in vocab:
            idx = vocab[g]
            if idx < dim:
                vec[idx] = float(cnt)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


def _get_meta(key: str, default=None):
    conn = _db_connection()
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def _set_meta(key: str, value):
    conn = _db_connection()
    conn.execute(
        "INSERT INTO metadata (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
    conn.commit()


class VectorDB:
    """基于 SQLite + numpy 的向量库。

    存储结构（data/vector/vectors.db）：
    - vectors 表：id, path, entity_type, content_hash, content, vector(BLOB), deleted_at
    - metadata 表：key, value（存储 kind/vocab 等元信息）
    """

    def __init__(self):
        _init_db()
        self.docs: List[Dict] = []
        self.vectors: Optional[np.ndarray] = None
        self.vocab: Dict[str, int] = {}
        self.kind: str = "tfidf"
        self._load()

    def _load(self):
        conn = _db_connection()
        self.kind = _get_meta("kind", "tfidf")
        self.vocab = _get_meta("vocab", {})
        rows = conn.execute(
            "SELECT id, path, entity_type, content_hash, content, vector, deleted_at "
            "FROM vectors ORDER BY id"
        ).fetchall()
        self.docs = [dict(r) for r in rows]
        if rows:
            raw_vectors = [np.frombuffer(r["vector"], dtype=np.float32) for r in rows]
            max_dim = max((v.shape[0] for v in raw_vectors), default=0)
            padded = []
            for v in raw_vectors:
                if v.shape[0] < max_dim:
                    pad = np.zeros(max_dim - v.shape[0], dtype=np.float32)
                    v = np.concatenate([v, pad])
                padded.append(v)
            self.vectors = np.stack(padded)
        else:
            self.vectors = None

    def _save_doc(self, path: str, entity_type: str, content_hash: str, content: str,
                  vector: np.ndarray, deleted_at: str = ""):
        conn = _db_connection()
        conn.execute(
            """
            INSERT INTO vectors (path, entity_type, content_hash, content, vector, deleted_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                entity_type = excluded.entity_type,
                content_hash = excluded.content_hash,
                content = excluded.content,
                vector = excluded.vector,
                deleted_at = excluded.deleted_at
            """,
            (path, entity_type, content_hash, content, vector.tobytes(), deleted_at),
        )
        conn.commit()

    def _encode(self, texts: List[str]) -> np.ndarray:
        embedder, kind = _load_embedder()
        if kind == "transformer" and embedder is not None:
            if self.kind != "transformer":
                self.kind = "transformer"
                _set_meta("kind", "transformer")
            return _encode_transformer(embedder, texts)
        # TF-IDF 回退
        if not self.vocab:
            self.vocab = _build_tfidf_vocab(texts)
        else:
            # 增量补充词汇，但不超过最大维度，避免触发全量重建
            for t in texts:
                for g in _ngrams(t, 2):
                    if g not in self.vocab and len(self.vocab) < _MAX_TFIDF_DIM:
                        self.vocab[g] = len(self.vocab)
        dim = max(min(len(self.vocab), _MAX_TFIDF_DIM), 1)
        vecs = np.stack([_encode_tfidf(t, self.vocab, dim) for t in texts])
        # 持久化 vocab 变化
        _set_meta("vocab", self.vocab)
        return vecs

    def _ensure_dim(self, target_dim: int) -> None:
        """将现有向量矩阵填充到目标维度（用 0 填充），避免全量重建。"""
        if self.vectors is None or self.vectors.shape[1] >= target_dim:
            return
        pad_width = ((0, 0), (0, target_dim - self.vectors.shape[1]))
        self.vectors = np.pad(self.vectors, pad_width, mode="constant", constant_values=0)

    @timed("vector.add")
    def add(self, path: str, entity_type: str, content: str) -> None:
        """添加或更新一个文档（按 path 去重）。

        更新时自动把 deleted_at 置空（如果之前被软删除则恢复）。
        维度变化时通过零填充增量更新，不触发全量重建。
        """
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        existing = next((i for i, d in enumerate(self.docs) if d["path"] == path), None)
        vec = self._encode([content])
        target_dim = vec.shape[1]

        if existing is not None:
            doc = self.docs[existing]
            if doc.get("content_hash") == content_hash and not doc.get("deleted_at"):
                return  # 内容未变且未删除，跳过
            self._ensure_dim(target_dim)
            if self.vectors is not None:
                self.vectors[existing] = vec[0]
            doc["content"] = content
            doc["content_hash"] = content_hash
            doc["entity_type"] = entity_type
            doc["deleted_at"] = ""
            self._save_doc(path, entity_type, content_hash, content, vec[0], "")
            return

        # 新文档
        self._ensure_dim(target_dim)
        if self.vectors is None or len(self.vectors) == 0:
            self.vectors = vec
        else:
            self.vectors = np.vstack([self.vectors, vec])

        new_id = max((d["id"] for d in self.docs), default=0) + 1
        self.docs.append({
            "id": new_id,
            "path": path,
            "entity_type": entity_type,
            "content": content,
            "content_hash": content_hash,
            "deleted_at": "",
        })
        self._save_doc(path, entity_type, content_hash, content, vec[0], "")

    @timed("vector.add_batch")
    def add_batch(self, items: List[tuple]) -> None:
        """批量添加/更新文档，减少重复编码和 SQLite 事务开销。

        items: [(path, entity_type, content), ...]
        """
        if not items:
            return
        texts = [content for _, _, content in items]
        vecs = self._encode(texts)
        target_dim = vecs.shape[1]
        self._ensure_dim(target_dim)

        conn = _db_connection()
        for (path, entity_type, content), vec in zip(items, vecs):
            content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
            existing = next((i for i, d in enumerate(self.docs) if d["path"] == path), None)
            if existing is not None:
                doc = self.docs[existing]
                if doc.get("content_hash") == content_hash and not doc.get("deleted_at"):
                    continue
                if self.vectors is not None:
                    self.vectors[existing] = vec
                doc["content"] = content
                doc["content_hash"] = content_hash
                doc["entity_type"] = entity_type
                doc["deleted_at"] = ""
            else:
                if self.vectors is None or len(self.vectors) == 0:
                    self.vectors = vec.reshape(1, -1)
                else:
                    self.vectors = np.vstack([self.vectors, vec])
                new_id = max((d["id"] for d in self.docs), default=0) + 1
                self.docs.append({
                    "id": new_id,
                    "path": path,
                    "entity_type": entity_type,
                    "content": content,
                    "content_hash": content_hash,
                    "deleted_at": "",
                })
            self._save_doc(path, entity_type, content_hash, content, vec, "")
        conn.commit()

    def soft_delete(self, path: str) -> bool:
        """软删除：打 deleted_at 标记。向量保留在矩阵中，检索时跳过。"""
        for d in self.docs:
            if d["path"] == path and not d.get("deleted_at"):
                ts = datetime.now().isoformat(timespec="seconds")
                d["deleted_at"] = ts
                conn = _db_connection()
                conn.execute("UPDATE vectors SET deleted_at = ? WHERE path = ?", (ts, path))
                conn.commit()
                return True
        return False

    def restore(self, path: str) -> bool:
        """恢复软删除的文档。"""
        for d in self.docs:
            if d["path"] == path and d.get("deleted_at"):
                d["deleted_at"] = ""
                conn = _db_connection()
                conn.execute("UPDATE vectors SET deleted_at = '' WHERE path = ?", (path,))
                conn.commit()
                return True
        return False

    def hard_delete(self, path: str) -> bool:
        """硬删除：从 docs 和向量矩阵中永久移除。"""
        for i, d in enumerate(self.docs):
            if d["path"] == path:
                del self.docs[i]
                if self.vectors is not None and self.vectors.shape[0] > i:
                    self.vectors = np.delete(self.vectors, i, axis=0)
                    if self.vectors.shape[0] == 0:
                        self.vectors = None
                conn = _db_connection()
                conn.execute("DELETE FROM vectors WHERE path = ?", (path,))
                conn.commit()
                return True
        return False

    def reconcile(self, valid_paths: set) -> int:
        """把所有不在 valid_paths 中的文档软删除，返回被标记的数量。"""
        count = 0
        ts = datetime.now().isoformat(timespec="seconds")
        conn = _db_connection()
        for d in self.docs:
            if d["path"] not in valid_paths and not d.get("deleted_at"):
                d["deleted_at"] = ts
                conn.execute("UPDATE vectors SET deleted_at = ? WHERE path = ?", (ts, d["path"]))
                count += 1
        if count > 0:
            conn.commit()
        return count

    def _rebuild_all(self):
        if not self.docs:
            self.vectors = None
            conn = _db_connection()
            conn.execute("DELETE FROM vectors")
            conn.commit()
            return
        texts = [d["content"] for d in self.docs]
        self.vectors = self._encode(texts)
        # 重新写入全部向量
        conn = _db_connection()
        conn.execute("DELETE FROM vectors")
        for doc, vec in zip(self.docs, self.vectors):
            conn.execute(
                "INSERT INTO vectors (path, entity_type, content_hash, content, vector, deleted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (doc["path"], doc["entity_type"], doc["content_hash"], doc["content"],
                 vec.tobytes(), doc.get("deleted_at", "")),
            )
        conn.commit()
        _set_meta("kind", self.kind)
        _set_meta("vocab", self.vocab)

    def rebuild(self):
        """强制重建全部向量索引。"""
        self._rebuild_all()

    @timed("vector.search")
    def search(self, query: str, entity_type: Optional[str] = None,
               top_k: int = 10) -> List[Dict]:
        """余弦相似度检索。自动跳过软删除的文档。"""
        if not self.docs or self.vectors is None or len(self.vectors) == 0:
            return []
        qvec = self._encode([query])[0]
        if qvec.shape[0] != self.vectors.shape[1]:
            return []
        # 余弦相似度（向量已归一化时为点积，未归一化则手动除）
        scores = self.vectors @ qvec
        qnorm = np.linalg.norm(qvec)
        vnorms = np.linalg.norm(self.vectors, axis=1)
        denom = vnorms * qnorm
        denom = np.where(denom == 0, 1.0, denom)
        cosine = scores / denom

        results = []
        for i, doc in enumerate(self.docs):
            if doc.get("deleted_at"):
                continue  # 跳过软删除
            if entity_type and doc.get("entity_type") != entity_type:
                continue
            results.append((float(cosine[i]), i))
        results.sort(key=lambda x: -x[0])
        output = []
        for score, i in results[:top_k]:
            d = self.docs[i].copy()
            d["score"] = round(score, 4)
            output.append(d)
        return output

    def stats(self) -> Dict:
        active = sum(1 for d in self.docs if not d.get("deleted_at"))
        deleted = sum(1 for d in self.docs if d.get("deleted_at"))
        return {
            "doc_count": active,
            "deleted_count": deleted,
            "kind": self.kind,
            "dim": int(self.vectors.shape[1]) if self.vectors is not None else 0,
        }

    def list_docs(self, include_deleted: bool = False,
                  entity_type: Optional[str] = None) -> List[Dict]:
        """列出文档元信息（不含向量矩阵）。"""
        out = []
        for d in self.docs:
            if not include_deleted and d.get("deleted_at"):
                continue
            if entity_type and d.get("entity_type") != entity_type:
                continue
            # 不返回 content 字段以减少内存占用
            out.append({
                "id": d["id"],
                "path": d["path"],
                "entity_type": d["entity_type"],
                "content_hash": d.get("content_hash", ""),
                "deleted_at": d.get("deleted_at", ""),
                "content_len": len(d.get("content", "")),
            })
        return out

    def ensure_loaded(self, download_progress_cb=None):
        """确保 embedding 模型已加载（首次调用会触发下载）。

        供 GUI 在「重建向量索引」或「运行管线」前调用，以便显示下载进度。
        """
        _load_embedder(download_progress_cb=download_progress_cb)
