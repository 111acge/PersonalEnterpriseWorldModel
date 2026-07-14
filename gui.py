#!/usr/bin/env python3
"""个人企业世界模型 · 本地桌面界面（tkinter + RAG + OCR API）。

所有数据保存在用户目录的 ~/.enterprise_world_model/ 中，跨机器/跨 exe 保留。
"""
import io
import os
import re
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

if getattr(sys, "frozen", False):
    # PyInstaller onefile 模式：__file__ 指向 TEMP/_MEI，必须用 sys.executable.parent
    ROOT = Path(sys.executable).resolve().parent
    # _MEI 是解压目录，包含 .pipeline/ 和 90-Meta/ 的完整子目录结构
    _MEI = Path(sys._MEIPASS).resolve()
else:
    ROOT = Path(__file__).resolve().parent
    _MEI = ROOT

# 无论哪种模式，都要把子模块目录加入 sys.path
sys.path.insert(0, str(_MEI / ".pipeline" / "processors"))
sys.path.insert(0, str(_MEI / "90-Meta" / "query"))

from chat import chat as chat_answer
from config_manager import backup_to_dir, export_all, import_from
from database import get_stats, init_db, search_documents
from llm_client import PROVIDERS, load_config, save_config, test_api
from ocr_api import OCR_PROVIDERS, load_ocr_config, save_ocr_config, test_ocr_api
from progress_dialog import ProgressDialog
from prompt_config import PROMPT_FIELDS, get_greeting, load_prompt, save_prompt
from search import search as search_docs
from user_profile import load_profile, save_profile
from utils import ROOT as PROJECT_ROOT


# ========== Chat Tab ==========

class ChatTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        parent.add(self.frame, text="💬 对话")

        top = ttk.Frame(self.frame)
        top.pack(fill="x", padx=10, pady=(10, 0))
        ttk.Label(top, text="问题：").pack(side="left")
        self.question = ttk.Entry(top)
        self.question.pack(side="left", fill="x", expand=True, padx=(5, 5))
        self.ask_btn = ttk.Button(top, text="发送", command=self.ask)
        self.ask_btn.pack(side="left")
        self.question.bind("<Return>", lambda e: self.ask())

        filter_frame = ttk.Frame(self.frame)
        filter_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(filter_frame, text="层级：").pack(side="left")
        self.layer = ttk.Combobox(filter_frame, values=["", "term", "constant", "case", "process", "system", "skill"], width=12, state="readonly")
        self.layer.set("")
        self.layer.pack(side="left", padx=(0, 10))
        self.use_rag = tk.BooleanVar(value=True)
        ttk.Checkbutton(filter_frame, text="RAG 生成", variable=self.use_rag).pack(side="left")

        self.answer_area = scrolledtext.ScrolledText(self.frame, wrap="word", state="disabled", font=("Microsoft YaHei", 11))
        self.answer_area.pack(fill="both", expand=True, padx=10, pady=10)

        greeting = get_greeting()
        self.append_system(greeting)

    def append_system(self, text):
        self.answer_area.config(state="normal")
        self.answer_area.insert("end", f"🤖 {text}\n\n")
        self.answer_area.config(state="disabled")
        self.answer_area.see("end")

    def ask(self):
        q = self.question.get().strip()
        if not q:
            return
        self.append_system(f"你：{q}")
        self.question.delete(0, "end")
        self.ask_btn.config(state="disabled")

        cfg = load_config()
        api_key = cfg.get("api_key") if self.use_rag.get() else None
        provider = cfg.get("provider") if self.use_rag.get() else None
        model = cfg.get("model") if self.use_rag.get() else None

        # 弹出小型进度提示（在对话区域内显示「正在检索...」）
        self.answer_area.config(state="normal")
        marker = self.answer_area.index("end-1c")
        self.answer_area.insert("end", "⏳ 正在检索并生成回答...\n")
        self.answer_area.config(state="disabled")
        self.answer_area.see("end")

        def task():
            try:
                if self.use_rag.get() and api_key:
                    answer = chat_answer(query=q, entity_type=self.layer.get() or None, top_k=5,
                                         api_key=api_key, provider=provider, model=model)
                else:
                    answer = chat_answer(query=q, entity_type=self.layer.get() or None, top_k=5)
            except Exception as e:
                answer = f"出错了：{e}"
            self.frame.after(0, lambda: self._show_answer(answer, marker))

        threading.Thread(target=task, daemon=True).start()

    def _show_answer(self, answer, marker):
        # 删除之前的"正在检索..."占位
        self.answer_area.config(state="normal")
        self.answer_area.delete(marker, "end-1c")
        self.answer_area.config(state="disabled")
        self.append_system(answer)
        self.ask_btn.config(state="normal")


# ========== Search Tab ==========

class SearchTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        parent.add(self.frame, text="🔍 检索")

        top = ttk.Frame(self.frame)
        top.pack(fill="x", padx=10, pady=(10, 0))
        ttk.Label(top, text="关键词：").pack(side="left")
        self.keyword = ttk.Entry(top)
        self.keyword.pack(side="left", fill="x", expand=True, padx=(5, 5))
        self.search_btn = ttk.Button(top, text="搜索", command=self.do_search)
        self.search_btn.pack(side="left")
        self.keyword.bind("<Return>", lambda e: self.do_search())

        filter_frame = ttk.Frame(self.frame)
        filter_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(filter_frame, text="层级：").pack(side="left")
        self.layer = ttk.Combobox(filter_frame, values=["", "term", "constant", "case", "process", "system", "skill"], width=12, state="readonly")
        self.layer.set("")
        self.layer.pack(side="left", padx=(0, 10))

        self.result_area = scrolledtext.ScrolledText(self.frame, wrap="word", state="disabled", font=("Microsoft YaHei", 10))
        self.result_area.pack(fill="both", expand=True, padx=10, pady=10)

    def append(self, text):
        self.result_area.config(state="normal")
        self.result_area.insert("end", text + "\n\n")
        self.result_area.config(state="disabled")
        self.result_area.see("end")

    def do_search(self):
        q = self.keyword.get().strip()
        if not q:
            return
        self.result_area.config(state="normal")
        self.result_area.delete("1.0", "end")
        self.result_area.config(state="disabled")
        self.append(f"搜索：{q}")
        self.search_btn.config(state="disabled")

        def task():
            try:
                results = search_docs(query=q, entity_type=self.layer.get() or None, top_k=10)
            except Exception as e:
                self.frame.after(0, lambda: self.append(f"搜索失败：{e}"))
                self.frame.after(0, lambda: self.search_btn.config(state="normal"))
                return
            self.frame.after(0, lambda: self._show_results(results))

        threading.Thread(target=task, daemon=True).start()

    def _show_results(self, results):
        if not results:
            self.append("未找到相关知识。")
        else:
            for i, r in enumerate(results, 1):
                preview = r.get("content", "").replace("\n", " ")[:200]
                source = r.get("source", "?")
                score = r.get("score")
                score_str = f" [{source}, score: {score:.4f}]" if score is not None else f" [{source}]"
                self.append(f"{i}. {score_str} {r.get('path', '')}\n   \"{preview}...\"")
        self.search_btn.config(state="normal")


# ========== Inbox Tab ==========

class InboxTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        parent.add(self.frame, text="📝 Inbox 速记")

        ttk.Label(self.frame, text="标题（可选）：").pack(anchor="w", padx=10, pady=(10, 0))
        self.title = ttk.Entry(self.frame)
        self.title.pack(fill="x", padx=10, pady=5)

        ttk.Label(self.frame, text="内容：").pack(anchor="w", padx=10)
        self.content = scrolledtext.ScrolledText(self.frame, wrap="word", height=12, font=("Microsoft YaHei", 11))
        self.content.pack(fill="both", expand=True, padx=10, pady=5)

        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(fill="x", padx=10, pady=10)
        self.save_btn = ttk.Button(btn_frame, text="投递到 Inbox", command=self.save)
        self.save_btn.pack(side="left")

    def sanitize_filename(self, name: str) -> str:
        name = re.sub(r"[^\w\u4e00-\u9fff-]", "", name)
        return name.strip("-") or "note"

    def save(self):
        title = self.title.get().strip()
        content = self.content.get("1.0", "end").strip()
        if not title and not content:
            messagebox.showwarning("提示", "标题或内容至少填一项")
            return
        if not title:
            title = content.splitlines()[0][:20]

        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str}-{self.sanitize_filename(title)}.md"
        inbox_path = PROJECT_ROOT / "00-Inbox" / filename

        counter = 1
        original_path = inbox_path
        while inbox_path.exists():
            inbox_path = original_path.with_stem(f"{original_path.stem}-{counter}")
            counter += 1

        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        inbox_path.write_text(content + "\n", encoding="utf-8")

        self.title.delete(0, "end")
        self.content.delete("1.0", "end")
        messagebox.showinfo("已保存", f"已投递到：\n{inbox_path.relative_to(PROJECT_ROOT)}")


# ========== Pipeline Tab（带进度条）==========

class PipelineTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.parent = parent
        parent.add(self.frame, text="⚙️ 管线")

        self.status_label = ttk.Label(self.frame, text="状态：准备就绪", font=("Microsoft YaHei", 11))
        self.status_label.pack(anchor="w", padx=10, pady=(10, 5))

        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(fill="x", padx=10, pady=5)
        self.run_btn = ttk.Button(btn_frame, text="▶ 运行 AI 管线", command=self.run_pipeline)
        self.run_btn.pack(side="left", padx=(0, 10))
        self.ocr_btn = ttk.Button(btn_frame, text="📷 批量 OCR", command=self.run_batch_ocr)
        self.ocr_btn.pack(side="left", padx=(0, 10))
        self.refresh_btn = ttk.Button(btn_frame, text="刷新状态", command=self.refresh_status)
        self.refresh_btn.pack(side="left")
        self.rebuild_btn = ttk.Button(btn_frame, text="重建向量索引", command=self.rebuild_vector)
        self.rebuild_btn.pack(side="right")

        self.log_area = scrolledtext.ScrolledText(self.frame, wrap="word", state="disabled", font=("Consolas", 10), height=16)
        self.log_area.pack(fill="both", expand=True, padx=10, pady=10)

        self.refresh_status()

    def log(self, text):
        self.log_area.config(state="normal")
        self.log_area.insert("end", text)
        self.log_area.config(state="disabled")
        self.log_area.see("end")

    def refresh_status(self):
        try:
            init_db()
            stats = get_stats()
            self.status_label.config(text=f"Inbox 已处理：{stats['inbox_total']}  |  已索引文档：{stats['document_count']}")
        except Exception as e:
            self.status_label.config(text=f"状态获取失败：{e}")

    def run_pipeline(self):
        self.run_btn.config(state="disabled")
        dlg = ProgressDialog(self.frame, title="运行 AI 管线", total=0, message="正在准备...")

        def progress_callback(current, total, message):
            dlg.update(current, message)

        def task():
            import runpy
            old_stdout = sys.stdout
            buffer = io.StringIO()
            sys.stdout = buffer
            try:
                progress_callback(0, 0, "正在扫描 Inbox...")
                runpy.run_path(str(PROJECT_ROOT / "run.py"), run_name="__main__")
                output = buffer.getvalue()
            except Exception as e:
                output = f"管线运行失败：{e}\n"
            finally:
                sys.stdout = old_stdout
            self.frame.after(0, lambda: self._pipeline_done(output, dlg))

        threading.Thread(target=task, daemon=True).start()

    def _pipeline_done(self, output, dlg):
        dlg.finish("管线运行完成")
        self.log(output)
        self.log("\n管线运行完成。\n")
        self.run_btn.config(state="normal")
        self.refresh_status()

    def run_batch_ocr(self):
        """批量 OCR 识别 00-Inbox/_media/ 下所有图片，带进度条。"""
        self.ocr_btn.config(state="disabled")
        dlg = ProgressDialog(self.frame, title="批量 OCR", total=0, message="正在扫描 _media/ ...")

        def task():
            try:
                from ocr import process_all_media, list_media_files
                from ocr_api import load_ocr_config
                cfg = load_ocr_config()
                if cfg.get("mode") == "local":
                    from ocr import is_local_available
                    if not is_local_available():
                        self.frame.after(0, lambda: messagebox.showwarning(
                            "提示", "本地模式需要安装 paddlepaddle+paddleocr\n\n"
                                    "pip install paddlepaddle paddleocr"))
                        dlg.finish("未安装本地 OCR")
                        return

                files = list_media_files()
                if not files:
                    self.frame.after(0, lambda: messagebox.showinfo("提示", "_media/ 目录没有图片"))
                    dlg.finish("无图片")
                    return

                results = process_all_media(progress_callback=lambda c, t, m: dlg.update(c, m))
                # 把结果写到日志区
                summary_lines = []
                for img, text in results.items():
                    preview = text[:80].replace("\n", " ") if text else "(空)"
                    summary_lines.append(f"- {img.name}: {preview}")
                summary = f"已识别 {len(results)} 张图片：\n" + "\n".join(summary_lines)
                self.frame.after(0, lambda: self.log(summary + "\n\n"))
                dlg.finish(f"完成：识别 {len(results)} 张图片")
            except Exception as e:
                self.frame.after(0, lambda: messagebox.showerror("批量 OCR 失败", str(e)))
                dlg.finish(f"失败：{e}")
            finally:
                self.frame.after(0, lambda: self.ocr_btn.config(state="normal"))

        threading.Thread(target=task, daemon=True).start()

    def rebuild_vector(self):
        self.rebuild_btn.config(state="disabled")
        dlg = ProgressDialog(self.frame, title="重建向量索引", total=100, message="正在准备...")

        def task():
            old_stdout = sys.stdout
            buffer = io.StringIO()
            sys.stdout = buffer
            try:
                from vector_db import VectorDB
                init_db()

                # 第一步：触发 embedding 模型下载（带进度）
                dlg.update(0, "正在检查 embedding 模型...")
                vdb = VectorDB()

                def on_download(loaded_mb, total_mb, msg):
                    pct = int(min(loaded_mb / max(total_mb, 1) * 40, 40))  # 前 40% 留给下载
                    dlg.update(pct, msg)

                vdb.ensure_loaded(download_progress_cb=on_download)
                dlg.update(40, "模型就绪，开始重建索引...")

                # 第二步：真正重建
                def on_rebuild_progress(current, total, msg):
                    if total > 0:
                        pct = 40 + int(current / total * 60)
                    else:
                        pct = 50
                    dlg.update(pct, msg)

                from vectorizer import rebuild_vector as do_rebuild
                do_rebuild()
                output = buffer.getvalue()
            except Exception as e:
                output = f"向量索引重建失败：{e}\n"
            finally:
                sys.stdout = old_stdout
            self.frame.after(0, lambda: self._rebuild_done(output, dlg))

        threading.Thread(target=task, daemon=True).start()

    def _rebuild_done(self, output, dlg):
        dlg.finish("向量索引重建完成")
        self.log(output)
        self.log("\n向量索引重建完成。\n")
        self.rebuild_btn.config(state="normal")
        self.refresh_status()


# ========== 文档管理 Tab ==========

