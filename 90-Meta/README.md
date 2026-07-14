# 个人企业世界模型

> **你只管往 00-Inbox 里丢东西，剩下的交给 AI 管线。**

本项目是一个**声明式、AI 自动维护的本地企业知识系统**。你唯一需要维护的是 `00-Inbox/` 文件夹——把每日见闻、会议记录、随手记丢进去即可。AI 管线会自动完成分类、提取、关联、存储和索引。

**特点**：
- **纯本地运行**：无需服务器、无需浏览器、无需联网（LLM 调用除外）。
- **双击即用**：可打包成 `.exe`（Windows）或可执行文件（Linux/macOS）。
- **SQLite + 向量双索引**：关键词检索 + 语义检索双路召回。
- **RAG 问答**：配置 LLM API 后，可基于知识库进行生成式问答。
- **OCR 支持**：`_media/` 下的图片自动提取文字（需 PaddleOCR）。

---

## 目录结构

```text
enterprise-world-model/
├── 00-Inbox/              # 你唯一维护的文件夹：原始见闻、随手记
│   └── _media/            # 配套图片，管线自动 OCR
├── 10-Theory/             # AI 维护：行业通用知识
├── 20-Ontology/           # AI 维护：业务本体（字典、流程、系统、组织）
├── 30-Instances/          # AI 维护：实例（常量、数据画像、案例）
├── 40-Skills/             # AI 维护：技能（提示词、脚本、检查清单）
├── data/                  # 本地 SQLite 数据库 + 向量索引（自动创建）
│   ├── world-model.db
│   └── vector/
│       ├── index.pkl
│       └── embedding_model/  # 首次运行自动下载
├── pewm/                  # AI 处理管线配置与代码
│   ├── config/
│   │   ├── schemas/       # 各层级数据 Schema（AI 的提取宪法）
│   │   ├── extraction-rules.yaml
│   │   └── merge-policy.yaml
│   ├── prompts/           # AI 系统提示词
│   └── processors/        # 管线执行脚本
│       ├── database.py    # SQLite 数据层
│       ├── extractor.py   # 规则/LLM 提取器
│       ├── vectorizer.py  # 全文索引 + 向量索引
│       ├── vector_db.py   # numpy 向量库
│       ├── llm_client.py  # LLM API 客户端（DeepSeek/Kimi/MiniMax）
│       ├── rag.py         # RAG 问答管道
│       ├── user_profile.py # 用户身份与公司信息（注入到提示词）
│       ├── prompt_config.py # 可编辑的 AI 系统提示词
│       ├── ocr.py         # OCR 调度（本地 + API 双模式）
│       ├── ocr_api.py     # 云端 OCR API 适配器（百度/腾讯/阿里）
│       ├── progress_dialog.py # 通用进度条对话框
│       ├── config_manager.py  # 配置导出/导入/备份
│       └── utils.py
├── 90-Meta/
│   ├── README.md          # 本文件
│   └── query/             # 命令行查询接口
│       ├── chat.py        # RAG 对话式问答
│       └── search.py      # 混合检索（FTS5 + 向量）
├── gui.py                 # 桌面界面（tkinter）
├── start.py               # 一键启动桌面界面
├── run.py                 # 命令行运行管线
├── build.spec             # PyInstaller 打包配置
├── build.py               # 打包辅助脚本
└── requirements.txt
```

---

## 快速开始

### 方式一：下载可执行文件（推荐，Windows）

1. 下载 `个人企业世界模型.exe`。
2. 双击运行，弹出桌面窗口。
3. 数据会自动保存在同目录的 `data/` 文件夹中。
4. 如需启用 RAG 生成式问答，在「API 配置」Tab 中填写 LLM API Key。

### 方式二：源码运行

```bash
# 1. 安装完整依赖（含语义向量）
pip install -r requirements.txt

# 2. 启动桌面界面
python3 start.py
```

### 可选依赖

```bash
# OCR 图片文字识别（可选，首次运行会自动下载模型 ~150MB）
pip install paddlepaddle paddleocr
```

### 界面功能

启动后会打开一个桌面窗口，包含九个标签页：

