# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Flask + pywebview 桌面应用）。

Windows 用户安装 PyInstaller 后运行：
    pyinstaller build.spec

会在 dist/ 目录下生成可执行文件。
"""
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import os
import shutil
import sys
from pathlib import Path

block_cipher = None

# 打包内容目录时只使用空目录骨架（仅含 .gitkeep），避免把用户私人笔记打进 exe。
# 与 build.py 的 prepare_staging 一致；直接运行 pyinstaller build.spec 时也会自动生成。
_spec_root = Path(os.getcwd())
_staging = _spec_root / 'build' / 'staging'
_content_dirs = ['00-Inbox', '10-Theory', '20-Ontology', '30-Instances', '40-Skills', '90-Meta']
if not all((_staging / d / '.gitkeep').exists() for d in _content_dirs):
    if _staging.exists():
        shutil.rmtree(_staging)
    for _d in _content_dirs:
        (_staging / _d).mkdir(parents=True, exist_ok=True)
        (_staging / _d / '.gitkeep').write_text('', encoding='utf-8')

# 收集 Flask / pywebview / jinja2 / werkzeug 运行时所需的静态文件和子模块
flask_datas = collect_data_files('flask')
webview_datas = collect_data_files('webview')
webview_submodules = collect_submodules('webview')
jinja2_datas = collect_data_files('jinja2')

a = Analysis(
    ['start.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('build/staging/00-Inbox', '00-Inbox'),
        ('build/staging/10-Theory', '10-Theory'),
        ('build/staging/20-Ontology', '20-Ontology'),
        ('build/staging/30-Instances', '30-Instances'),
        ('build/staging/40-Skills', '40-Skills'),
        ('build/staging/90-Meta', '90-Meta'),
        ('pewm', 'pewm'),
        ('pewm/web/templates', 'pewm/web/templates'),
        ('pewm/web/static', 'pewm/web/static'),
        ('.env.example', '.'),
        ('requirements.txt', '.'),
        ('VERSION', '.'),
        ('bge-model', 'bge-model'),
    ] + flask_datas + webview_datas + jinja2_datas,
    hiddenimports=[
        # Flask / Web
        'flask',
        'jinja2',
        'jinja2.ext',
        'werkzeug',
        'markupsafe',
        'itsdangerous',
        'click',
        'webview',
        'webview.util',
        'webview.platforms',
        'webview.platforms.winforms',
        'webview.http',
        'bottle',
        'pythonnet',
        # 内部模块
        'pewm.paths',
        'pewm.web.app',
        'pewm.web.desktop',
        'pewm.processors.__main__',
        'pewm.processors.database',
        'pewm.processors.extractor',
        'pewm.processors.vectorizer',
        'pewm.processors.vector_db',
        'pewm.processors.watcher',
        'pewm.processors.utils',
        'pewm.processors.llm_client',
        'pewm.processors.rag',
        'pewm.processors.retrieval',
        'pewm.processors.merge',
        'pewm.processors.ocr',
        'pewm.processors.ocr_api',
        'pewm.processors.user_profile',
        'pewm.processors.prompt_config',
        'pewm.processors.progress_dialog',
        'pewm.processors.config_manager',
        # 外部依赖
        'openai',
        'numpy',
        'requests',
        'pydantic',
        # 语义向量（可选，若未安装则自动回退 TF-IDF）
        'sentence_transformers',
        'transformers',
        'torch',
        'torch.nn',
        'torch.nn.functional',
        'tokenizers',
        'huggingface_hub',
        'safetensors',
    ] + webview_submodules,
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
    icon='assets/icon.ico',          # 应用图标（任务栏 / 资源管理器 / 窗口）
    version='assets/version_info.txt',  # Windows 版本信息资源
    manifest='assets/app.manifest',  # DPI 感知 + 通用控件 v6，消除高 DPI 模糊
)
