<div align="center">

# 个人企业世界模型

### Personal Enterprise World Model (PEWM)

**你只管往 `00-Inbox/` 里丢东西，剩下的交给 AI 管线。**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)]()

[快速开始](#-快速开始) · [功能特性](#-功能特性) · [架构设计](#-架构设计) · [配置指南](#-配置指南) · [常见问题](#-常见问题)

</div>

---

##  项目简介

**个人企业世界模型**是一款**纯本地、声明式、AI 自动维护**的个人知识系统。它的核心理念是：

> 用户只负责**原始信息的采集**（随手记、会议记录、图片、语音转文字），AI 负责**结构化提取、索引、关联、问答**。

整个系统跑在你的笔记本上，不依赖任何云端服务（除非你主动配置 LLM API 启用 RAG 问答），所有数据都在本地，随时可以 `Ctrl+C / Ctrl+V` 整文件夹迁移。

**解决的痛点**：

- 笔记软件越用越多，搜不到想要的内容
- 知识散落在多个 app（印象笔记/Notion/Obsidian/微信收藏）无法联动
- 想有自己的 AI 助手，但公有云方案既贵又泄露隐私
- 传统 RAG 方案部署复杂（Docker/向量库/后端/前端）

---

##  功能特性

| 功能 | 说明 |
|------|------|
| **Inbox 速记** | 任意格式的随手记（文字/图片/代码），零格式要求 |
| **LLM 智能提取** | 用大模型分析笔记，自动拆分成 7 种实体类型 |
| **双路检索** | SQLite FTS5 关键词检索 + 语义向量检索，混合召回 |
| **RAG 问答** | 基于知识库的生成式问答，自动注入用户身份上下文，支持 SSE 流式输出 |
| **OCR 识别** | 本地 PaddleOCR 或 云端 API（百度/腾讯/阿里） |
| **文档管理** | 软删除/硬删除/恢复，源文件丢失自动进回收站 |
| **配置迁移** | API Key / 用户身份 / AI 提示词 一键导出/导入 |
| **双击即用** | 单文件 `.exe`（Windows），内置 Python 运行时 + bge 模型 |
| **统一日志** | 控制台 + 文件双通道，按天轮转保留 7 天，关键路径性能埋点 |
| **崩溃日志** | 未捕获异常自动写入 `logs/crashes/`，可选匿名上报开关 |
| **诊断面板** | 设置页实时查看 torch 状态、崩溃日志、性能指标 |

---

##  架构设计

### 知识分层

```
┌─────────────────────────────────────────────────────────┐
│  00-Inbox       你负责：原始信息采集（随手记/图片/截图） │
└─────────────────────┬───────────────────────────────────┘
                      │ AI 管线自动提取
┌─────────────────────▼───────────────────────────────────┐
│  10-Theory      行业通用知识、笔记原文存档（notes/）     │
│  20-Ontology    业务本体：术语/流程/系统/组织            │
│  30-Instances   实例：常量/案例/数据画像                 │
│  40-Skills      可执行技能：提示词/脚本/检查清单         │
└─────────────────────┬───────────────────────────────────┘
                      │ 双索引
┌─────────────────────▼───────────────────────────────────┐
│  data/                                                   │
│   ├── world-model.db   SQLite + FTS5 全文索引            │
│   └── vector/          bge-small-zh 语义向量索引         │
└─────────────────────┬───────────────────────────────────┘
                      │ 检索
┌─────────────────────▼───────────────────────────────────┐
│  RAG Pipeline    双路召回 → 注入用户上下文 → LLM 生成   │
└─────────────────────────────────────────────────────────┘
```

### 7 种实体类型

| 类型 | 目录 | 用途 | 示例 |
|------|------|------|------|
| `term` | `20-Ontology/dictionary/` | 术语/概念 | RAG、知识图谱 |
| `process` | `20-Ontology/processes/` | 业务流程 | 订单退款流程 |
| `system` | `20-Ontology/systems/` | 系统/平台 | SAP、EPM |
| `constant` | `30-Instances/constants/` | 数值常量 | 超时时间=30分钟 |
| `case` | `30-Instances/cases/` | 案例/故障 | 订单服务 OOM 故障 |
| `skill` | `40-Skills/` | 可执行技能 | 代码审查检查清单 |
| `note` | `10-Theory/notes/` | 原文存档（兜底） | 杂记、无结构化内容 |

**提取策略**：LLM 优先分析 → 失败回退关键词触发 → 再失败整篇存为 `note`（**永远不丢**）。

### 技术栈

| 组件 | 选型 | 用途 |
|------|------|------|
| GUI | `tkinter` | Python 内置，零依赖 |
| 数据库 | `SQLite + FTS5` | 全文索引 + 向量索引存储 |
| 向量模型 | `bge-small-zh-v1.5` (ONNX) | 中文语义向量（512 维，92MB） |
| 推理框架 | `sentence-transformers + torch` | 模型加载与向量生成 |
| LLM API | `openai` SDK | DeepSeek/Kimi/MiniMax 统一接口 |
| OCR 本地 | `PaddleOCR`（可选） | 高精度中文识别 |
| OCR 云端 | `baidu-aip` / `tencentcloud-sdk` | 每月免费额度 |
| 打包 | `PyInstaller` | 单文件 exe |

---

##  快速开始

### 方式一：下载 exe（推荐）

1. 前往 [Releases](https://github.com/111acge/personal-word-onto/releases) 下载最新 `个人企业世界模型.exe`（约 318MB）
2. 放到任意目录（例如桌面）
3. 双击运行，等待 20-30 秒启动（首次需要解压）
4. 在「API 配置」Tab 填入 LLM Key（可选，启用 RAG 问答）
5. 在「Inbox 速记」写第一篇笔记 → 点「管线」运行 AI 管线 → 开始问答

### 方式二：源码运行

```bash
# 1. 克隆仓库（需要 Git LFS）
git lfs install
git clone https://github.com/111acge/personal-word-onto.git
cd personal-word-onto

# 2. 创建虚拟环境（推荐 Python 3.11+）
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动 GUI
python start.py
```

### 方式三：仅命令行（无 GUI）

```bash
# 运行 AI 管线（处理 Inbox + 提取 + 索引）
python start.py --pipeline

# 语义检索
python 90-Meta/query/search.py "订单支付超时多久"

# RAG 问答
python 90-Meta/query/chat.py "我处理过什么故障"
```

---

##  配置指南

### 1. LLM API（RAG 问答必需）

在 GUI 的「**API 配置**」Tab 中填写。三家提供商都走 OpenAI 兼容协议，只换 base_url：

| 提供商 | Base URL | 推荐模型 | 注册链接 |
|--------|----------|----------|---------|
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` | [注册](https://platform.deepseek.com/) |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | [注册](https://platform.moonshot.cn/) |
| MiniMax | `https://api.minimax.chat/v1` | `abab6.5s-chat` | [注册](https://www.minimaxi.com/) |

**成本估算**：DeepSeek 约 0.003 元/篇笔记，每月 1000 篇 ≈ 3 元。

### 2. OCR 配置（图片文字识别）

在 GUI 的「**OCR 配置**」Tab 切换模式：

| 模式 | 优点 | 缺点 |
|------|------|------|
| **API 模式（推荐）** | exe 体积小，识别精度高 | 需要 Key（每月有免费额度） |
| **本地模式** | 离线可用 | 需要 `pip install paddlepaddle paddleocr`（+450MB） |

API 提供商：

| 提供商 | 免费额度 | 所需凭证 |
|--------|---------|---------|
| 百度智能云 | 每月 1000 次 | API Key + Secret Key |
| 腾讯云 | 每月 1000 次 | SecretId + SecretKey |
| 阿里云 | 按量付费 | AppCode |

### 3. 用户信息（注入到 AI 提示词）

在 GUI 的「**用户信息**」Tab 填写个人/公司信息。AI 问答时会自动注入作为上下文，让回答更贴合你的身份。

例如：你填了"职位：供应链产品经理"，AI 在回答供应链相关问题时会用产品经理的视角组织语言。

### 4. AI 提示词（可定制）

在 GUI 的「**AI 提示词**」Tab 可编辑 3 个模板：

| 模板 | 用途 |
|------|------|
| **RAG 系统提示词** | AI 回答问题的全局指令，包含 `{{USER_CONTEXT}}` 占位符（自动替换为用户身份） |
| **无结果回复** | 知识库检索为空时的回复文本 |
| **欢迎语** | 启动时对话区显示的第一句话 |

### 5. 诊断信息（torch / 崩溃日志 / 性能指标）

在 GUI 的「**设置 → 诊断**」Tab 可查看：

| 面板 | 内容 |
|------|------|
| **torch 环境状态** | torch 版本、CUDA/CPU 后端、bge 模型完整性、健康标志 |
| **崩溃日志** | 匿名上报开关（默认关闭）、最近 5 条崩溃记录（含完整堆栈） |
| **性能指标** | 最近 50 条埋点（启动耗时、检索耗时、RAG 耗时等） |

崩溃日志写入 `logs/crashes/crash-YYYY-MM-DD-HHMMSS.log`，应用日志写入 `logs/app.log`（按天轮转保留 7 天）。

### 开发与测试

```bash
# 后端测试（130 项用例，覆盖率 64%）
pip install pytest pytest-cov
pytest tests/ --cov=pewm

# 前端测试（Vitest + jsdom，15 项用例）
cd pewm/web/static
npm install
npm test
```

---

##  使用指南

### 典型工作流

```mermaid
flowchart LR
    A[随手写笔记] --> B[Inbox 速记 Tab]
    B --> C[管线 Tab: ▶ 运行]
    C --> D[AI 自动提取]
    D --> E[10-Theory / 20-Ontology / 30-Instances]
    E --> F[双索引 FTS5 + 向量]
    F --> G[对话 Tab: RAG 问答]
```

### 9 个 GUI Tab

| Tab | 功能 |
|-----|------|
| **💬 对话** | 与知识库对话，支持 RAG 生成（需勾选「RAG 生成」+ 配置 API） |
| **🔍 检索** | 关键词/语义混合检索，查看原文 |
| **📝 Inbox 速记** | 快速写入 `00-Inbox/`，文件名自动带日期 |
| **⚙️ 管线** | 运行 AI 管线、重建向量索引、批量 OCR |
| **📚 文档管理** | 浏览/搜索/软删/硬删/恢复所有已索引文档 |
| **⚙️ API 配置** | LLM API Key + 连通测试 |
| **📷 OCR 配置** | 本地/API 模式切换 + OCR Key |
| **👤 用户信息** | 个人/公司信息 + 配置导出/导入/备份 |
| **🤖 AI 提示词** | 编辑 RAG 系统提示词等 3 个模板 |

### 文档管理：软删除 vs 硬删除

| 操作 | FTS5 行为 | 向量库行为 | 可恢复？ | 触发方式 |
|------|---------|----------|---------|---------|
| **软删除** | 从索引移除，原文档保留 | 标记 `deleted_at`，向量保留 | ✅ | 自动（源文件丢）/ 手动 |
| **硬删除** | DELETE 触发器清理 | 物理删除行 | ❌ | 手动 + 二次确认 |
| **恢复** | 重加 FTS 索引 | 清 `deleted_at` 标记 | - | 手动 |

**核心设计**：默认所有删除都是软删除，知识**永不意外丢失**。

### 配置导出/导入

在「用户信息」Tab 底部有 3 个按钮：

- **📤 导出全部配置**：把 LLM/OCR Key + 用户信息 + AI 提示词 打包成 1 个 JSON
- **📥 导入配置**：从 JSON 一键恢复（换电脑用这个）
- **💾 备份配置目录**：把整个 `~/.enterprise_world_model/` 快照到指定目录

---

##  项目结构

```
personal-word-onto/
├── pewm/                         # AI 管线（核心代码）
│   ├── config/
│   │   ├── schemas/              # 7 个实体 schema（yaml）
│   │   ├── extraction-rules.yaml # 关键词触发规则
│   │   └── merge-policy.yaml     # 冲突合并策略
│   ├── processors/               # 15 个 Python 模块
│   │   ├── extractor.py          # LLM + 规则 混合提取
│   │   ├── rag.py                # RAG 问答管道
│   │   ├── vector_db.py          # numpy 向量库
│   │   ├── database.py           # SQLite + FTS5
│   │   ├── llm_client.py         # LLM API 适配（3 家）
│   │   ├── ocr.py + ocr_api.py   # OCR 双模式
│   │   ├── user_profile.py       # 用户身份
│   │   ├── prompt_config.py      # AI 提示词
│   │   ├── progress_dialog.py    # 通用进度条
│   │   └── config_manager.py     # 配置导出/导入
│   └── prompts/
│
├── 00-Inbox/                     # 你唯一维护的目录
├── 10-Theory/                    # AI 维护：笔记存档
├── 20-Ontology/                  # AI 维护：术语/流程/系统
├── 30-Instances/                 # AI 维护：常量/案例
├── 40-Skills/                    # AI 维护：技能
├── 90-Meta/query/                # 命令行查询接口
├── bge-model/                    # 内置语义向量模型（LFS）
├── data/                         # 运行时数据（自动创建，不入 git）
│
├── gui.py                        # tkinter 主界面（9 个 Tab）
├── start.py                      # 启动器（GUI / 命令行）
├── run.py                        # 管线入口
├── build.py                      # PyInstaller 打包脚本
├── build.spec                    # PyInstaller 配置
├── requirements.txt
└── README.md                     # 本文件
```

---

##  命令行用法

```bash
# 运行 AI 管线（处理 Inbox + 提取 + 索引 + reconcile）
python run.py

# 常用参数
python run.py --reset             # 重置处理标记并重建索引
python run.py --skip-errors       # 跳过冲突文件继续
python run.py --no-git            # 禁用 Git 自动提交
python run.py --no-vector         # 跳过向量索引（只用 FTS5）
python run.py --no-ocr            # 跳过图片 OCR
python run.py --status            # 查看处理状态
python run.py --rebuild-vector    # 重建全部向量索引
python run.py --reconcile         # 手动软删失效记录
python run.py --purge             # 硬删所有软删记录（不可逆！）
```

---

##  打包指南

### 环境要求

- Python 3.11+（推荐 3.12）
- Git + Git LFS
- Windows（Linux/macOS 未充分测试）

### 打包步骤

```bash
# 1. 安装依赖
pip install -r requirements.txt
pip install pyinstaller

# 2. 执行打包（约 5 分钟）
python build.py

# 3. 产物
ls dist/
# 个人企业世界模型.exe   318MB
```

### 打包原理

- `build.spec` 用 `onefile` 模式，所有 Python 运行时 + 第三方库 + bge 模型打到一个 exe
- 首次启动时 PyInstaller bootloader 会把 exe 解压到 `%TEMP%\_MEIxxxxx` 临时目录
- 程序实际写入的 `data/` 目录在 exe 旁边，不在 TEMP（已修复 PyInstaller 路径陷阱）

### 验证打包

```bash
# 复制 exe 到任意空目录运行，验证 data/ 在 exe 旁边创建
mkdir C:\test
copy dist\个人企业世界模型.exe C:\test\
cd C:\test
.\个人企业世界模型.exe
# 等 30 秒，ls 应该看到 data/ 目录
```

---

##  常见问题

### Q1. 启动 exe 后 30 秒还没反应？

**A**：正常。PyInstaller 单文件模式需要先解压 318MB 到 TEMP，然后加载 torch（约 20 秒）。第一次启动可能需要 30-60 秒，后续会快一些。

### Q2. data/ 文件夹没创建？

**A**：检查 exe 所在目录是否**有写入权限**（不要放 `C:\Program Files`）。如果 exe 在桌面，`data/` 应该也在桌面旁边。

### Q3. 跑管线提示"LLM 调用失败"？

**A**：「API 配置」Tab 没填 Key，或 Key 过期了。如果不想用 LLM，系统会自动回退到基于触发词的规则提取 + note 兜底，功能可用但提取质量下降。

### Q4. 向量检索精度不高？

**A**：确认已启用语义向量（`data/vector/index.pkl` 存在）。首次跑管线时会自动加载 bge 模型。如果模型没加载，点「管线」Tab 的「重建向量索引」。

### Q5. 换电脑怎么迁移？

**A**：

- **知识库数据**：把 exe 旁边的 `data/`、`10-Theory/`、`20-Ontology/`、`30-Instances/`、`40-Skills/`、`00-Inbox/` 全部拷到新电脑
- **配置**：在旧电脑「用户信息」Tab 点「📤 导出全部配置」→ 新电脑「📥 导入配置」

### Q6. exe 杀毒软件报毒？

**A**：PyInstaller 打包的 exe 常被 Windows Defender 误报。加入白名单即可。

### Q7. GitHub 推送超时？

**A**：大陆访问 `github.com` 常被 SNI 干扰。配置本地代理后推送：

```bash
git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push
```

（7890 是 Clash 默认端口，按你实际代理端口改）

### Q8. 想自定义提取规则？

**A**：编辑 `pewm/config/extraction-rules.yaml`（关键词触发）和 `pewm/config/schemas/*.yaml`（实体字段模板）。改完下次跑管线生效。

---

##  Roadmap

- [ ] 移动端 App（iOS / Android）同步 Inbox
- [ ] 语音转文字接入（Whisper API）
- [ ] 多模态 RAG（图片理解 + 文字检索）
- [ ] 知识图谱可视化
- [ ] 团队协作（多用户共享知识库）
- [ ] 支持更多 LLM 提供商（Gemini / Claude / 通义千问）

---

## License

[MIT License](LICENSE) - 自由使用、修改、分发。

---

##  致谢

- [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) - 中文语义向量模型
- [sentence-transformers](https://github.com/UKPLab/sentence-transformers) - 模型加载框架
- [PyInstaller](https://pyinstaller.org/) - Python 打包工具
- [DeepSeek](https://platform.deepseek.com/) / [Moonshot](https://platform.moonshot.cn/) / [MiniMax](https://www.minimaxi.com/) - LLM API 提供商

---

<div align="center">

**如果这个项目对你有帮助，请点个 ⭐ Star 支持一下！**

[报告 Bug](https://github.com/111acge/personal-word-onto/issues) · [提出建议](https://github.com/111acge/personal-word-onto/issues)

</div>
