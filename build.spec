# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（纯 Python 实现，无需 numpy/sentence-transformers）。

Windows 用户安装 PyInstaller 后运行：
    pyinstaller build.spec

会在 dist/ 目录下生成可执行文件。
"""
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
import sys

block_cipher = None

a = Analysis(
    ['start.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('00-Inbox', '00-Inbox'),
        ('10-Theory', '10-Theory'),
        ('20-Ontology', '20-Ontology'),
        ('30-Instances', '30-Instances'),
        ('40-Skills', '40-Skills'),
        ('90-Meta', '90-Meta'),
        ('.pipeline', '.pipeline'),
        ('.env.example', '.'),
        ('requirements.txt', '.'),
        ('bge-model', 'bge-model'),
    ],
    hiddenimports=[
        # 核心标准库
        'yaml',
        'tkinter',
        'tkinter.ttk',
        'tkinter.scrolledtext',
        'tkinter.filedialog',
        # 内部模块
        'database',
        'extractor',
        'vectorizer',
        'vector_db',
        'utils',
        'chat',
        'search',
        'llm_client',
        'rag',
        'ocr',
        'ocr_api',
        'user_profile',
        'prompt_config',
        'progress_dialog',
        'config_manager',
        # 外部依赖
        'openai',
        'numpy',
        'requests',
        'httpx',
        'httpcore',
        # 语义向量（可选，若未安装则自动回退 TF-IDF）
        'sentence_transformers',
        'transformers',
        'torch',
        'torch.nn',
        'torch.nn.functional',
        'tokenizers',
        'huggingface_hub',
        'safetensors',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Windows 单文件 exe，隐藏控制台窗口
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='个人企业世界模型',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Windows 下不显示黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
