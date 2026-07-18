"""通用进度条对话框（tkinter）。

支持两种模式：
- 确定进度：显示百分比和进度条
- 不确定进度：显示滚动条 + 旋转动画

可从子线程安全更新，主线程保持响应。
"""
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from pewm.processors.log_config import get_logger

logger = get_logger(__name__)


class ProgressDialog(tk.Toplevel):
    """通用进度对话框。

    使用示例：
        dlg = ProgressDialog(parent, title="正在下载模型", total=100)
        for i in range(100):
            dlg.update(i, f"下载中... {i}%")
        dlg.finish("下载完成！")
    """

    def __init__(self, parent, title: str, total: int = 0, message: str = "",
                 cancelable: bool = True):
        super().__init__(parent)
        self.title(title)
        self.geometry("420x160")
        self.resizable(False, False)
        self.transient(parent)
        if not cancelable:
            self.protocol("WM_DELETE_WINDOW", lambda: None)

        self.total = total
        self._cancelled = False

        # 顶部消息
        self.message_var = tk.StringVar(value=message)
        ttk.Label(self, textvariable=self.message_var, wraplength=380,
                  justify="left").pack(padx=15, pady=(15, 5), anchor="w")

        # 进度条
        self.progress = ttk.Progressbar(self, orient="horizontal", length=380,
                                        mode="determinate" if total > 0 else "indeterminate")
        self.progress.pack(padx=15, pady=10)
        if total > 0:
            self.progress["maximum"] = total
            self.progress["value"] = 0
        else:
            self.progress.start(10)

        # 百分比和取消按钮
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=15, pady=(0, 15))
        self.percent_var = tk.StringVar(value="0%")
        ttk.Label(bottom, textvariable=self.percent_var).pack(side="left")
        if cancelable:
            ttk.Button(bottom, text="取消", command=self.cancel).pack(side="right")

        self.update_idletasks()
        self.grab_set()

    def update(self, current: int, message: str = None):
        """从子线程安全更新进度。"""
        if self._cancelled:
            return

        def _do():
            if message:
                self.message_var.set(message)
            if self.total > 0:
                self.progress["value"] = current
                pct = int(current / self.total * 100) if self.total > 0 else 0
                self.percent_var.set(f"{pct}% ({current}/{self.total})")
        try:
            self.after(0, _do)
        except tk.TclError:
            # 窗口已被销毁，忽略后续进度更新
            pass

    def finish(self, final_message: str = "完成"):
        """标记完成，自动关闭。"""
        def _do():
            self.message_var.set(final_message)
            if self.total > 0:
                self.progress["value"] = self.total
                self.percent_var.set("100%")
            else:
                self.progress.stop()
            self.update_idletasks()
            # 短暂延迟后关闭，让用户能看到"完成"
            self.after(800, self.destroy)
        try:
            self.after(0, _do)
        except tk.TclError:
            pass

    def cancel(self):
        """取消操作。"""
        self._cancelled = True
        self.destroy()

    def is_cancelled(self) -> bool:
        return self._cancelled


def run_with_progress(parent, title: str, task_fn: Callable,
                      on_done: Optional[Callable] = None,
                      on_error: Optional[Callable] = None):
    """在子线程里运行任务，并显示进度对话框。

    task_fn 的签名：task_fn(progress_callback, is_cancelled)
        - progress_callback(current, total, message) 由 task_fn 调用
        - is_cancelled() 返回 True 表示用户取消了

    on_done(result)：任务正常完成时的回调
    on_error(error)：任务抛异常时的回调
    """
    dlg = ProgressDialog(parent, title, total=0)

    def _progress(current, total, message=""):
        def _do():
            if dlg.total != total and total > 0:
                # 第一次知道总进度时切换为 determinate
                dlg.total = total
                dlg.progress.stop()
                dlg.progress.config(mode="determinate", maximum=total)
            dlg.update(current, message)
        try:
            dlg.after(0, _do)
        except tk.TclError:
            # 窗口已被销毁，忽略后续进度更新
            pass

    def _is_cancelled():
        return dlg.is_cancelled()

    def _worker():
        try:
            result = task_fn(_progress, _is_cancelled)
            if not dlg.is_cancelled():
                dlg.finish("完成")
                if on_done:
                    parent.after(0, lambda: on_done(result))
        except Exception as e:
            if not dlg.is_cancelled():
                logger.exception("进度对话框任务失败")
                dlg.finish(f"失败：{e}")
                if on_error:
                    parent.after(0, lambda: on_error(e))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return dlg
