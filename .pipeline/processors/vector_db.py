"""轻量级向量数据库。

优先使用 sentence-transformers (bge-small-zh-v1.5) 做语义检索，
如未安装则自动回退到字符 2-gram TF-IDF + numpy 余弦相似度。

数据持久化在 data/vector/index.pkl。
"""
import hashlib
import math
import pickle
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

try:
    from .utils import ROOT
except ImportError:
    from utils import ROOT

VECTOR_DIR = ROOT / "data" / "vector"
INDEX_FILE = VECTOR_DIR / "index.pkl"
MODEL_CACHE_DIR = VECTOR_DIR / "embedding_model"

# 全局单例：embedding 模型（懒加载，避免重复加载耗时）
_EMBEDDER = None
_EMBEDDER_KIND: Optional[str] = None  # "transformer" | "tfidf"


def _resource_path(*parts: str) -> Path:
    """返回资源文件的绝对路径。PyInstaller 打包后从 sys._MEIPASS 解析，源码模式从 ROOT 解析。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS).joinpath(*parts)
    return ROOT.joinpath(*parts)


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
        import sys
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
            print(f"[vector] 加载本地 bge 模型：{local_path}")
            _EMBEDDER = SentenceTransformer(str(local_path))
            _EMBEDDER_KIND = "transformer"
            if download_progress_cb:
                download_progress_cb(100, 100, "本地 bge 模型加载完成")
            return _EMBEDDER, _EMBEDDER_KIND

        # 没有本地模型：在线下载到 MODEL_CACHE_DIR
        MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        import threading, time
        EST_TOTAL_MB = 100

        def _watch_progress():
            if download_progress_cb is None:
                return
            while not _load_embedder._done_flag:
                try:
                    total_bytes = sum(p.stat().st_size for p in MODEL_CACHE_DIR.rglob("*") if p.is_file())
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
                cache_folder=str(MODEL_CACHE_DIR),
            )
            _EMBEDDER_KIND = "transformer"
        finally:
            _load_embedder._done_flag = True
            watcher.join(timeout=2)

        print(f"[vector] 已加载语义 embedding 模型 (transformer)")
        return _EMBEDDER, _EMBEDDER_KIND
    except ImportError:
        print("[vector] sentence-transformers 未安装，回退到 TF-IDF 模式")
    except Exception as e:
        print(f"[vector] 加载 embedding 失败，回退 TF-IDF: {e}")

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
            vec[vocab[g]] = float(cnt)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


class VectorDB:
    """基于 numpy + pickle 的向量库。

    存储结构（data/vector/index.pkl）：
    {
        "kind": "transformer" | "tfidf",
        "vocab": {...},              # 仅 tfidf 模式
        "docs": [{"id", "path", "entity_type", "content", "content_hash"}, ...],
        "vectors": np.ndarray,       # shape (N, dim), float32
    }
    """

    def __init__(self):
        self.docs: List[Dict] = []
        self.vectors: Optional[np.ndarray] = None
        self.vocab: Dict[str, int] = {}
        self.kind: str = "tfidf"
        self._load()

    def _load(self):
        if INDEX_FILE.exists():
            try:
                data = pickle.loads(INDEX_FILE.read_bytes())
                self.docs = data.get("docs", [])
                self.vectors = data.get("vectors")
                self.vocab = data.get("vocab", {})
                self.kind = data.get("kind", "tfidf")
                # 兼容旧索引：补齐 deleted_at 字段
                for d in self.docs:
                    d.setdefault("deleted_at", "")
            except Exception as e:
                print(f"[vector] 加载索引失败，将重建: {e}")

    def _save(self):
        VECTOR_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "kind": self.kind,
            "vocab": self.vocab,
            "docs": self.docs,
            "vectors": self.vectors,
        }
        INDEX_FILE.write_bytes(pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL))

    def _encode(self, texts: List[str]) -> np.ndarray:
        embedder, kind = _load_embedder()
        if kind == "transformer" and embedder is not None:
            return _encode_transformer(embedder, texts)
        # TF-IDF 回退
        if not self.vocab:
            self.vocab = _build_tfidf_vocab(texts)
        else:
            # 增量补充词汇
            for t in texts:
                for g in _ngrams(t, 2):
                    if g not in self.vocab:
                        self.vocab[g] = len(self.vocab)
        dim = max(len(self.vocab), 1)
        return np.stack([_encode_tfidf(t, self.vocab, dim) for t in texts])

    def add(self, path: str, entity_type: str, content: str) -> None:
        """添加或更新一个文档（按 path 去重）。

        更新时自动把 deleted_at 置空（如果之前被软删除则恢复）。
        """
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        for i, doc in enumerate(self.docs):
            if doc["path"] == path:
                if doc.get("content_hash") == content_hash and not doc.get("deleted_at"):
                    return  # 内容未变且未删除，跳过
                doc["content"] = content
                doc["content_hash"] = content_hash
                doc["entity_type"] = entity_type
                doc["deleted_at"] = ""  # 自动恢复
                vec = self._encode([content])
                if self.vectors is not None and self.vectors.shape[1] == vec.shape[1]:
                    self.vectors[i] = vec[0]
                else:
                    self._rebuild_all()
                self._save()
                return
        new_id = max((d["id"] for d in self.docs), default=0) + 1
        self.docs.append({
            "id": new_id,
            "path": path,
            "entity_type": entity_type,
            "content": content,
            "content_hash": content_hash,
            "deleted_at": "",
        })
        vec = self._encode([content])
        if self.vectors is None or len(self.vectors) == 0:
            self.vectors = vec
        elif self.vectors.shape[1] == vec.shape[1]:
            self.vectors = np.vstack([self.vectors, vec])
        else:
            self._rebuild_all()
        self._save()

    def soft_delete(self, path: str) -> bool:
        """软删除：打 deleted_at 标记。向量保留在矩阵中，检索时跳过。"""
        for d in self.docs:
            if d["path"] == path and not d.get("deleted_at"):
                d["deleted_at"] = datetime.now().isoformat(timespec="seconds")
                self._save()
                return True
        return False

    def restore(self, path: str) -> bool:
        """恢复软删除的文档。"""
        for d in self.docs:
            if d["path"] == path and d.get("deleted_at"):
                d["deleted_at"] = ""
                self._save()
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
                self._save()
                return True
        return False

    def reconcile(self, valid_paths: set) -> int:
        """把所有不在 valid_paths 中的文档软删除，返回被标记的数量。"""
        count = 0
        ts = datetime.now().isoformat(timespec="seconds")
        for d in self.docs:
            if d["path"] not in valid_paths and not d.get("deleted_at"):
                d["deleted_at"] = ts
                count += 1
        if count > 0:
            self._save()
        return count

    def _rebuild_all(self):
        if not self.docs:
            self.vectors = None
            return
        texts = [d["content"] for d in self.docs]
        self.vectors = self._encode(texts)

    def rebuild(self):
        """强制重建全部向量索引。"""
        self._rebuild_all()
        self._save()

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
