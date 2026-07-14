"""PEWM GUI 样式配置：提供现代化的 ttk 主题与辅助函数。"""
from tkinter import ttk


# 调色板
BG_COLOR = "#f8f9fa"
CARD_BG = "#ffffff"
ACCENT_COLOR = "#4a90d9"
ACCENT_HOVER = "#357abd"
TEXT_COLOR = "#212529"
SECONDARY_TEXT = "#6c757d"
BORDER_COLOR = "#dee2e6"
SUCCESS_COLOR = "#28a745"
DANGER_COLOR = "#dc3545"
WARNING_COLOR = "#ffc107"

# 字体
FONT_FAMILY = "Microsoft YaHei"
TITLE_FONT = (FONT_FAMILY, 14, "bold")
LABEL_FONT = (FONT_FAMILY, 10, "bold")
BODY_FONT = (FONT_FAMILY, 10)
SMALL_FONT = (FONT_FAMILY, 9)
MONO_FONT = ("Consolas", 10)


def configure_styles(root):
    """为传入的 Tk/Toplevel 根窗口配置全局 ttk 样式。"""
    style = ttk.Style(root)
    style.theme_use("clam")

    # 基础控件
    style.configure("TFrame", background=BG_COLOR)
    style.configure("TLabel", background=BG_COLOR, foreground=TEXT_COLOR, font=BODY_FONT)
    style.configure("TButton",
                    background=ACCENT_COLOR,
                    foreground="white",
                    font=BODY_FONT,
                    padding=(12, 5),
                    relief="flat")
    style.map("TButton",
              background=[("active", ACCENT_HOVER), ("pressed", ACCENT_HOVER)],
              foreground=[("active", "white")])

    style.configure("TEntry", padding=5, relief="flat")
    style.configure("TCombobox", padding=5)
    style.configure("TCheckbutton", background=BG_COLOR, font=BODY_FONT)
    style.configure("TRadiobutton", background=BG_COLOR, font=BODY_FONT)

    # Notebook
    style_notebook(style)
    # Treeview
    style_treeview(style)
    # 按钮变体
    style_buttons(style)

    return style


def style_notebook(style: ttk.Style):
    """配置 Notebook 标签页样式。"""
    style.configure("TNotebook", background=BG_COLOR, tabmargins=[2, 5, 2, 0])
    style.configure("TNotebook.Tab",
                    background=BG_COLOR,
                    foreground=TEXT_COLOR,
                    font=(FONT_FAMILY, 10),
                    padding=[12, 6],
                    borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", CARD_BG), ("active", "#e9ecef")],
              foreground=[("selected", ACCENT_COLOR), ("active", TEXT_COLOR)],
              expand=[("selected", [2, 6, 2, 0])])


def style_treeview(style: ttk.Style):
    """配置 Treeview 表格样式。"""
    style.configure("Treeview",
                    background=CARD_BG,
                    foreground=TEXT_COLOR,
                    fieldbackground=CARD_BG,
                    font=BODY_FONT,
                    rowheight=26)
    style.configure("Treeview.Heading",
                    background=BG_COLOR,
                    foreground=TEXT_COLOR,
                    font=LABEL_FONT,
                    padding=5)
    style.map("Treeview",
              background=[("selected", "#cfe2ff")],
              foreground=[("selected", TEXT_COLOR)])


def style_buttons(style: ttk.Style):
    """注册几种常用按钮变体。"""
    # 次要按钮
    style.configure("Secondary.TButton",
                    background=BG_COLOR,
                    foreground=TEXT_COLOR,
                    font=BODY_FONT,
                    padding=(12, 5))
    style.map("Secondary.TButton",
              background=[("active", "#e9ecef"), ("pressed", BORDER_COLOR)],
              foreground=[("active", TEXT_COLOR)])

    # 危险按钮
    style.configure("Danger.TButton",
                    background=DANGER_COLOR,
                    foreground="white",
                    font=BODY_FONT,
                    padding=(12, 5))
    style.map("Danger.TButton",
              background=[("active", "#c82333"), ("pressed", "#bd2130")],
              foreground=[("active", "white")])

    # 成功按钮
    style.configure("Success.TButton",
                    background=SUCCESS_COLOR,
                    foreground="white",
                    font=BODY_FONT,
                    padding=(12, 5))
    style.map("Success.TButton",
              background=[("active", "#218838"), ("pressed", "#1e7e34")],
              foreground=[("active", "white")])


def style_label_frame(style: ttk.Style):
    """配置 LabelFrame 卡片样式。"""
    style.configure("Card.TLabelframe",
                    background=CARD_BG,
                    foreground=TEXT_COLOR,
                    borderwidth=1,
                    relief="solid")
    style.configure("Card.TLabelframe.Label",
                    background=CARD_BG,
                    foreground=ACCENT_COLOR,
                    font=LABEL_FONT)
