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
from pewm.processors.database import _to_rel
from pewm.processors.log_config import get_logger
from pewm.processors.metrics import timed

logger = get_logger(__name__)


def _notify_vector_changed() -> None:
    """向量库写操作后通知检索层：清查询缓存并把进程级单例标记为过期。

    延迟 import 避免循环依赖（retrieval 依赖本模块）。
    """
    try:
        from pewm.processors.retrieval import on_vector_store_changed
        on_vector_store_changed()
    except Exception:
        pass


def _vector_dir() -> Path:
    return paths.ROOT / "data" / "vector"


def _db_file() -> Path:
    return _vector_dir() / "vectors.db"


def _model_cache_dir() -> Path:
    return _vector_dir() / "embedding_model"


# TF-IDF 模式下固定最大维度，避免新增文档触发全量重建
_MAX_TFIDF_DIM = 65536

_thread_local = threading.local()

# 全局单例：embedding 模型（懒加载，避免重复加载耗时）
_EMBEDDER = None
_EMBEDDER_KIND: Optional[str] = None  # "transformer" | "tfidf"
_EMBEDDER_LOADED = False  # 是否已完成加载决策（含 TF-IDF 回退）
_EMBEDDER_LOCK = threading.Lock()


def _resource_path(*parts: str) -> Path:
    """返回资源文件的绝对路径。PyInstaller 打包后从 sys._MEIPASS 解析，源码模式从 ROOT 解析。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS).joinpath(*parts)
    return paths.ROOT.joinpath(*parts)


def _db_connection():
    """获取线程本地 SQLite 连接。"""
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        _vector_dir().mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_db_file()))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("PRAGMA busy_timeout=10000")
        except sqlite3.OperationalError:
            pass
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
    _migrate_paths_to_rel(conn)


def _migrate_paths_to_rel(conn) -> None:
    """一次性迁移：把 vectors 表中残留的绝对路径转为相对路径。

    与 documents 表统一使用 database._to_rel 的口径；若相对路径已存在
    则删除旧的绝对路径记录（去重）。
    """
    rows = conn.execute("SELECT id, path FROM vectors").fetchall()
    changed = False
    for row in rows:
        rel = _to_rel(row["path"])
        if rel != row["path"]:
            conflict = conn.execute(
                "SELECT id FROM vectors WHERE path = ?", (rel,)
            ).fetchone()
            if conflict is not None:
                conn.execute("DELETE FROM vectors WHERE id = ?", (row["id"],))
            else:
                conn.execute(
                    "UPDATE vectors SET path = ? WHERE id = ?", (rel, row["id"])
                )
            changed = True
    if changed:
        conn.commit()
        logger.info("已将向量库中的绝对路径迁移为相对路径。")


def _load_embedder(download_progress_cb=None):
    """加载 embedding 模型，优先 sentence-transformers，回退 TF-IDF。

    加载顺序：
    1. 项目内 bge-model/（PyInstaller 打包后通过 sys._MEIPASS 定位）
    2. 用户主目录 ~/.fastembed_cache/models/Xorbits--bge-small-zh-v1.5/snapshots/master/
    3. 在线下载（HF_HUB_OFFLINE 默认关闭，允许联网）
    4. 回退到 TF-IDF
    """
    global _EMBEDDER, _EMBEDDER_KIND, _EMBEDDER_LOADED
    if _EMBEDDER_LOADED:
        return _EMBEDDER, _EMBEDDER_KIND

    with _EMBEDDER_LOCK:
        # double-checked：并发下仅一个线程执行加载
        if _EMBEDDER_LOADED:
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
                _EMBEDDER_LOADED = True
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
                        # 进度回调失败不应阻塞下载
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

            _EMBEDDER_LOADED = True
            logger.info("已加载语义 embedding 模型 (transformer)")
            return _EMBEDDER, _EMBEDDER_KIND
        except ImportError:
            logger.info("sentence-transformers 未安装，回退到 TF-IDF 模式")
        except Exception as e:
            logger.warning("加载 embedding 失败，回退 TF-IDF: %s", e)

        _EMBEDDER = None
        _EMBEDDER_KIND = "tfidf"
        _EMBEDDER_LOADED = True
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
    """构建词表：token -> 连续列下标（0..N-1）。"""
    vocab: Dict[str, int] = {}
    for t in texts:
        for g in set(_ngrams(t, 2)):
            if g not in vocab:
                vocab[g] = len(vocab)
    return vocab


def _build_tfidf_idf(texts: List[str]) -> Dict[str, float]:
    """构建 IDF 表：token -> 平滑 IDF 分值（与 vocab 下标分离存储）。"""
    n = len(texts)
    df: Dict[str, int] = defaultdict(int)
    for t in texts:
        for g in set(_ngrams(t, 2)):
            df[g] += 1
    return {g: math.log((1 + n) / (1 + freq)) + 1.0 for g, freq in df.items()}


def _encode_tfidf(text: str, vocab: Dict[str, int], dim: int,
                  idf: Optional[Dict[str, float]] = None) -> np.ndarray:
    vec = np.zeros(dim, dtype=np.float32)
    grams = _ngrams(text, 2)
    if not grams:
        return vec
    tf: Dict[str, int] = defaultdict(int)
    for g in grams:
        tf[g] += 1
    for g, cnt in tf.items():
        idx = vocab.get(g)
        if idx is not None and idx < dim:
            weight = float(cnt)
            if idf:
                weight *= idf.get(g, 1.0)
            vec[idx] = weight
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
        # 旧数据可能是纯字符串，直接返回
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
        self._idf: Dict[str, float] = {}
        self.kind: str = "tfidf"
        self._path_index: Dict[str, int] = {}
        self._load()

    def _load(self):
        conn = _db_connection()
        kind = _get_meta("kind", "tfidf")
        vocab = _get_meta("vocab", {})
        idf = _get_meta("idf", {})
        rows = conn.execute(
            "SELECT id, path, entity_type, content_hash, content, vector, deleted_at "
            "FROM vectors ORDER BY id"
        ).fetchall()
        docs = [dict(r) for r in rows]
        if rows:
            raw_vectors = [np.frombuffer(r["vector"], dtype=np.float32) for r in rows]
            max_dim = max((v.shape[0] for v in raw_vectors), default=0)
            padded = []
            for v in raw_vectors:
                if v.shape[0] < max_dim:
                    pad = np.zeros(max_dim - v.shape[0], dtype=np.float32)
                    v = np.concatenate([v, pad])
                padded.append(v)
            vectors = np.stack(padded)
        else:
            vectors = None
        # 末尾统一赋值，避免并发检索读到半成品状态
        self.kind = kind
        self.vocab = vocab
        self._idf = idf if isinstance(idf, dict) else {}
        self.docs = docs
        self.vectors = vectors
        self._path_index = {d["path"]: i for i, d in enumerate(docs)}

    def refresh(self) -> None:
        """从 SQLite 重新加载全部文档与向量（进程级单例缓存失效后调用）。"""
        self._load()

    def _save_doc(self, path: str, entity_type: str, content_hash: str, content: str,
                  vector: np.ndarray, deleted_at: str = "", commit: bool = True):
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
        if commit:
            conn.commit()

    def _encode(self, texts: List[str]) -> np.ndarray:
        embedder, kind = _load_embedder()
        if kind == "transformer" and embedder is not None:
            if self.kind != "transformer":
                # embedder 从 TF-IDF 切换为 transformer：全量重编码旧向量
                self._reencode_all(embedder, "transformer")
            return _encode_transformer(embedder, texts)
        # TF-IDF 回退
        if self.kind != "tfidf":
            # embedder 从 transformer 回退为 TF-IDF：全量重编码旧向量
            self._reencode_all(None, "tfidf")
        if not self.vocab:
            self.vocab = _build_tfidf_vocab(texts)
            self._idf = _build_tfidf_idf(texts)
        else:
            # 增量补充词汇，但不超过最大维度，避免触发全量重建
            for t in texts:
                for g in _ngrams(t, 2):
                    if g not in self.vocab and len(self.vocab) < _MAX_TFIDF_DIM:
                        self.vocab[g] = len(self.vocab)
        dim = max(min(len(self.vocab), _MAX_TFIDF_DIM), 1)
        vecs = np.stack([_encode_tfidf(t, self.vocab, dim, self._idf) for t in texts])
        # 持久化 vocab/idf 变化
        _set_meta("vocab", self.vocab)
        _set_meta("idf", self._idf)
        return vecs

    def _reencode_all(self, embedder, kind: str) -> None:
        """embedder 类型切换后，用新编码器重编码全部现有文档并持久化。"""
        logger.warning(
            "检测到 embedding 模型切换（%s -> %s），正在全量重编码 %d 条向量",
            self.kind, kind, len(self.docs),
        )
        self.kind = kind
        self.vocab = {}
        self._idf = {}
        contents = [d["content"] for d in self.docs]
        if not contents:
            self.vectors = None
        elif kind == "transformer":
            self.vectors = _encode_transformer(embedder, contents)
        else:
            self.vocab = _build_tfidf_vocab(contents)
            self._idf = _build_tfidf_idf(contents)
            dim = max(min(len(self.vocab), _MAX_TFIDF_DIM), 1)
            self.vectors = np.stack(
                [_encode_tfidf(t, self.vocab, dim, self._idf) for t in contents]
            )
        if self.vectors is not None:
            conn = _db_connection()
            for i, doc in enumerate(self.docs):
                conn.execute(
                    "UPDATE vectors SET vector = ? WHERE path = ?",
                    (self.vectors[i].tobytes(), doc["path"]),
                )
            conn.commit()
        _set_meta("kind", self.kind)
        _set_meta("vocab", self.vocab)
        _set_meta("idf", self._idf)

    def _ensure_dim(self, target_dim: int) -> None:
        """将现有向量矩阵填充到目标维度（用 0 填充），避免全量重建。"""
        if self.vectors is None or self.vectors.shape[1] >= target_dim:
            return
        pad_width = ((0, 0), (0, target_dim - self.vectors.shape[1]))
        self.vectors = np.pad(self.vectors, pad_width, mode="constant", constant_values=0)

    def _align_vectors(self, vecs: np.ndarray) -> np.ndarray:
        """把新编码向量与现有矩阵维度对齐。

        新向量更宽时给旧矩阵右侧补零；新向量更窄时（如模型回退后）
        给新向量右侧补零，避免赋值/vstack 维度不匹配崩溃。
        """
        target_dim = vecs.shape[1]
        if self.vectors is not None and len(self.vectors) > 0:
            cur_dim = self.vectors.shape[1]
            if cur_dim > target_dim:
                vecs = np.pad(vecs, ((0, 0), (0, cur_dim - target_dim)),
                              mode="constant", constant_values=0)
            else:
                self._ensure_dim(target_dim)
        return vecs

    @timed("vector.add")
    def add(self, path: str, entity_type: str, content: str) -> None:
        """添加或更新一个文档（按 path 去重）。

        更新时自动把 deleted_at 置空（如果之前被软删除则恢复）。
        维度变化时通过零填充增量更新，不触发全量重建。
        """
        path = _to_rel(path)
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        existing = self._path_index.get(path)
        vec = self._align_vectors(self._encode([content]))

        if existing is not None:
            doc = self.docs[existing]
            if doc.get("content_hash") == content_hash and not doc.get("deleted_at"):
                return  # 内容未变且未删除，跳过
            if self.vectors is not None:
                self.vectors[existing] = vec[0]
            doc["content"] = content
            doc["content_hash"] = content_hash
            doc["entity_type"] = entity_type
            doc["deleted_at"] = ""
            self._save_doc(path, entity_type, content_hash, content, vec[0], "")
            _notify_vector_changed()
            return

        # 新文档
        if self.vectors is None or len(self.vectors) == 0:
            self.vectors = vec
        else:
            self.vectors = np.vstack([self.vectors, vec])

        new_id = max((d["id"] for d in self.docs), default=0) + 1
        self._path_index[path] = len(self.docs)
        self.docs.append({
            "id": new_id,
            "path": path,
            "entity_type": entity_type,
            "content": content,
            "content_hash": content_hash,
            "deleted_at": "",
        })
        self._save_doc(path, entity_type, content_hash, content, vec[0], "")
        _notify_vector_changed()

    @timed("vector.add_batch")
    def add_batch(self, items: List[tuple]) -> None:
        """批量添加/更新文档，减少重复编码和 SQLite 事务开销。

        items: [(path, entity_type, content), ...]
        """
        if not items:
            return
        items = [(_to_rel(path), entity_type, content) for path, entity_type, content in items]
        texts = [content for _, _, content in items]
        vecs = self._align_vectors(self._encode(texts))

        changed = False
        conn = _db_connection()
        for (path, entity_type, content), vec in zip(items, vecs):
            content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
            existing = self._path_index.get(path)
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
                self._path_index[path] = len(self.docs)
                self.docs.append({
                    "id": new_id,
                    "path": path,
                    "entity_type": entity_type,
                    "content": content,
                    "content_hash": content_hash,
                    "deleted_at": "",
                })
            # 批量模式：由末尾统一 commit，避免逐条事务
            self._save_doc(path, entity_type, content_hash, content, vec, "", commit=False)
            changed = True
        conn.commit()
        if changed:
            _notify_vector_changed()

    def soft_delete(self, path: str) -> bool:
        """软删除：打 deleted_at 标记。向量保留在矩阵中，检索时跳过。"""
        path = _to_rel(path)
        for d in self.docs:
            if d["path"] == path and not d.get("deleted_at"):
                ts = datetime.now().isoformat(timespec="seconds")
                d["deleted_at"] = ts
                conn = _db_connection()
                conn.execute("UPDATE vectors SET deleted_at = ? WHERE path = ?", (ts, path))
                conn.commit()
                _notify_vector_changed()
                return True
        return False

    def restore(self, path: str) -> bool:
        """恢复软删除的文档。"""
        path = _to_rel(path)
        for d in self.docs:
            if d["path"] == path and d.get("deleted_at"):
                d["deleted_at"] = ""
                conn = _db_connection()
                conn.execute("UPDATE vectors SET deleted_at = '' WHERE path = ?", (path,))
                conn.commit()
                _notify_vector_changed()
                return True
        return False

    def hard_delete(self, path: str) -> bool:
        """硬删除：从 docs 和向量矩阵中永久移除。"""
        path = _to_rel(path)
        i = self._path_index.get(path)
        if i is not None:
            del self.docs[i]
            if self.vectors is not None and self.vectors.shape[0] > i:
                self.vectors = np.delete(self.vectors, i, axis=0)
                if self.vectors.shape[0] == 0:
                    self.vectors = None
            # 删除后下标前移，重建索引
            self._path_index = {d["path"]: idx for idx, d in enumerate(self.docs)}
            conn = _db_connection()
            conn.execute("DELETE FROM vectors WHERE path = ?", (path,))
            conn.commit()
            _notify_vector_changed()
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
            _notify_vector_changed()
        return count

    def _rebuild_all(self):
        if not self.docs:
            self.vectors = None
            conn = _db_connection()
            conn.execute("DELETE FROM vectors")
            conn.commit()
            _notify_vector_changed()
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
        _set_meta("idf", self._idf)
        _notify_vector_changed()

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
        # 维度对齐：查询向量比矩阵窄则补零，比矩阵宽则截断（超宽列在矩阵中恒为 0）
        dim = self.vectors.shape[1]
        if qvec.shape[0] < dim:
            qvec = np.concatenate([qvec, np.zeros(dim - qvec.shape[0], dtype=np.float32)])
        elif qvec.shape[0] > dim:
            qvec = qvec[:dim]
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