- **💬 对话**：像聊天一样提问，系统从本地知识库中检索回答。启用 RAG 生成后，会调用 LLM 对检索结果做总结（自动注入用户/公司身份作为背景）。
- **🔍 检索**：通过关键词或语义查找相关知识片段（FTS5 + 向量双路召回）。
- **📝 Inbox 速记**：随手把见闻写进 `00-Inbox/`。
- **⚙️ 管线**：查看状态，一键运行 AI 管线整理知识并构建索引；支持重建向量索引；操作过程带进度条。
- **📚 文档管理**：查看所有已索引文档，支持软删除（进回收站）/恢复/硬删除（永久抹掉）/清空回收站。源文件丢失时自动软删除，随时可恢复。
- **⚙️ API 配置**：填写 LLM API Key（支持 DeepSeek/Kimi/MiniMax），测试连通性。
- **📷 OCR 配置**：选择 OCR 模式（API / 本地），API 模式支持百度/腾讯/阿里云三家提供商，每月有免费额度。
- **👤 用户信息**：编辑个人身份（姓名/职位/邮箱）与公司信息（名称/行业/产品），会自动注入到 AI 提示词中。
- **🤖 AI 提示词**：编辑 RAG 系统提示词、欢迎语和无结果回复模板，保存后下次对话立即生效。

### 典型使用流程

1. 在「Inbox 速记」页面写几条见闻（无需格式）。
2. 进入「管线」页面，点击「运行 AI 管线」。
3. 在「API 配置」中填写 LLM API Key（可选，启用 RAG 生成式问答）。
4. 去「对话」或「检索」页面查询知识。

---

## LLM API 配置

支持三种提供商，均走 OpenAI 兼容的 Chat Completions 接口：

| 提供商 | Base URL | 推荐模型 |
|--------|----------|----------|
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| Kimi (Moonshot) | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| MiniMax | `https://api.minimax.chat/v1` | `abab6.5s-chat` |

配置方式：
- **GUI（推荐）**：在「API 配置」Tab 中填写并点击「保存配置」，会持久化到 `~/.enterprise_world_model/config.json`，跨 exe 保留。
- **环境变量（兜底）**：设置 `OPENAI_API_KEY`（所有提供商都用这一个变量名，配合 GUI 中选的 provider 生效）。

---

## OCR 配置（图片文字识别）

支持两种模式，可在「OCR 配置」Tab 中切换：

### API 模式（推荐，exe 体积小）

云端识别，需要填 API Key，三家提供商都有免费额度：

| 提供商 | 免费额度 | 所需凭证 |
|--------|----------|----------|
| 百度智能云 | 每月 1000 次 | API Key + Secret Key |
| 腾讯云 | 每月 1000 次 | SecretId + SecretKey |
| 阿里云 | 按量付费 | AppCode |

