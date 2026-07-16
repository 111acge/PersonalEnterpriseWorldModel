"""AI 管线入口。"""
import argparse
import subprocess
from pathlib import Path
from typing import Dict

import pewm.paths as paths
from pewm.processors.utils import (
    list_inbox_files,
    is_unprocessed,
    read_text,
    write_text,
)
from pewm.processors.extractor import extract_entities, extract_entities_batch, load_schemas
from pewm.processors.log_config import get_logger
from pewm.processors.metrics import timed
from pewm.processors.vectorizer import index_documents
from pewm.processors.database import init_db, mark_inbox_processed, load_processed, get_stats

logger = get_logger(__name__)

ROOT = paths.ROOT
INBOX_DIR = paths.INBOX_DIR


def reconcile() -> Dict[str, int]:
    """扫描所有已索引文档，把磁盘上不存在的文件自动软删除（保留历史，可恢复）。

    注意：数据库 documents 表存的是绝对路径，当 exe 被拷贝到新目录或项目目录被重命名时，
    旧的绝对路径会失效。这里会尝试把绝对路径"回译"成相对于当前 ROOT 的相对路径，
    再用当前 ROOT 重新拼接成新的绝对路径来判断文件是否真的不存在。这样能避免误软删。

    返回 {"database": N, "vector": M}，分别表示 FTS5 和向量库被软删除的数量。
    """
    from pewm.paths import ROOT
    from pewm.processors.database import list_documents, soft_delete_document
    from pewm.processors.vector_db import VectorDB

    def _path_still_exists(stored_path: str) -> bool:
        """判断存储的路径在当前环境下是否还存在。

        处理策略：
        1. 原路径直接存在 → True
        2. 原路径是绝对路径 → 尝试计算它相对于"其根目录"的片段，
           再用当前 ROOT 拼接，看新路径是否存在
        3. 都不存在 → False
        """
        p = Path(stored_path)
        if p.exists():
            return True
        if not p.is_absolute():
            # 相对路径：相对于当前 ROOT 判断
            return (ROOT / p).exists()
        # 绝对路径：提取尾部片段，尝试在当前 ROOT 下寻找
        # 例：C:/old/project/20-Ontology/foo.md → 20-Ontology/foo.md → C:/new/project/20-Ontology/foo.md
        parts = p.parts
        # 找到第一个"知识层目录"作为锚点（10-Theory / 20-Ontology / 30-Instances / 40-Skills / 00-Inbox）
        anchor_names = {"10-Theory", "20-Ontology", "30-Instances", "40-Skills", "00-Inbox"}
        for i, part in enumerate(parts):
            if part in anchor_names:
                rel = Path(*parts[i:])
                new_path = ROOT / rel
                if new_path.exists():
                    return True
                break
        # 退路：尝试取最后 3 段作为相对路径
        if len(parts) >= 3:
            rel = Path(*parts[-3:])
            if (ROOT / rel).exists():
                return True
        return False

    # 收集所有未删除文档的 path
    docs = list_documents(include_deleted=False, limit=100000)
    db_count = 0
    for d in docs:
        if not _path_still_exists(d["path"]):
            if soft_delete_document(d["path"]):
                db_count += 1
                logger.info("[soft-delete] %s", d["path"])

    # 向量库同步
    vdb = VectorDB()
    active_paths = {d["path"] for d in vdb.list_docs(include_deleted=False)}
    vec_count = 0
    for p in list(active_paths):
        if not _path_still_exists(p):
            if vdb.soft_delete(p):
                vec_count += 1

    return {"database": db_count, "vector": vec_count}


def purge_orphans() -> Dict[str, int]:
    """硬删除所有软删除的文档（不可恢复）。

    返回 {"database": N, "vector": M}。
    """
    from pewm.processors.database import list_documents, hard_delete_document
    from pewm.processors.vector_db import VectorDB

    docs = list_documents(include_deleted=True, limit=100000)
    db_count = 0
    for d in docs:
        if d.get("deleted_at"):
            if hard_delete_document(d["path"]):
                db_count += 1

    vdb = VectorDB()
    vec_count = 0
    for d in vdb.list_docs(include_deleted=True):
        if d.get("deleted_at"):
            if vdb.hard_delete(d["path"]):
                vec_count += 1

    return {"database": db_count, "vector": vec_count}


def _try_ocr(inbox_path: Path, text: str) -> str:
    """尝试把与该 Inbox 文件关联的图片 OCR 结果拼接到原文后。

    支持双模式：
    - mode=api: 直接调用云端 API，不需要本地 PaddleOCR
    - mode=local: 调用本地 PaddleOCR，未安装时静默跳过
    """
    from pewm.processors.ocr import ocr_for_inbox_file, is_local_available
    from pewm.processors.ocr_api import load_ocr_config
    try:
        cfg = load_ocr_config()
        mode = cfg.get("mode", "local")
        if mode == "local" and not is_local_available():
            return text
        ocr_text = ocr_for_inbox_file(inbox_path)
        if ocr_text:
            return text + "\n\n## 来自图片的补充内容\n\n" + ocr_text
    except Exception as e:
        logger.warning("OCR 处理失败: %s", e)
    return text