class DocumentsTab:
    """文档管理 Tab：查看所有已索引文档，支持软删/恢复/硬删/批量操作。"""

    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        parent.add(self.frame, text="📚 文档管理")

        # 顶部工具栏
        toolbar = ttk.Frame(self.frame)
        toolbar.pack(fill="x", padx=10, pady=(10, 5))

        ttk.Label(toolbar, text="搜索：").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_list())
        ttk.Entry(toolbar, textvariable=self.search_var, width=20).pack(side="left", padx=(0, 10))

        ttk.Label(toolbar, text="类型：").pack(side="left")
        self.type_var = tk.StringVar()
        self.type_var.trace_add("write", lambda *_: self.refresh_list())
        ttk.Combobox(toolbar, textvariable=self.type_var,
                     values=["", "term", "constant", "case", "process", "system", "skill"],
                     width=10, state="readonly").pack(side="left", padx=(0, 10))

        self.show_deleted_var = tk.BooleanVar(value=False)
        self.show_deleted_var.trace_add("write", lambda *_: self.refresh_list())
        ttk.Checkbutton(toolbar, text="显示回收站", variable=self.show_deleted_var).pack(side="left")

        ttk.Button(toolbar, text="刷新", command=self.refresh_list).pack(side="right")

        # Treeview 文档列表
        columns = ("path", "entity_type", "updated_at", "status", "source")
        self.tree = ttk.Treeview(self.frame, columns=columns, show="headings",
                                 selectmode="extended")
        self.tree.heading("path", text="文档路径")
        self.tree.heading("entity_type", text="类型")
        self.tree.heading("updated_at", text="更新时间")
        self.tree.heading("status", text="状态")
        self.tree.heading("source", text="来源")
        self.tree.column("path", width=280, anchor="w")
        self.tree.column("entity_type", width=80, anchor="center")
        self.tree.column("updated_at", width=140, anchor="w")
        self.tree.column("status", width=80, anchor="center")
        self.tree.column("source", width=180, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=5)

        # 双击查看内容
        self.tree.bind("<Double-Button-1>", self.on_double_click)

        # 底部统计和操作按钮
        bottom = ttk.Frame(self.frame)
        bottom.pack(fill="x", padx=10, pady=(0, 10))

        self.status_label = ttk.Label(bottom, text="")
        self.status_label.pack(side="left")

        ttk.Button(bottom, text="🗑 软删除（进回收站）",
                   command=self.soft_delete_selected).pack(side="right", padx=(5, 0))
        ttk.Button(bottom, text="♻️ 恢复选中",
                   command=self.restore_selected).pack(side="right", padx=(5, 0))
        ttk.Button(bottom, text="💣 硬删除（不可恢复）",
                   command=self.hard_delete_selected).pack(side="right", padx=(5, 0))
        ttk.Button(bottom, text="清空回收站",
                   command=self.purge_all_deleted).pack(side="right")

        self.refresh_list()

    def refresh_list(self):
        """根据当前筛选条件刷新文档列表。"""
        from database import init_db, list_documents, get_stats
        init_db()
        # 清空现有行
        for item in self.tree.get_children():
            self.tree.delete(item)

        include_deleted = self.show_deleted_var.get()
        entity_type = self.type_var.get() or None
        keyword = self.search_var.get().strip().lower()

        docs = list_documents(include_deleted=include_deleted,
                              entity_type=entity_type,
                              limit=50000)

        # 客户端过滤：关键词匹配 path/title/source
        if keyword:
            docs = [d for d in docs
                    if keyword in (d.get("path") or "").lower()
                    or keyword in (d.get("title") or "").lower()
                    or keyword in (d.get("source") or "").lower()]

        truncated = len(docs) >= 50000
        for d in docs:
            deleted_at = d.get("deleted_at") or ""
            status = "🗑 回收站" if deleted_at else "✅ 正常"
            self.tree.insert("", "end", values=(
                d.get("path", ""),
                d.get("entity_type", ""),
                d.get("updated_at", ""),
                status,
                d.get("source", ""),
            ))

        # 更新统计
        try:
            stats = get_stats()
            active = stats.get("document_count", 0)
            deleted = stats.get("deleted_count", 0)
            msg = f"当前显示 {len(docs)} 条  |  正常 {active}  |  回收站 {deleted}"
            if truncated:
                msg += "  ⚠ 结果已截断（>50000 条），请用搜索/类型过滤"
            self.status_label.config(text=msg)
        except Exception as e:
            self.status_label.config(text=f"统计失败：{e}")

    def _get_selected_paths(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先在列表中选择一行或多行文档")
            return []
        paths = []
        for item in selected:
            values = self.tree.item(item, "values")
            if values:
                paths.append(values[0])
        return paths

    def on_double_click(self, event):
        """双击弹出文档内容查看对话框。"""
        from database import get_document
        item = self.tree.identify_row(event.y)
        if not item:
            return
        values = self.tree.item(item, "values")
        if not values:
            return
        path = values[0]
        doc = get_document(path)
        if not doc:
            messagebox.showwarning("提示", "文档不存在或已被清理")
            return

        # 弹出查看窗口
        win = tk.Toplevel(self.frame)
        win.title(f"文档详情：{path}")
        win.geometry("700x500")

        meta = (
            f"路径：{doc.get('path', '')}\n"
            f"类型：{doc.get('entity_type', '')}   来源：{doc.get('source', '')}\n"
            f"更新时间：{doc.get('updated_at', '')}   "
            f"删除状态：{'已软删除 @ ' + (doc.get('deleted_at') or '') if doc.get('deleted_at') else '正常'}\n"
            f"{'=' * 60}\n\n"
        )
        text = scrolledtext.ScrolledText(win, wrap="word", font=("Microsoft YaHei", 10))
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.insert("end", meta)
        text.insert("end", doc.get("content", ""))
        text.config(state="disabled")

    def soft_delete_selected(self):
        from database import soft_delete_document
        from vector_db import VectorDB
        paths = self._get_selected_paths()
        if not paths:
            return
        if not messagebox.askyesno("确认软删除",
                                    f"将把 {len(paths)} 篇文档移入回收站。\n\n"
                                    "文档不会被永久删除，可随时恢复。\n继续？"):
            return
        n_db = sum(1 for p in paths if soft_delete_document(p))
        vdb = VectorDB()
        n_vec = sum(1 for p in paths if vdb.soft_delete(p))
        messagebox.showinfo("完成", f"软删除：FTS5 {n_db} 条，向量库 {n_vec} 条")
        self.refresh_list()

    def restore_selected(self):
        from database import restore_document
        from vector_db import VectorDB
        paths = self._get_selected_paths()
        if not paths:
            return
        n_db = sum(1 for p in paths if restore_document(p))
        vdb = VectorDB()
        n_vec = sum(1 for p in paths if vdb.restore(p))
        messagebox.showinfo("完成", f"恢复：FTS5 {n_db} 条，向量库 {n_vec} 条")
        self.refresh_list()

    def hard_delete_selected(self):
        from database import hard_delete_document
        from vector_db import VectorDB
        paths = self._get_selected_paths()
        if not paths:
            return
        if not messagebox.askyesno("⚠️ 确认硬删除（不可恢复！）",
                                    f"将永久删除 {len(paths)} 篇文档，从 FTS5 和向量库中彻底抹掉。\n\n"
                                    "此操作不可逆！建议先点「清空回收站」旁的「备份配置目录」。\n\n继续？"):
            return
        n_db = sum(1 for p in paths if hard_delete_document(p))
        vdb = VectorDB()
        n_vec = sum(1 for p in paths if vdb.hard_delete(p))
        messagebox.showinfo("完成", f"永久删除：FTS5 {n_db} 条，向量库 {n_vec} 条")
        self.refresh_list()

    def purge_all_deleted(self):
        """一键清空回收站（硬删除所有软删除的文档）。"""
        from database import list_documents, hard_delete_document
        from vector_db import VectorDB
        deleted = [d for d in list_documents(include_deleted=True, limit=100000)
                   if d.get("deleted_at")]
        if not deleted:
            messagebox.showinfo("提示", "回收站为空，无需清理")
            return
        if not messagebox.askyesno("⚠️ 确认清空回收站",
                                    f"将永久删除 {len(deleted)} 篇已软删除的文档。\n\n"
                                    "此操作不可逆！继续？"):
            return
        n_db = sum(1 for d in deleted if hard_delete_document(d["path"]))
        vdb = VectorDB()
        n_vec = sum(1 for d in vdb.list_docs(include_deleted=True)
                    if d.get("deleted_at") and vdb.hard_delete(d["path"]))
        messagebox.showinfo("完成", f"回收站已清空：FTS5 {n_db} 条，向量库 {n_vec} 条")
        self.refresh_list()


# ========== API 配置 Tab ==========

class ApiConfigTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        parent.add(self.frame, text="⚙️ API 配置")

        ttk.Label(self.frame, text="LLM 提供商：").pack(anchor="w", padx=15, pady=(15, 2))
        self.provider_var = tk.StringVar()
        self.provider_combo = ttk.Combobox(self.frame, textvariable=self.provider_var,
                                           values=list(PROVIDERS.keys()), width=20, state="readonly")
        self.provider_combo.set("")
        self.provider_combo.pack(anchor="w", padx=15, pady=2)
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_change)
        ttk.Label(self.frame, text="选择提供商后，base_url 和默认模型会自动填充", foreground="gray").pack(anchor="w", padx=15, pady=0)

        ttk.Label(self.frame, text="API Key：").pack(anchor="w", padx=15, pady=(12, 2))
        self.api_key_var = tk.StringVar()
        self.api_key_entry = ttk.Entry(self.frame, textvariable=self.api_key_var, width=40)
        self.api_key_entry.pack(anchor="w", padx=15, pady=2)
        self.show_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.frame, text="显示 Key", variable=self.show_key_var, command=self._toggle_key).pack(anchor="w", padx=15)

        ttk.Label(self.frame, text="Base URL：").pack(anchor="w", padx=15, pady=(12, 2))
        self.base_url_var = tk.StringVar()
        ttk.Entry(self.frame, textvariable=self.base_url_var, width=50).pack(anchor="w", padx=15, pady=2)

        ttk.Label(self.frame, text="模型名：").pack(anchor="w", padx=15, pady=(12, 2))
        self.model_var = tk.StringVar()
        ttk.Entry(self.frame, textvariable=self.model_var, width=30).pack(anchor="w", padx=15, pady=2)

        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(fill="x", padx=15, pady=(15, 5))
        ttk.Button(btn_frame, text="保存配置", command=self.save_config).pack(side="left", padx=(0, 10))
        ttk.Button(btn_frame, text="测试连通", command=self.test_connection).pack(side="left")
        ttk.Button(btn_frame, text="清空配置", command=self.clear_config).pack(side="left", padx=(10, 0))

        self.status_label = ttk.Label(self.frame, text="", foreground="blue")
        self.status_label.pack(anchor="w", padx=15, pady=5)

        self._load_config()

    def _on_provider_change(self, event=None):
        p = self.provider_var.get()
        if p in PROVIDERS:
            self.base_url_var.set(PROVIDERS[p]["base_url"])
            self.model_var.set(PROVIDERS[p]["default_model"])

    def _load_config(self):
        cfg = load_config()
        self.provider_var.set(cfg.get("provider", ""))
        self.api_key_var.set(cfg.get("api_key", ""))
        self.base_url_var.set(cfg.get("base_url", ""))
        self.model_var.set(cfg.get("model", ""))
        if self.api_key_var.get():
            self.api_key_entry.config(show="*")

    def _toggle_key(self):
        self.api_key_entry.config(show="" if self.show_key_var.get() else "*")

    def save_config(self):
        cfg = {
            "provider": self.provider_var.get().strip(),
            "api_key": self.api_key_var.get().strip(),
            "base_url": self.base_url_var.get().strip(),
            "model": self.model_var.get().strip(),
        }
        # 保留已有的 OCR 配置
        old = load_config()
        if "ocr" in old:
            cfg["ocr"] = old["ocr"]
        if not cfg["provider"] and not cfg["api_key"]:
            messagebox.showwarning("提示", "请至少选择提供商或填写 API Key")
            return
        save_config(cfg)
        messagebox.showinfo("成功", "LLM 配置已保存。重启后依然有效。")
        self._load_config()

    def test_connection(self):
        provider = self.provider_var.get().strip()
        api_key = self.api_key_var.get().strip()
        base_url = self.base_url_var.get().strip()
        if not provider or not api_key:
            messagebox.showwarning("提示", "请先选择提供商并填写 API Key")
            return
        self.status_label.config(text="正在测试...", foreground="blue")
        self.frame.update_idletasks()

        def task():
            try:
                result = test_api(provider, api_key, base_url if base_url else None)
                self.frame.after(0, lambda: self._on_test_done(result))
            except Exception as e:
                self.frame.after(0, lambda: self._on_test_done(f"ERROR: {e}"))

        threading.Thread(target=task, daemon=True).start()

    def _on_test_done(self, result):
        if result.startswith("OK:"):
            self.status_label.config(text=f"✅ 连通成功！{result[4:]}", foreground="green")
        else:
            self.status_label.config(text=f"❌ 失败: {result[7:] if result.startswith('ERROR:') else result}", foreground="red")

    def clear_config(self):
        # 保留 OCR 配置
        old = load_config()
        save_config({"ocr": old.get("ocr", {})})
        self.provider_var.set("")
        self.api_key_var.set("")
        self.base_url_var.set("")
        self.model_var.set("")
        self.status_label.config(text="配置已清空", foreground="blue")