推荐用**百度智能云**：去 [控制台](https://console.bce.baidu.com/ai/#/ai/ocr/overview/index) 创建应用拿到 Key，每月免费 1000 次。

### 本地模式

使用 PaddleOCR，识别精度高，但需要安装 paddlepaddle+paddleocr（exe 不内置，需手动安装）：

```bash
pip install paddlepaddle paddleocr
```

首次运行会自动下载中文识别模型（约 150MB）到 `data/ocr_models/`。


---

## 打包成可执行文件

如果你想自己打包成 `.exe`：

```bash
# 安装 PyInstaller
pip install pyinstaller

# 执行打包
python3 build.py
```

打包完成后，可执行文件位于 `dist/` 目录：
- Windows：`dist/个人企业世界模型.exe`
- Linux：`dist/个人企业世界模型`

**注意**：
- PaddleOCR 和 sentence-transformers 的模型文件体积较大（合计约 250MB），**不会打包进 exe**。
- 首次运行时会自动下载模型到 `data/` 目录，需保持网络连通。
- 如需离线使用，可提前下载模型放到目标机器的 `data/embedding_model/` 和 `data/ocr_models/` 目录。

---

## AI 管线说明

### 管线阶段

```text
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Stage 1   │ --> │   Stage 2   │ --> │   Stage 3   │ --> │   Stage 4   │ --> │   Stage 5   │
│    解析     │     │  分类与提取  │     │   关系构建   │     │  双索引构建  │     │   归档提交   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

| 阶段 | 说明 |
|------|------|
| **解析** | 读取 Markdown 文本，对 `_media/` 图片进行 OCR 提取文字 |
| **分类与提取** | 按 Schema 识别实体类型（术语、常量、案例、流程等），提取结构化数据 |
| **关系构建** | 自动建立实体间关联，生成 `[[双链]]` |
| **双索引构建** | SQLite FTS5 全文索引 + numpy 向量语义索引 |
| **归档提交** | Git 自动提交，标记 Inbox 文件为已处理 |

### 提取触发规则

管线通过 `pewm/config/extraction-rules.yaml` 控制提取行为。默认规则：

| 规则 ID | 触发关键词/模式 | 目标层级 | 优先级 |
|---------|----------------|---------|--------|
| `extract_term` | "我们叫"、"术语"、"定义为"、"指的是"、"简称"、"别名" | `20-Ontology/dictionary/` | 高 |
| `extract_constant` | `\d+\s*(分钟\|小时\|天\|%)`、`阈值`、`上限`、`下限`、`超时`、`重试`、`枚举` | `30-Instances/constants/` | 高 |
| `extract_case` | "故障"、"事故"、"P0"、"P1"、"复盘"、"踩坑"、"项目总结" | `30-Instances/cases/` | 中 |
| `extract_process` | "流程"、"审批"、"先...然后...最后"、"必须" | `20-Ontology/processes/` | 中 |
| `extract_system` | "系统"、"平台"、"服务"、"模块"、"数据库"、"API" | `20-Ontology/systems/` | 低 |

---

## 命令行高级用法

### 无界面运行管线（适合打包后部署/定时任务）

```bash
# 直接运行管线，不打开 GUI（透传所有 run.py 参数）
python3 start.py --pipeline

# 重置并重建所有索引
python3 start.py --pipeline --reset

# 不触发 Git 自动提交
python3 start.py --pipeline --no-git
```

### run.py 完整参数

```bash
# 运行管线
python3 run.py

# 重置处理标记并重建索引
python3 run.py --reset

# 跳过冲突文件继续处理
python3 run.py --skip-errors

# 禁用 Git 自动提交
python3 run.py --no-git

# 跳过向量索引（只用 FTS5）
python3 run.py --no-vector

# 跳过图片 OCR
python3 run.py --no-ocr

# 查看状态
python3 run.py --status

# 重建向量索引
python3 run.py --rebuild-vector

# 扫描并软删除磁盘上已不存在的文档
python3 run.py --reconcile

# 硬删除所有软删除的文档（清空回收站，不可逆！）
python3 run.py --purge
```

### RAG 对话问答

```bash
python3 90-Meta/query/chat.py "你的问题" --api-key sk-xxx --provider deepseek
```

### 混合语义检索

```bash
python3 90-Meta/query/search.py "支付失败的东西"
```

---

## 备份与迁移

整个项目是纯文本 + SQLite，可直接：

- **Git 备份**：所有 Markdown 和配置都在版本控制中
- **云同步**：将文件夹放入 iCloud Drive / Dropbox / OneDrive
- **迁移**：直接复制整个文件夹到另一台机器即可运行

数据库文件 `data/world-model.db` 和向量索引 `data/vector/` 可在 `.gitignore` 中排除，需要时重新构建：

```bash
# .gitignore
data/
.vector/
.env
__pycache__/
*.pyc
```

重新构建索引：

```bash
python3 run.py --reset
```

---

## 故障排查

### 界面无法打开

1. 确认使用的是包含 tkinter 的 Python 版本（大多数官方安装包都包含）。
2. 终端运行 `python3 start.py`，查看报错信息。
3. 若是打包后的可执行文件无法打开，尝试在命令行运行 exe，查看报错。

### 对话或搜索无结果

1. 先投递一些内容到 Inbox。
2. 在「管线」页面点击「运行 AI 管线」。
3. 确认 `data/world-model.db` 已生成。

### RAG 问答不生效

1. 在「API 配置」Tab 中填写 LLM API Key。
2. 点击「测试连通」确认 API 可用。
3. 在「对话」Tab 中勾选「RAG 生成」复选框。

### 向量检索失败

1. 检查是否安装了 `sentence-transformers`：`pip install sentence-transformers`。
2. 首次运行需要联网下载模型（~100MB）。
3. 在「管线」页面点击「重建向量索引」。

### OCR 失败

1. 检查是否安装了 `paddlepaddle` 和 `paddleocr`。
2. 首次运行需要联网下载模型（~150MB）。
3. 图片放在 `00-Inbox/_media/` 目录下，命名建议与 Inbox 文件同名或带日期前缀。

---

## 配置导出/导入/备份

在「用户信息」Tab 底部有三个配置管理按钮：

| 按钮 | 作用 |
|------|------|
| **📤 导出全部配置** | 把 LLM/OCR Key、用户信息、AI 提示词打包成一个 JSON 文件 |
| **📥 导入配置** | 从导出的 JSON 恢复全部配置（覆盖当前） |
| **💾 备份配置目录** | 把 `~/.enterprise_world_model/` 整体复制一份快照 |

典型使用场景：
- **换电脑**：旧电脑导出 → 拷 JSON 到新电脑 → 导入，全部配置一键迁移
- **团队共享提示词**：只分享导出的 JSON（可在文本编辑器里删除 API Key 字段脱敏）
- **升级前快照**：点「备份配置目录」保留完整备份，回滚无忧

导出的 JSON 结构示例：

```json
{
  "version": 1,
  "exported_at": "2026-07-13T10:00:00",
  "app": "个人企业世界模型",
  "llm": {"provider": "deepseek", "api_key": "sk-xxx", ...},
  "ocr": {"mode": "api", "provider": "baidu", ...},
  "profile": {"personal_name": "...", "company_name": "...", ...},
  "prompt":  {"system_prompt": "...", "greeting": "...", ...}
}
```

---

## 进度条体验

以下耗时操作都会弹出进度对话框，支持取消：

| 操作 | 位置 | 进度展示 |
|------|------|---------|
| **embedding 模型下载** | 重建向量索引 | `正在下载 bge-small-zh 模型... 45.2/100 MB` |
| **重建向量索引** | 管线 Tab | `模型就绪，开始重建索引...` → `完成` |
| **运行 AI 管线** | 管线 Tab | `正在扫描 Inbox...` → `处理 N 个文件...` |
| **批量 OCR** | 管线 Tab | `[1/10] 正在识别 2026-07-13-早会.png` |
| **对话问答** | 对话 Tab | 对话区域内显示 `⏳ 正在检索并生成回答...` |

进度对话框基于 tkinter Toplevel 实现，线程安全（`update()` 可从子线程调用），不会卡住主界面。

---

## 增量更新与删除语义

知识库采用**软删除优先**的保守策略，避免误操作丢失知识。

### 删除语义对比

| 操作 | FTS5 | 向量库 | 是否可恢复 | 触发方式 |
|------|------|--------|-----------|---------|
| **软删除**（进回收站） | 从 FTS 索引移除，但保留原文档 | 标记 `deleted_at`，向量保留 | ✅ 可恢复 | 自动（源文件丢失）/ 手动（GUI） |
| **硬删除**（永久抹掉） | DELETE 触发器自动清理 | 从矩阵中物理删除 | ❌ 不可逆 | 手动（GUI）/ `--purge` 命令 |
| **恢复** | 重新加入 FTS 索引 | 清除 `deleted_at` 标记 | - | 手动（GUI） |

### 增量更新触发链

1. **每次管线运行** → `reconcile()` 扫描所有文档的源文件 → 磁盘上不存在的自动软删除
2. **用户手动删除 Inbox md** → 下次跑管线时自动同步到知识库
3. **用户手动删除提取出的实体文件**（`20-Ontology/*` 等） → 同上
4. **GUI「📚 文档管理」Tab** → 显式软删/恢复/硬删

### 数据库兼容性

旧版本生成的 `data/world-model.db` 可以直接升级：
- `init_db()` 会自动用 `ALTER TABLE` 给 `documents` 表补 `deleted_at` 字段
- 旧数据默认 `deleted_at = ''`（视为未删除）
- 向量库旧索引也会自动补齐 `deleted_at` 字段

---

## 版本

- **Model Version**: 2.0
- **Schema Version**: 1.0
- **Last Updated**: 2026-07-13

---

> **记住：维护这个知识系统的成本，每天不超过 5 分钟——就是把见闻丢进 00-Inbox。其余交给 AI。**
