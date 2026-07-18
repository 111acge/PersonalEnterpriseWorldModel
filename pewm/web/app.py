"""Flask Web API，为 PEWM 提供 H5+CSS 前端所需的后端接口。"""
import contextlib
import io
import json
import os
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, Response, jsonify, request, send_from_directory

from pewm.paths import ROOT
from pewm.processors.config_manager import backup_to_dir, export_all, import_from
from pewm.processors.database import (
    add_conversation_message,
    clear_conversation_history,
    get_conversation_history,
    get_document,
    get_stats,
    hard_delete_document,
    init_db,
    list_documents,
    restore_document,
    soft_delete_document,
)
from pewm.processors.llm_client import PROVIDERS, load_config, save_config, test_api
from pewm.processors.ocr_api import OCR_PROVIDERS, load_ocr_config, save_ocr_config, test_ocr_api
from pewm.processors.metrics import get_recent, get_summary
from pewm.processors.prompt_config import PROMPT_FIELDS, get_greeting, load_prompt, save_prompt
from pewm.processors.retrieval import invalidate_search_cache
from pewm.processors.torch_validator import get_torch_status
from pewm.processors.user_profile import load_profile, save_profile
from pewm.processors.log_config import get_logger

logger = get_logger(__name__)


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.json.ensure_ascii = False

    # 本次进程随机访问令牌：前端通过 /api/auth/token 获取后在 X-Token 头中携带
    app.config["API_TOKEN"] = secrets.token_hex(16)

    _ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

    @app.before_request
    def _security_guard():
        """仅允许本机访问；/api/* 校验令牌；非 GET 的 /api/* 强制 JSON 请求体。"""
        host = (request.host or "").split(":")[0].lower()
        if host not in _ALLOWED_HOSTS:
            return jsonify(success=False, error="仅允许本机访问"), 403
        if not request.path.startswith("/api/"):
            return None
        if request.method not in ("GET", "HEAD", "OPTIONS") and not request.is_json:
            return jsonify(success=False, error="请求体必须是 application/json"), 415
        if request.path != "/api/auth/token":
            token = request.headers.get("X-Token", "")
            if not token or not secrets.compare_digest(token, app.config["API_TOKEN"]):
                return jsonify(success=False, error="无效或缺失的访问令牌"), 401
        return None

    # 初始化数据库
    init_db()

    @app.route("/api/auth/token")
    def api_auth_token():
        """向前端发放本次进程的访问令牌（仅本机 Host 可达，受 CORS 同源限制保护）。"""
        return jsonify(success=True, data={"token": app.config["API_TOKEN"]})

    # ========== 页面 ==========
    @app.route("/")
    def index():
        return send_from_directory(app.template_folder, "index.html")

    @app.route("/error")
    def error_page():
        return send_from_directory(app.template_folder, "error.html")

    @app.errorhandler(404)
    def not_found(e):
        return send_from_directory(app.template_folder, "error.html"), 404

    # ========== 状态 ==========
    @app.route("/api/stats")
    def api_stats():
        try:
            stats = get_stats()
            return jsonify(success=True, data=stats)
        except Exception as e:
            logger.exception("获取统计信息失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/metrics")
    def api_metrics():
        event = request.args.get("event") or None
        limit = min(request.args.get("limit", 100, type=int) or 100, 1000)
        try:
            data = get_recent(event=event, limit=limit)
            return jsonify(success=True, data=data)
        except Exception as e:
            logger.exception("获取指标失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/metrics/summary")
    def api_metrics_summary():
        event = request.args.get("event")
        limit = min(request.args.get("limit", 100, type=int) or 100, 1000)
        if not event:
            return jsonify(success=False, error="缺少 event 参数"), 400
        try:
            data = get_summary(event=event, limit=limit)
            return jsonify(success=True, data=data)
        except Exception as e:
            logger.exception("获取指标摘要失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/torch/status")
    def api_torch_status():
        try:
            status = get_torch_status()
            return jsonify(success=True, data=status)
        except Exception as e:
            logger.exception("获取 torch 状态失败")
            return jsonify(success=False, error=str(e)), 500

    # ========== Inbox 速记 ==========
    @app.route("/api/inbox", methods=["POST"])
    def api_inbox_create():
        data = request.get_json() or {}
        title = (data.get("title") or "").strip()
        content = (data.get("content") or "").strip()
        if not title and not content:
            return jsonify(success=False, error="标题或内容至少填一项"), 400
        if not title:
            title = content.splitlines()[0][:20]

        date_str = datetime.now().strftime("%Y-%m-%d")
        safe = (re.sub(r"[^\w\u4e00-\u9fff-]", "", title).strip("-") or "note")[:50]

        inbox_dir = ROOT / "00-Inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        counter = 0
        while True:
            stem = f"{date_str}-{safe}" + (f"-{counter}" if counter else "")
            inbox_path = inbox_dir / f"{stem}.md"
            try:
                with open(inbox_path, "x", encoding="utf-8") as f:
                    f.write(content + "\n")
                break
            except FileExistsError:
                counter += 1
        return jsonify(success=True, path=str(inbox_path.relative_to(ROOT)))

    # ========== 笔记工作台 ==========
    # 笔记读取范围（相对于 ROOT）：速记 + AI 提炼生成的各知识层
    _NOTE_DIRS = ["00-Inbox", "10-Theory", "20-Ontology", "30-Instances"]

    def _resolve_note_path(rel_path: str):
        """将相对路径解析为 ROOT 下的安全绝对路径，越界返回 None。"""
        rel_path = (rel_path or "").strip().lstrip("/\\")
        if not rel_path:
            return None
        candidate = (ROOT / rel_path).resolve()
        root_resolved = ROOT.resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError:
            return None
        in_note_dirs = False
        for d in _NOTE_DIRS:
            try:
                candidate.relative_to(root_resolved / d)
                in_note_dirs = True
                break
            except ValueError:
                continue
        if not in_note_dirs:
            return None
        if candidate.suffix.lower() != ".md":
            return None
        return candidate

    def _note_meta(p: Path):
        """生成笔记列表项元数据。"""
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        stat = p.stat()
        return {
            "path": str(p.relative_to(ROOT)).replace("\\", "/"),
            "name": p.stem,
            "dir": p.parent.relative_to(ROOT).as_posix(),
            "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "preview": (lines[0] if lines else "")[:80],
            "tags": sorted(set(re.findall(r"#([\w\u4e00-\u9fff-]+)", text))),
        }

    @app.route("/api/notes")
    def api_notes():
        """列出笔记目录下的所有 Markdown 笔记。"""
        try:
            keyword = (request.args.get("keyword") or "").strip().lower()
            tag = (request.args.get("tag") or "").strip().lstrip("#")
            notes = []
            for d in _NOTE_DIRS:
                base = ROOT / d
                if not base.exists():
                    continue
                for p in sorted(base.rglob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
                    meta = _note_meta(p)
                    if keyword and keyword not in meta["name"].lower() and keyword not in meta["preview"].lower():
                        continue
                    if tag and tag not in meta["tags"]:
                        continue
                    notes.append(meta)
            return jsonify(success=True, data=notes[:500])
        except Exception as e:
            logger.exception("列出笔记失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/notes/content")
    def api_note_content():
        """读取单篇笔记内容。"""
        p = _resolve_note_path(request.args.get("path") or "")
        if not p or not p.exists():
            return jsonify(success=False, error="笔记不存在"), 404
        try:
            return jsonify(success=True, data={
                "path": str(p.relative_to(ROOT)).replace("\\", "/"),
                "name": p.stem,
                "content": p.read_text(encoding="utf-8", errors="replace"),
            })
        except Exception as e:
            logger.exception("读取笔记失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/notes/save", methods=["POST"])
    def api_note_save():
        """保存笔记：有 path 则覆盖，否则按标题新建到 00-Inbox。"""
        data = request.get_json() or {}
        content = data.get("content") or ""
        rel = (data.get("path") or "").strip()
        title = (data.get("title") or "").strip()
        try:
            if rel:
                p = _resolve_note_path(rel)
                if not p:
                    return jsonify(success=False, error="非法的笔记路径"), 400
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content.rstrip("\n") + "\n", encoding="utf-8")
            else:
                if not title:
                    first = next((l.strip() for l in content.splitlines() if l.strip()), "")
                    title = first[:20] or "未命名笔记"
                date_str = datetime.now().strftime("%Y-%m-%d")
                safe = (re.sub(r"[^\w\u4e00-\u9fff-]", "", title).strip("-") or "note")[:50]
                inbox_dir = ROOT / "00-Inbox"
                inbox_dir.mkdir(parents=True, exist_ok=True)
                counter = 0
                while True:
                    stem = f"{date_str}-{safe}" + (f"-{counter}" if counter else "")
                    p = inbox_dir / f"{stem}.md"
                    try:
                        with open(p, "x", encoding="utf-8") as f:
                            f.write(content.rstrip("\n") + "\n")
                        break
                    except FileExistsError:
                        counter += 1
            return jsonify(success=True, message="笔记已保存",
                           path=str(p.relative_to(ROOT)).replace("\\", "/"))
        except Exception as e:
            logger.exception("保存笔记失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/notes/delete", methods=["POST"])
    def api_note_delete():
        """删除单篇笔记文件。"""
        data = request.get_json() or {}
        p = _resolve_note_path(data.get("path") or "")
        if not p or not p.exists():
            return jsonify(success=False, error="笔记不存在"), 404
        try:
            p.unlink()
            return jsonify(success=True, message="笔记已删除")
        except Exception as e:
            logger.exception("删除笔记失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/tags")
    def api_tags():
        """从所有笔记内容中提取 #标签 及使用次数。"""
        try:
            counts: Dict[str, int] = {}
            for d in _NOTE_DIRS:
                base = ROOT / d
                if not base.exists():
                    continue
                for p in base.rglob("*.md"):
                    try:
                        text = p.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                    for t in set(re.findall(r"#([\w\u4e00-\u9fff-]+)", text)):
                        counts[t] = counts.get(t, 0) + 1
            tags = [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])]
            return jsonify(success=True, data=tags[:50])
        except Exception as e:
            logger.exception("提取标签失败")
            return jsonify(success=False, error=str(e)), 500

    # ========== 文档管理 ==========
    @app.route("/api/documents")
    def api_documents():
        include_deleted = request.args.get("include_deleted", "false").lower() == "true"
        entity_type = request.args.get("type") or None
        keyword = (request.args.get("keyword") or "").strip().lower()
        try:
            docs = list_documents(
                include_deleted=include_deleted,
                entity_type=entity_type,
                limit=50000,
            )
            if keyword:
                docs = [
                    d for d in docs
                    if keyword in (d.get("path") or "").lower()
                    or keyword in (d.get("title") or "").lower()
                    or keyword in (d.get("source") or "").lower()
                ]
            return jsonify(success=True, data=docs)
        except Exception as e:
            logger.exception("列出文档失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/documents/<path:doc_path>")
    def api_document_detail(doc_path):
        try:
            doc = get_document(doc_path)
            if not doc:
                return jsonify(success=False, error="文档不存在"), 404
            return jsonify(success=True, data=doc)
        except Exception as e:
            logger.exception("获取文档详情失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/documents/<path:doc_path>/soft_delete", methods=["POST"])
    def api_document_soft_delete(doc_path):
        try:
            n_db = 1 if soft_delete_document(doc_path) else 0
            from pewm.processors.vector_db import VectorDB
            vdb = VectorDB()
            n_vec = 1 if vdb.soft_delete(doc_path) else 0
            invalidate_search_cache()
            return jsonify(success=True, message=f"软删除：FTS5 {n_db} 条，向量库 {n_vec} 条")
        except Exception as e:
            logger.exception("软删除文档失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/documents/<path:doc_path>/restore", methods=["POST"])
    def api_document_restore(doc_path):
        try:
            n_db = 1 if restore_document(doc_path) else 0
            from pewm.processors.vector_db import VectorDB
            vdb = VectorDB()
            n_vec = 1 if vdb.restore(doc_path) else 0
            invalidate_search_cache()
            return jsonify(success=True, message=f"恢复：FTS5 {n_db} 条，向量库 {n_vec} 条")
        except Exception as e:
            logger.exception("恢复文档失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/documents/<path:doc_path>/hard_delete", methods=["POST"])
    def api_document_hard_delete(doc_path):
        try:
            n_db = 1 if hard_delete_document(doc_path) else 0
            from pewm.processors.vector_db import VectorDB
            vdb = VectorDB()
            n_vec = 1 if vdb.hard_delete(doc_path) else 0
            invalidate_search_cache()
            return jsonify(success=True, message=f"永久删除：FTS5 {n_db} 条，向量库 {n_vec} 条")
        except Exception as e:
            logger.exception("硬删除文档失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/documents/purge", methods=["POST"])
    def api_documents_purge():
        try:
            deleted = [
                d for d in list_documents(include_deleted=True, limit=100000)
                if d.get("deleted_at")
            ]
            n_db = sum(1 for d in deleted if hard_delete_document(d["path"]))
            from pewm.processors.vector_db import VectorDB
            vdb = VectorDB()
            n_vec = sum(1 for d in vdb.list_docs(include_deleted=True)
                        if d.get("deleted_at") and vdb.hard_delete(d["path"]))
            invalidate_search_cache()
            return jsonify(success=True, message=f"回收站已清空：FTS5 {n_db} 条，向量库 {n_vec} 条")
        except Exception as e:
            logger.exception("清空回收站失败")
            return jsonify(success=False, error=str(e)), 500

    # ========== 搜索 ==========
    @app.route("/api/search")
    def api_search():
        q = (request.args.get("q") or "").strip()
        entity_type = request.args.get("type") or None
        top_k = min(request.args.get("top_k", 10, type=int) or 10, 1000)
        if not q:
            return jsonify(success=False, error="缺少关键词"), 400
        try:
            from pewm.processors.retrieval import hybrid_search
            results = hybrid_search(q, entity_type=entity_type, top_k=top_k)
            return jsonify(success=True, data=results)
        except Exception as e:
            logger.exception("搜索失败")
            return jsonify(success=False, error=str(e)), 500

    # ========== 对话 ==========
    @app.route("/api/chat", methods=["POST"])
    def api_chat():
        data = request.get_json() or {}
        q = (data.get("q") or "").strip()
        entity_type = data.get("type") or None
        use_rag = data.get("use_rag", True)
        session_id = (data.get("session_id") or "default").strip()
        if not q:
            return jsonify(success=False, error="问题不能为空"), 400
        try:
            from pewm.processors.rag import rag_answer
            cfg = load_config()
            api_key = cfg.get("api_key") if use_rag else None
            provider = cfg.get("provider") if use_rag else None
            model = cfg.get("model") if use_rag else None
            history = get_conversation_history(session_id, limit=10)
            result = rag_answer(
                query=q,
                entity_type=entity_type or None,
                top_k=5,
                api_key=api_key,
                provider=provider,
                model=model,
                history=history,
            )
            # 保存用户问题和 AI 回答
            add_conversation_message(session_id, "user", q)
            add_conversation_message(session_id, "assistant", result.get("answer", ""))
            return jsonify(success=True, data=result)
        except Exception as e:
            logger.exception("对话失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/chat/stream", methods=["POST"])
    def api_chat_stream():
        data = request.get_json() or {}
        q = (data.get("q") or "").strip()
        entity_type = data.get("type") or None
        use_rag = data.get("use_rag", True)
        session_id = (data.get("session_id") or "default").strip()
        if not q:
            return jsonify(success=False, error="问题不能为空"), 400

        def generate():
            full_answer = ""
            try:
                from pewm.processors.rag import rag_answer_stream
                cfg = load_config()
                api_key = cfg.get("api_key") if use_rag else None
                provider = cfg.get("provider") if use_rag else None
                model = cfg.get("model") if use_rag else None
                history = get_conversation_history(session_id, limit=10)

                for chunk in rag_answer_stream(
                    query=q,
                    entity_type=entity_type or None,
                    top_k=5,
                    api_key=api_key,
                    provider=provider,
                    model=model,
                    history=history,
                ):
                    delta = chunk.get("delta", "")
                    full_answer += delta
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            except Exception as e:
                logger.exception("流式对话失败")
                err = {"delta": "", "done": True, "mode": "error", "error": str(e)}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            finally:
                # 断连/异常也把已生成的部分落库
                try:
                    add_conversation_message(session_id, "user", q)
                    if full_answer:
                        add_conversation_message(session_id, "assistant", full_answer)
                except Exception:
                    logger.exception("保存流式对话历史失败")

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.route("/api/chat/history")
    def api_chat_history():
        session_id = (request.args.get("session_id") or "default").strip()
        try:
            history = get_conversation_history(session_id, limit=100)
            return jsonify(success=True, data=history)
        except Exception as e:
            logger.exception("获取对话历史失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/chat/history/clear", methods=["POST"])
    def api_chat_history_clear():
        data = request.get_json() or {}
        session_id = (data.get("session_id") or "default").strip()
        try:
            clear_conversation_history(session_id)
            return jsonify(success=True, message="对话历史已清空")
        except Exception as e:
            logger.exception("清空对话历史失败")
            return jsonify(success=False, error=str(e)), 500

    # ========== 本体生成（原管线） ==========
    _pipeline_lock = threading.Lock()
    _pipeline_logs: Dict[str, Any] = {"running": False, "output": ""}

    @app.route("/api/pipeline/status")
    def api_pipeline_status():
        return jsonify(success=True, data={
            "running": _pipeline_logs["running"],
            "stats": get_stats(),
        })

    @app.route("/api/pipeline/run", methods=["POST"])
    def api_pipeline_run():
        data = request.get_json() or {}
        options = data.get("options", ["--no-git", "--no-ocr"])
        if not _pipeline_lock.acquire(blocking=False):
            return jsonify(success=False, error="本体生成正在运行中"), 409
        _pipeline_logs["running"] = True
        _pipeline_logs["output"] = ""

        def task():
            buffer = io.StringIO()
            try:
                # 直接调用本体生成函数，避免依赖磁盘上的 run.py（打包后不存在）
                from pewm.processors.__main__ import run_pipeline as do_run
                with contextlib.redirect_stdout(buffer):
                    do_run(
                        reset="--reset" in options,
                        skip_errors="--skip-errors" in options,
                        no_git="--no-git" in options,
                        no_vector="--no-vector" in options,
                        no_ocr="--no-ocr" in options,
                    )
                output = buffer.getvalue()
            except Exception as e:
                logger.exception("本体生成运行失败")
                output = f"本体生成运行失败：{e}\n"
            finally:
                _pipeline_logs["output"] += output
                _pipeline_logs["running"] = False
                invalidate_search_cache()
                _pipeline_lock.release()

        threading.Thread(target=task, daemon=True).start()
        return jsonify(success=True, message="本体生成已启动")

    @app.route("/api/pipeline/logs")
    def api_pipeline_logs():
        return jsonify(success=True, data={
            "running": _pipeline_logs["running"],
            "output": _pipeline_logs["output"],
        })

    # ========== 后台监听 ==========
    @app.route("/api/watcher/status")
    def api_watcher_status():
        from pewm.processors.watcher import get_watcher
        w = get_watcher()
        return jsonify(success=True, data={
            "running": w.running,
            "logs": w.get_logs(),
        })

    @app.route("/api/watcher/start", methods=["POST"])
    def api_watcher_start():
        from pewm.processors.watcher import get_watcher
        w = get_watcher()
        ok = w.start()
        return jsonify(success=ok, message="后台监听已启动" if ok else "监听已在运行")

    @app.route("/api/watcher/stop", methods=["POST"])
    def api_watcher_stop():
        from pewm.processors.watcher import get_watcher
        w = get_watcher()
        ok = w.stop()
        return jsonify(success=ok, message="后台监听已停止" if ok else "监听未运行")

    # ========== OCR 批量 ==========
    @app.route("/api/ocr/run", methods=["POST"])
    def api_ocr_run():
        if not _pipeline_lock.acquire(blocking=False):
            return jsonify(success=False, error="有任务正在运行中"), 409
        _pipeline_logs["running"] = True

        def task():
            try:
                from pewm.processors.ocr import list_media_files, process_all_media
                files = list_media_files()
                if not files:
                    _pipeline_logs["output"] += "_media/ 目录没有图片\n"
                    return
                results = process_all_media()
                lines = [f"- {img.name}: {text[:80].replace(chr(10), ' ')}" for img, text in results.items()]
                _pipeline_logs["output"] += f"已识别 {len(results)} 张图片：\n" + "\n".join(lines) + "\n"
            except Exception as e:
                logger.exception("批量 OCR 失败")
                _pipeline_logs["output"] += f"批量 OCR 失败：{e}\n"
            finally:
                _pipeline_logs["running"] = False
                _pipeline_lock.release()

        threading.Thread(target=task, daemon=True).start()
        return jsonify(success=True, message="OCR 已启动")

    # ========== 向量索引 ==========
    @app.route("/api/vector/rebuild", methods=["POST"])
    def api_vector_rebuild():
        if not _pipeline_lock.acquire(blocking=False):
            return jsonify(success=False, error="有任务正在运行中"), 409
        _pipeline_logs["running"] = True

        def task():
            buffer = io.StringIO()
            try:
                from pewm.processors.vector_db import VectorDB
                from pewm.processors.vectorizer import rebuild_vector as do_rebuild
                init_db()
                vdb = VectorDB()
                vdb.ensure_loaded()
                with contextlib.redirect_stdout(buffer):
                    do_rebuild()
                output = buffer.getvalue()
            except Exception as e:
                logger.exception("向量索引重建失败")
                output = f"向量索引重建失败：{e}\n"
            finally:
                invalidate_search_cache()
                _pipeline_logs["output"] += output + "\n向量索引重建完成。\n"
                _pipeline_logs["running"] = False
                _pipeline_lock.release()

        threading.Thread(target=task, daemon=True).start()
        return jsonify(success=True, message="向量索引重建已启动")

    # ========== 配置：LLM ==========
    def _mask_api_key(key: str) -> str:
        """API Key 打码：仅返回尾 4 位。"""
        if not key:
            return ""
        return "***" + key[-4:]

    @app.route("/api/config/llm")
    def api_config_llm():
        cfg = load_config()
        if cfg.get("api_key"):
            cfg["api_key"] = _mask_api_key(cfg["api_key"])
        return jsonify(success=True, data=cfg)

    @app.route("/api/config/llm", methods=["POST"])
    def api_config_llm_save():
        data = request.get_json() or {}
        old = load_config()
        new_key = (data.get("api_key") or "").strip()
        # 打码值或空值表示不修改原 key
        if not new_key or new_key.startswith("***"):
            new_key = old.get("api_key", "")
        cfg = {
            "provider": (data.get("provider") or "").strip(),
            "api_key": new_key,
            "base_url": (data.get("base_url") or "").strip(),
            "model": (data.get("model") or "").strip(),
        }
        if "ocr" in old:
            cfg["ocr"] = old["ocr"]
        save_config(cfg)
        return jsonify(success=True, message="LLM 配置已保存")

    @app.route("/api/config/llm/test", methods=["POST"])
    def api_config_llm_test():
        data = request.get_json() or {}
        provider = (data.get("provider") or "").strip()
        api_key = (data.get("api_key") or "").strip()
        base_url = (data.get("base_url") or "").strip() or None
        # 前端回填的可能是打码值，此时使用已保存的 key 测试
        if api_key.startswith("***"):
            api_key = load_config().get("api_key", "")
        if not provider or not api_key:
            return jsonify(success=False, error="请先选择提供商并填写 API Key"), 400
        try:
            result = test_api(provider, api_key, base_url)
            return jsonify(success=True, message=result)
        except Exception as e:
            logger.exception("LLM API 测试失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/config/providers")
    def api_config_providers():
        return jsonify(success=True, data=PROVIDERS)

    # ========== 配置：OCR ==========
    @app.route("/api/config/ocr")
    def api_config_ocr():
        return jsonify(success=True, data=load_ocr_config())

    @app.route("/api/config/ocr", methods=["POST"])
    def api_config_ocr_save():
        data = request.get_json() or {}
        mode = data.get("mode", "local")
        if mode not in ("local", "api"):
            return jsonify(success=False, error="非法的 OCR 模式"), 400
        provider = data.get("provider", "baidu")
        if provider not in OCR_PROVIDERS:
            return jsonify(success=False, error="非法的 OCR 提供商"), 400
        credentials = data.get("credentials", {})
        if not isinstance(credentials, dict):
            return jsonify(success=False, error="credentials 必须是对象"), 400
        ocr_cfg = {
            "mode": mode,
            "provider": provider,
            "credentials": credentials,
        }
        save_ocr_config(ocr_cfg)
        return jsonify(success=True, message="OCR 配置已保存")

    @app.route("/api/config/ocr/test", methods=["POST"])
    def api_config_ocr_test():
        data = request.get_json() or {}
        provider = data.get("provider")
        credentials = data.get("credentials", {})
        try:
            result = test_ocr_api(provider, credentials)
            return jsonify(success=True, message=result)
        except Exception as e:
            logger.exception("OCR API 测试失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/config/ocr/providers")
    def api_config_ocr_providers():
        return jsonify(success=True, data=OCR_PROVIDERS)

    # ========== 配置：用户画像 ==========
    @app.route("/api/config/profile")
    def api_config_profile():
        return jsonify(success=True, data=load_profile())

    @app.route("/api/config/profile", methods=["POST"])
    def api_config_profile_save():
        data = request.get_json() or {}
        save_profile(data)
        return jsonify(success=True, message="用户信息已保存")

    # ========== 配置：提示词 ==========
    @app.route("/api/config/prompt")
    def api_config_prompt():
        return jsonify(success=True, data=load_prompt())

    @app.route("/api/config/prompt", methods=["POST"])
    def api_config_prompt_save():
        data = request.get_json() or {}
        save_prompt(data)
        return jsonify(success=True, message="提示词已保存")

    @app.route("/api/config/prompt/fields")
    def api_config_prompt_fields():
        return jsonify(success=True, data=PROMPT_FIELDS)

    # ========== 配置：导入/导出/备份 ==========
    def _resolve_user_path(raw: str) -> Optional[Path]:
        """把用户输入的路径限制在用户目录下，拒绝 .. 穿越，越界返回 None。"""
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = Path.home() / candidate
            resolved = candidate.resolve()
            resolved.relative_to(Path.home().resolve())
        except (ValueError, OSError):
            return None
        return resolved

    @app.route("/api/config/export", methods=["POST"])
    def api_config_export():
        data = request.get_json() or {}
        path = _resolve_user_path(data.get("path"))
        if not path:
            return jsonify(success=False, error="导出路径无效：必须位于用户目录下"), 400
        include_api_keys = bool(data.get("include_api_keys", False))
        ok, msg = export_all(path, include_api_keys=include_api_keys)
        return jsonify(success=ok, message=msg)

    @app.route("/api/config/import", methods=["POST"])
    def api_config_import():
        data = request.get_json() or {}
        path = _resolve_user_path(data.get("path"))
        if not path:
            return jsonify(success=False, error="导入路径无效：必须位于用户目录下"), 400
        overwrite = bool(data.get("overwrite", False))
        ok, msg = import_from(path, overwrite=overwrite)
        return jsonify(success=ok, message=msg)

    @app.route("/api/config/backup", methods=["POST"])
    def api_config_backup():
        data = request.get_json() or {}
        path = _resolve_user_path(data.get("path"))
        if not path:
            return jsonify(success=False, error="备份路径无效：必须位于用户目录下"), 400
        ok, msg = backup_to_dir(path)
        return jsonify(success=ok, message=msg)

    @app.route("/api/environment/status")
    def api_environment_status():
        try:
            from pewm.processors.environment_status import get_environment_status
            status = get_environment_status()
            return jsonify(success=True, data=status)
        except Exception as e:
            logger.exception("获取环境状态失败")
            return jsonify(success=False, error=str(e)), 500

    # ========== 崩溃日志（仅本地查看） ==========
    @app.route("/api/crash/logs")
    def api_crash_logs():
        try:
            from pewm.processors.crash_handler import get_recent_crash_logs
            from pathlib import Path as _P
            logs = []
            for p in get_recent_crash_logs(limit=5):
                try:
                    logs.append({"file": _P(p).name, "content": _P(p).read_text(encoding="utf-8")})
                except Exception:
                    logs.append({"file": _P(p).name, "content": "(读取失败)"})
            return jsonify(success=True, data=logs)
        except Exception as e:
            logger.exception("读取崩溃日志失败")
            return jsonify(success=False, error=str(e)), 500

    # ========== 问候语 ==========
    @app.route("/api/greeting")
    def api_greeting():
        return jsonify(success=True, data=get_greeting())

    return app