# ========== 用户信息 Tab ==========

class UserProfileTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        parent.add(self.frame, text="👤 用户信息")

        personal_frame = ttk.LabelFrame(self.frame, text="个人信息")
        personal_frame.pack(fill="x", padx=15, pady=10)

        self._fields = {}
        personal_fields = [
            ("personal_name", "姓名"),
            ("personal_role", "职位/角色"),
            ("personal_dept", "部门"),
            ("personal_email", "邮箱"),
            ("personal_phone", "电话"),
        ]
        for key, label in personal_fields:
            row_frame = ttk.Frame(personal_frame)
            row_frame.pack(fill="x", padx=10, pady=3)
            ttk.Label(row_frame, text=f"{label}：", width=10).pack(side="left")
            entry = ttk.Entry(row_frame, width=40)
            entry.pack(side="left", fill="x", expand=True, padx=(5, 0))
            self._fields[key] = entry

        row = ttk.Frame(personal_frame)
        row.pack(fill="x", padx=10, pady=3)
        ttk.Label(row, text="个人简介：", width=10).pack(side="left", anchor="n")
        self._fields["personal_bio"] = scrolledtext.ScrolledText(row, wrap="word", height=3, width=40)
        self._fields["personal_bio"].pack(side="left", fill="x", expand=True, padx=(5, 0))

        company_frame = ttk.LabelFrame(self.frame, text="公司信息")
        company_frame.pack(fill="x", padx=15, pady=10)
        company_fields = [
            ("company_name", "公司名称"),
            ("company_industry", "行业"),
            ("company_scale", "规模"),
            ("company_products", "产品/服务"),
            ("company_address", "地址"),
            ("company_website", "网址"),
        ]
        for key, label in company_fields:
            row_frame = ttk.Frame(company_frame)
            row_frame.pack(fill="x", padx=10, pady=3)
            ttk.Label(row_frame, text=f"{label}：", width=12).pack(side="left")
            entry = ttk.Entry(row_frame, width=40)
            entry.pack(side="left", fill="x", expand=True, padx=(5, 0))
            self._fields[key] = entry

        row = ttk.Frame(company_frame)
        row.pack(fill="x", padx=10, pady=3)
        ttk.Label(row, text="公司简介：", width=12).pack(side="left", anchor="n")
        self._fields["company_bio"] = scrolledtext.ScrolledText(row, wrap="word", height=3, width=40)
        self._fields["company_bio"].pack(side="left", fill="x", expand=True, padx=(5, 0))

        extra_frame = ttk.LabelFrame(self.frame, text="AI 补充背景")
        extra_frame.pack(fill="x", padx=15, pady=10)
        self._fields["extra_context"] = scrolledtext.ScrolledText(extra_frame, wrap="word", height=3, width=60)
        self._fields["extra_context"].pack(fill="x", padx=10, pady=5)

        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(fill="x", padx=15, pady=10)
        ttk.Button(btn_frame, text="保存信息", command=self.save_profile).pack(side="left", padx=(0, 10))
        ttk.Button(btn_frame, text="清空", command=self.clear_profile).pack(side="left", padx=(0, 10))
        ttk.Separator(btn_frame, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(btn_frame, text="📤 导出全部配置", command=self.export_config).pack(side="left", padx=(0, 10))
        ttk.Button(btn_frame, text="📥 导入配置", command=self.import_config).pack(side="left", padx=(0, 10))
        ttk.Button(btn_frame, text="💾 备份配置目录", command=self.backup_config).pack(side="left")

        self.status_label = ttk.Label(self.frame, text="", foreground="blue")
        self.status_label.pack(anchor="w", padx=15, pady=5)

        self._load_profile()

    def export_config(self):
        """弹出保存对话框，把 LLM/OCR/用户/提示词全部导出为一个 JSON 文件。"""
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            parent=self.frame,
            title="导出配置到",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            initialfile=f"world-model-config-{datetime.now().strftime('%Y%m%d')}.json",
        )
        if not path:
            return
        ok, msg = export_all(Path(path), include_api_keys=True)
        if ok:
            messagebox.showinfo("导出成功", msg)
        else:
            messagebox.showerror("导出失败", msg)

    def import_config(self):
        """弹出打开对话框，导入之前导出的 JSON 配置。"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self.frame,
            title="选择配置 JSON 文件",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        if not messagebox.askyesno("确认导入",
                                    "导入会覆盖当前的 API Key、用户信息、AI 提示词。\n\n"
                                    "是否继续？（建议先点「备份配置目录」）"):
            return
        ok, msg = import_from(Path(path), overwrite=True)
        if ok:
            messagebox.showinfo("导入成功", msg + "\n\n请重启应用以生效。")
            self._load_profile()
        else:
            messagebox.showerror("导入失败", msg)

    def backup_config(self):
        """把配置目录整体复制一份快照。"""
        from tkinter import filedialog
        path = filedialog.askdirectory(parent=self.frame, title="选择备份目录")
        if not path:
            return
        ok, msg = backup_to_dir(Path(path))
        if ok:
            messagebox.showinfo("备份成功", msg)
        else:
            messagebox.showerror("备份失败", msg)

    def _load_profile(self):
        profile = load_profile()
        for key, widget in self._fields.items():
            value = profile.get(key, "")
            if isinstance(widget, scrolledtext.ScrolledText):
                widget.delete("1.0", "end")
                widget.insert("1.0", str(value))
            else:
                widget.delete(0, "end")
                widget.insert(0, str(value))

    def save_profile(self):
        profile = load_profile()
        for key, widget in self._fields.items():
            if isinstance(widget, scrolledtext.ScrolledText):
                profile[key] = widget.get("1.0", "end").strip()
            else:
                profile[key] = widget.get().strip()
        save_profile(profile)
        self.status_label.config(text="✅ 用户信息已保存，AI 会在下次回答时自动加载。", foreground="green")

    def clear_profile(self):
        save_profile({})
        self._load_profile()
        self.status_label.config(text="已清空", foreground="blue")


# ========== AI 提示词 Tab ==========

class PromptConfigTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        parent.add(self.frame, text="🤖 AI 提示词")

        self._widgets = {}
        for field in PROMPT_FIELDS:
            ttk.Label(self.frame, text=field["label"], font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", padx=15, pady=(10, 2))
            ttk.Label(self.frame, text=field["description"], foreground="gray", justify="left").pack(anchor="w", padx=15, pady=0)
            widget = scrolledtext.ScrolledText(self.frame, wrap="word", height=max(field["rows"], 3), width=65)
            widget.pack(fill="x", padx=15, pady=5)
            self._widgets[field["key"]] = widget

        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(fill="x", padx=15, pady=10)
        ttk.Button(btn_frame, text="保存提示词", command=self.save_prompt).pack(side="left", padx=(0, 10))
        ttk.Button(btn_frame, text="恢复默认", command=self.reset_prompt).pack(side="left")

        self.status_label = ttk.Label(self.frame, text="", foreground="blue")
        self.status_label.pack(anchor="w", padx=15, pady=5)

        self._load_prompt()

    def _load_prompt(self):
        cfg = load_prompt()
        for key, widget in self._widgets.items():
            widget.delete("1.0", "end")
            widget.insert("1.0", str(cfg.get(key, "")))

    def save_prompt(self):
        prompt = {k: w.get("1.0", "end").strip() for k, w in self._widgets.items()}
        save_prompt(prompt)
        self.status_label.config(text="✅ 提示词已保存，下次对话时生效。", foreground="green")

    def reset_prompt(self):
        from prompt_config import load_prompt as _load_prompt
        cfg = _load_prompt()
        for key, widget in self._widgets.items():
            widget.delete("1.0", "end")
            widget.insert("1.0", str(cfg.get(key, "")))
        self.status_label.config(text="已恢复默认", foreground="blue")


# ========== OCR 配置 Tab ==========

class OcrConfigTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        parent.add(self.frame, text="📷 OCR 配置")

        # 模式选择
        mode_frame = ttk.LabelFrame(self.frame, text="识别模式")
        mode_frame.pack(fill="x", padx=15, pady=10)
        self.mode_var = tk.StringVar(value="api")
        ttk.Radiobutton(mode_frame, text="API 模式（推荐，体积小，需要 Key）",
                        variable=self.mode_var, value="api", command=self._on_mode_change).pack(anchor="w", padx=10, pady=3)
        ttk.Radiobutton(mode_frame, text="本地模式（PaddleOCR，需要安装 paddlepaddle+paddleocr）",
                        variable=self.mode_var, value="local", command=self._on_mode_change).pack(anchor="w", padx=10, pady=3)

        # API 提供商
        self.api_frame = ttk.LabelFrame(self.frame, text="云端 OCR 提供商")
        self.api_frame.pack(fill="x", padx=15, pady=10)

        ttk.Label(self.api_frame, text="提供商：").pack(anchor="w", padx=10, pady=(5, 2))
        self.provider_var = tk.StringVar()
        self.provider_combo = ttk.Combobox(self.api_frame, textvariable=self.provider_var,
                                           values=list(OCR_PROVIDERS.keys()), width=15, state="readonly")
        self.provider_combo.set("baidu")
        self.provider_combo.pack(anchor="w", padx=10, pady=2)
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_change)

        self.desc_label = ttk.Label(self.api_frame, text="", foreground="gray", justify="left", wraplength=500)
        self.desc_label.pack(anchor="w", padx=10, pady=5)

        # 凭证字段容器
        self.cred_frame = ttk.Frame(self.api_frame)
        self.cred_frame.pack(fill="x", padx=10, pady=5)
        self._cred_widgets = {}

        # 本地模式提示
        self.local_frame = ttk.LabelFrame(self.frame, text="本地模式说明")
        self.local_frame.pack(fill="x", padx=15, pady=10)
        ttk.Label(self.local_frame,
                  text="使用本地 PaddleOCR 需要安装：\n"
                       "   pip install paddlepaddle paddleocr\n\n"
                       "首次运行会自动下载中文识别模型（约 150MB）。",
                  justify="left").pack(anchor="w", padx=10, pady=10)

        # 按钮
        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(fill="x", padx=15, pady=10)
        ttk.Button(btn_frame, text="保存配置", command=self.save_config).pack(side="left", padx=(0, 10))
        ttk.Button(btn_frame, text="测试连通（API 模式）", command=self.test_connection).pack(side="left")

        self.status_label = ttk.Label(self.frame, text="", foreground="blue")
        self.status_label.pack(anchor="w", padx=15, pady=5)

        self._load_config()
        self._on_mode_change()
        self._on_provider_change()

    def _on_mode_change(self):
        mode = self.mode_var.get()
        if mode == "api":
            self.api_frame.pack(fill="x", padx=15, pady=10, before=self.local_frame)
            self.local_frame.pack_forget()
        else:
            self.local_frame.pack(fill="x", padx=15, pady=10, before=self.api_frame)
            self.api_frame.pack_forget()

    def _on_provider_change(self, event=None):
        provider = self.provider_var.get()
        if provider not in OCR_PROVIDERS:
            return
        cfg = OCR_PROVIDERS[provider]
        self.desc_label.config(text=cfg["description"])
        # 重建凭证字段
        for w in self.cred_frame.winfo_children():
            w.destroy()
        self._cred_widgets = {}
        for field_key, field_label in cfg["fields"]:
            row = ttk.Frame(self.cred_frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{field_label}：", width=16).pack(side="left")
            entry = ttk.Entry(row, width=40)
            entry.pack(side="left", fill="x", expand=True, padx=(5, 0))
            self._cred_widgets[field_key] = entry
        # 从已保存配置中回填
        ocr_cfg = load_ocr_config()
        creds = ocr_cfg.get("credentials", {}) or {}
        for k, w in self._cred_widgets.items():
            w.delete(0, "end")
            w.insert(0, creds.get(k, ""))

    def _load_config(self):
        cfg = load_ocr_config()
        self.mode_var.set(cfg.get("mode", "api"))
        self.provider_var.set(cfg.get("provider", "baidu"))

    def save_config(self):
        mode = self.mode_var.get()
        provider = self.provider_var.get()
        credentials = {}
        if mode == "api":
            for k, w in self._cred_widgets.items():
                credentials[k] = w.get().strip()
        ocr_cfg = {
            "mode": mode,
            "provider": provider,
            "credentials": credentials,
        }
        save_ocr_config(ocr_cfg)
        self.status_label.config(text="✅ OCR 配置已保存", foreground="green")

    def test_connection(self):
        if self.mode_var.get() != "api":
            messagebox.showinfo("提示", "本地模式无需测试连通，请确保已安装 paddlepaddle+paddleocr")
            return
        provider = self.provider_var.get()
        credentials = {k: w.get().strip() for k, w in self._cred_widgets.items()}
        if not any(credentials.values()):
            messagebox.showwarning("提示", "请先填写至少一个凭证字段")
            return
        self.status_label.config(text="正在测试 OCR API 连通性...", foreground="blue")
        self.frame.update_idletasks()

        def task():
            try:
                result = test_ocr_api(provider, credentials)
                self.frame.after(0, lambda: self._on_test_done(result))
            except Exception as e:
                self.frame.after(0, lambda: self._on_test_done(f"ERROR: {e}"))

        threading.Thread(target=task, daemon=True).start()

    def _on_test_done(self, result):
        if result.startswith("OK:"):
            self.status_label.config(text=f"✅ 连通成功！{result[4:]}", foreground="green")
        else:
            self.status_label.config(text=f"❌ 失败: {result[7:] if result.startswith('ERROR:') else result}", foreground="red")


# ========== 主函数 ==========

def main():
    root = tk.Tk()
    root.title("个人企业世界模型")
    root.geometry("920x720")
    root.minsize(750, 580)

    try:
        root.iconbitmap("icon.ico")
    except Exception:
        pass

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=8, pady=8)

    ChatTab(notebook)
    SearchTab(notebook)
    InboxTab(notebook)
    PipelineTab(notebook)
    DocumentsTab(notebook)
    ApiConfigTab(notebook)
    OcrConfigTab(notebook)
    UserProfileTab(notebook)
    PromptConfigTab(notebook)

    root.mainloop()


if __name__ == "__main__":
    main()
