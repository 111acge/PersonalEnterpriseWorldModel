"""PEWM 主窗口。"""
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from pewm.gui.styles import BG_COLOR, configure_styles
from pewm.gui.tabs import (
    ApiConfigTab,
    ChatTab,
    DocumentsTab,
    InboxTab,
    OcrConfigTab,
    PipelineTab,
    PromptConfigTab,
    SearchTab,
    UserProfileTab,
)


def main():
    root = tk.Tk()
    root.title("个人企业世界模型")
    root.geometry("920x720")
    root.minsize(750, 580)
    root.configure(background=BG_COLOR)

    try:
        root.iconbitmap("icon.ico")
    except Exception:
        pass

    try:
        configure_styles(root)
    except Exception as e:
        print(f"[gui] 样式配置失败：{e}", file=sys.stderr)

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=8, pady=8)

    try:
        ChatTab(notebook)
        SearchTab(notebook)
        InboxTab(notebook)
        PipelineTab(notebook)
        DocumentsTab(notebook)
        ApiConfigTab(notebook)
        OcrConfigTab(notebook)
        UserProfileTab(notebook)
        PromptConfigTab(notebook)
    except Exception as e:
        messagebox.showerror("启动错误", f"初始化界面时出错：{e}")
        raise

    root.mainloop()
