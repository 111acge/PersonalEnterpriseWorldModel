"""Flask Web API，为 PEWM 提供 H5+CSS 前端所需的后端接口。"""
import io
import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

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
    app.config["JSON_AS_ASCII"] = False

    # 初始化数据库
    init_db()

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
        limit = int(request.args.get("limit", 100))
        try:
            data = get_recent(event=event, limit=limit)
            return jsonify(success=True, data=data)
        except Exception as e:
            logger.exception("获取指标失败")
            return jsonify(success=False, error=str(e)), 500

    @app.route("/api/metrics/summary")
    def api_metrics_summary():
        event = request.args.get("event")
        limit = int(request.args.get("limit", 100))
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
        safe = re.sub(r"[^\w\u4e00-\u9fff-]", "", title).strip("-") or "note"
        filename = f"{date_str}-{safe}.md"
        inbox_path = ROOT / "00-Inbox" / filename

        counter = 1
        original_path = inbox_path
        while inbox_path.exists():
            inbox_path = original_path.with_stem(f"{original_path.stem}-{counter}")
            counter += 1

        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        inbox_path.write_text(content + "\n", encoding="utf-8")
        return jsonify(success=True, path=str(inbox_path.relative_to(ROOT)))

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
        top_k = int(request.args.get("top_k", 10))
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
        session_id = (data.get("session_id") or "default").strip()
        if not q:
            return jsonify(success=False, error="问题不能为空"), 400

        def generate():
            from pewm.processors.rag import rag_answer_stream
            cfg = load_config()
            api_key = cfg.get("api_key")
            provider = cfg.get("provider")
            model = cfg.get("model")
            history = get_conversation_history(session_id, limit=10)

            full_answer = ""
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
                import json
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            # 保存对话历史
            add_conversation_message(session_id, "user", q)
            add_conversation_message(session_id, "assistant", full_answer)

        return Response(generate(), mimetype="text/event-stream")

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

    # ========== 管线 ==========
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
        if _pipeline_lock.locked():
            return jsonify(success=False, error="管线正在运行中"), 409
        with _pipeline_lock:
            _pipeline_logs["running"] = True
            _pipeline_logs["output"] = ""
        data = request.get_json() or {}
        options = data.get("options", ["--no-git", "--no-ocr"])

        def task():
            old_stdout = sys.stdout
            buffer = io.StringIO()
            sys.stdout = buffer
            try:
                import runpy
                argv = ["run.py"] + options
                sys.argv = argv
                runpy.run_path(str(ROOT / "run.py"), run_name="__main__")
                output = buffer.getvalue()
            except Exception as e:
                logger.exception("管线运行失败")
                output = f"管线运行失败：{e}\n"
            finally:
                sys.stdout = old_stdout
            _pipeline_logs["output"] += output
            _pipeline_logs["running"] = False
            invalidate_search_cache()

        threading.Thread(target=task, daemon=True).start()
        return jsonify(success=True, message="管线已启动")

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

        threading.Thread(target=task, daemon=True).start()
        return jsonify(success=True, message="OCR 已启动")

    # ========== 向量索引 ==========
    @app.route("/api/vector/rebuild", methods=["POST"])
    def api_vector_rebuild():
        def task():
            old_stdout = sys.stdout
            buffer = io.StringIO()
            sys.stdout = buffer
            try:
                from pewm.processors.vector_db import VectorDB
                from pewm.processors.vectorizer import rebuild_vector as do_rebuild
                init_db()
                vdb = VectorDB()
                vdb.ensure_loaded()
                do_rebuild()
                output = buffer.getvalue()
            except Exception as e:
                logger.exception("向量索引重建失败")
                output = f"向量索引重建失败：{e}\n"
            finally:
                sys.stdout = old_stdout
            invalidate_search_cache()
            _pipeline_logs["output"] += output + "\n向量索引重建完成。\n"

        threading.Thread(target=task, daemon=True).start()
        return jsonify(success=True, message="向量索引重建已启动")

    # ========== 配置：LLM ==========
    @app.route("/api/config/llm")
    def api_config_llm():
        return jsonify(success=True, data=load_config())

    @app.route("/api/config/llm", methods=["POST"])
    def api_config_llm_save():
        data = request.get_json() or {}
        cfg = {
            "provider": (data.get("provider") or "").strip(),
            "api_key": (data.get("api_key") or "").strip(),
            "base_url": (data.get("base_url") or "").strip(),
            "model": (data.get("model") or "").strip(),
        }
        old = load_config()
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
        ocr_cfg = {
            "mode": data.get("mode", "api"),
            "provider": data.get("provider", "baidu"),
            "credentials": data.get("credentials", {}),
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
    @app.route("/api/config/export", methods=["POST"])
    def api_config_export():
        data = request.get_json() or {}
        path = (data.get("path") or "").strip()
        if not path:
            return jsonify(success=False, error="缺少导出路径"), 400
        ok, msg = export_all(Path(path), include_api_keys=True)
        return jsonify(success=ok, message=msg)

    @app.route("/api/config/import", methods=["POST"])
    def api_config_import():
        data = request.get_json() or {}
        path = (data.get("path") or "").strip()
        if not path:
            return jsonify(success=False, error="缺少导入路径"), 400
        ok, msg = import_from(Path(path), overwrite=True)
        return jsonify(success=ok, message=msg)

    @app.route("/api/config/backup", methods=["POST"])
    def api_config_backup():
        data = request.get_json() or {}
        path = (data.get("path") or "").strip()
        if not path:
            return jsonify(success=False, error="缺少备份目录"), 400
        ok, msg = backup_to_dir(Path(path))
        return jsonify(success=ok, message=msg)

    # ========== 崩溃上报 ==========
    @app.route("/api/config/crash")
    def api_config_crash():
        cfg = load_config()
        return jsonify(success=True, data={
            "crash_reporting_enabled": bool(cfg.get("crash_reporting_enabled", False))
        })

    @app.route("/api/config/crash", methods=["POST"])
    def api_config_crash_save():
        data = request.get_json() or {}
        cfg = load_config()
        cfg["crash_reporting_enabled"] = bool(data.get("crash_reporting_enabled", False))
        save_config(cfg)
        return jsonify(success=True, message="崩溃上报设置已保存")

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