@timed("pipeline.run")
def run_pipeline(
    reset: bool = False,
    skip_errors: bool = False,
    no_git: bool = False,
    no_vector: bool = False,
    no_ocr: bool = False,
):
    logger.info("启动 AI 管线：%s", ROOT)
    # 立即初始化数据库，确保 data/ 目录和表始终被创建
    init_db()
    from pewm.paths import DATA_DIR
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    schemas = load_schemas()
    documents = []
    processed_count = 0

    files = list_inbox_files()
    if not files:
        logger.info("00-Inbox 中没有 Markdown 文件。")
        return

    # 收集待处理的文件
    pending = []
    for path in files:
        rel = str(path.relative_to(ROOT))
        mtime = str(path.stat().st_mtime)
        if not reset and not is_unprocessed(path):
            continue
        pending.append((path, rel, mtime))

    if not pending:
        logger.info("没有新的 Inbox 文件需要处理。")
        return

    logger.info("发现 %d 个待处理文件，准备提取...", len(pending))

    # 批量提取：先尝试一次性 LLM 处理多篇
    batch_items = []
    for path, rel, mtime in pending:
        try:
            text = read_text(path)
            if not no_ocr:
                text = _try_ocr(path, text)
            batch_items.append((rel, text, mtime, path))
        except Exception as e:
            if skip_errors:
                logger.error("读取失败（已跳过）: %s - %s", rel, e)
                continue
            raise

    if batch_items:
        sources = [item[0] for item in batch_items]
        logger.info("批量处理：%s", ", ".join(sources))
        batch_results = extract_entities_batch([(s, t) for s, t, m, p in batch_items])

        for idx, (rel, text, mtime, path) in enumerate(batch_items):
            entities = batch_results[idx] if idx < len(batch_results) else []
            if not entities:
                # 批量未返回时单文件兜底
                entities = extract_entities(text, source=rel)

            logger.info("[process] %s", rel)
            for entity in entities:
                write_text(entity["path"], entity["content"])
                logger.info("  -> %s", entity["path"].relative_to(ROOT))
                documents.append({
                    "source": rel,
                    "entity_type": entity["entity_type"],
                    "path": entity["path"],
                    "content": entity["content"],
                })

            mark_inbox_processed(rel, mtime)
            processed_count += 1

    logger.info("处理完成：%d 个 Inbox 文件。", processed_count)

    # SQLite FTS5 + 向量索引
    index_documents(documents, build_vector=not no_vector)

    # 自动清理：把磁盘上已不存在的文档软删除
    orphan = reconcile()
    if orphan["database"] or orphan["vector"]:
        logger.info("自动清理：FTS5 软删除 %d 条，向量库软删除 %d 条。", orphan["database"], orphan["vector"])

    # Git 提交
    if not no_git and (processed_count > 0 or reset):
        git_commit()


def git_commit():
    """自动提交变更到 Git。"""
    try:
        subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
        subprocess.run(
            ["git", "commit", "-m", "auto: pipeline run"],
            cwd=ROOT,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("未找到 git，跳过自动提交。")
    except subprocess.CalledProcessError as e:
        logger.warning("Git 提交失败：%s", e)


def show_status():
    init_db()
    files = list_inbox_files()
    record = load_processed()
    pending = 0
    for f in files:
        rel = str(f.relative_to(ROOT))
        mtime = str(f.stat().st_mtime)
        if record.get(rel) != mtime:
            pending += 1
    stats = get_stats()

    # 向量库状态
    vec_status = "未构建"
    from pewm.processors.vector_db import VectorDB
    if VectorDB is not None:
        try:
            vs = VectorDB().stats()
            vec_status = f"{vs['doc_count']} 条 / {vs['kind']} / {vs['dim']}维"
        except Exception as e:
            logger.warning("读取向量库状态失败: %s", e)

    logger.info("[status] Inbox 文件总数：%d", len(files))
    logger.info("[status] 待处理文件数：%d", pending)
    logger.info("[status] 已索引文档数：%d", stats['document_count'])
    logger.info("[status] 软删除文档数：%d", stats.get('deleted_count', 0))
    logger.info("[status] 向量库状态：%s", vec_status)
    logger.info("[status] 数据库：%s", stats['db_path'])


def main():
    parser = argparse.ArgumentParser(description="个人企业世界模型 AI 管线")
    parser.add_argument("--reset", action="store_true", help="重置处理标记并重建索引")
    parser.add_argument("--skip-errors", action="store_true", help="跳过冲突文件继续处理")
    parser.add_argument("--no-git", action="store_true", help="禁用 Git 自动提交")
    parser.add_argument("--no-vector", action="store_true", help="跳过向量索引（只用 FTS5）")
    parser.add_argument("--no-ocr", action="store_true", help="跳过图片 OCR 处理")
    parser.add_argument("--status", action="store_true", help="查看 Inbox 处理状态")
    parser.add_argument("--rebuild-vector", action="store_true", help="重建全部向量索引")
    parser.add_argument("--reconcile", action="store_true", help="扫描并软删除磁盘上已不存在的文档")
    parser.add_argument("--purge", action="store_true", help="硬删除所有软删除的文档（不可恢复！）")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.rebuild_vector:
        from pewm.processors.vectorizer import rebuild_vector  # type: ignore
        rebuild_vector()
    elif args.reconcile:
        result = reconcile()
        logger.info("reconcile 完成：FTS5 %d 条，向量库 %d 条", result['database'], result['vector'])
    elif args.purge:
        result = purge_orphans()
        logger.info("purge 完成：FTS5 永久删除 %d 条，向量库永久删除 %d 条", result['database'], result['vector'])
    else:
        run_pipeline(
            reset=args.reset,
            skip_errors=args.skip_errors,
            no_git=args.no_git,
            no_vector=args.no_vector,
            no_ocr=args.no_ocr,
        )


if __name__ == "__main__":
    main()
