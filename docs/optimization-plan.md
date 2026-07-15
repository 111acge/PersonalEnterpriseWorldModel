# 个人企业世界模型（PEWM）系统优化方案 v2.0

> 本文档基于 2026-07-15 的代码库状态（commit 基线）制定，覆盖业务/体验/性能/工程四个维度，目标是在 14 周内完成从「可用」到「好用」的跨越。  
> **状态更新**：2026-07-15 已完成本方案中 P0/P1 全部落地实施，测试覆盖率从 14 个用例提升至 37 个（36 passed / 1 skipped），torch 框架及 bge 模型完整保留、未做任何剥离。

---

## 一、执行摘要

PEWM 已完成从 0 到 1 的产品闭环：Flask + pywebview 桌面壳、SQLite + FTS5 全文检索、bge-small-zh 语义向量、LLM 提取管线、RAG 问答、配置导入导出等核心功能均已落地。当前处于「功能可用，但性能与体验有显著短板」的阶段。

本方案识别的**最高优先级问题**是：
1. **启动链路过长**（模型/Flask 串行加载，用户感知 5–15 秒）。
2. **分发体积过大**（单文件 exe 约 288–318MB，torch 占大头）。
3. **交互体验不够原生**（无边框窗口缺少拖动/最小化/最大化，RAG 无流式输出）。
4. **可观测性缺失**（51 处 `print`、66 处裸 `except Exception`、无日志/指标/崩溃上报）。
5. **测试覆盖不足**（14 个用例，未覆盖管线/RAG/前端）。

优化后目标：完整保留 torch 框架与 bge 预训练模型，不做任何剥离；本地模型已存在时启动 ≤3 秒；RAG 首 token ≤2 秒；测试覆盖率 ≥60%；新增 torch 环境有效性自动化验证。

---

## 二、当前 baseline 诊断

### 2.1 项目现状快照

| 模块 | 状态 | 关键文件 |
|------|------|----------|
| 桌面启动器（pywebview + splash） | 已上线 | `pewm/web/desktop.py`、`pewm/web/splash_controller.py` |
| Flask Web API | 已上线，9 大接口群 | `pewm/web/app.py` |
| SQLite 数据层（含 FTS5） | 已上线 | `pewm/processors/database.py` |
| 向量检索（bge / TF-IDF 双模） | 已上线，自动回退 | `pewm/processors/vector_db.py` |
| 混合检索（RRF + embedding rerank） | 已上线 | `pewm/processors/retrieval.py` |
| AI 提取管线（LLM + 规则 + note 兜底） | 已上线，支持批量 | `pewm/processors/__main__.py`、`pewm/processors/extractor.py` |
| RAG 问答 | 已上线，非流式 | `pewm/processors/rag.py` |
| 配置管理（LLM/OCR/用户/提示词） | 已上线 | `pewm/processors/config_manager.py` 等 |
| 前端 SPA | 已上线，单 JS 文件 700+ 行 | `pewm/web/static/js/app.js` |
| 打包 | PyInstaller onefile | `build.py`、`build.spec` |
| 测试 | 14 个用例 | `tests/` |

### 2.2 核心指标 baseline

| 维度 | 当前 baseline | 目标值 | 备注 |
|------|--------------|--------|------|
| **包体积** | 288–318 MB（完整版 exe） | 保持 250–318MB（torch 完整保留，不剥离） | torch + transformers + bge 占主要体积，按约束不可裁剪 |
| **启动时间** | 5–15 秒（本地模型存在）<br>30–60 秒（首次解压/下载模型） | ≤3 秒（本地模型已存在）<br>≤8 秒（首次有模型） | 通过延迟加载 + 并行初始化优化，不裁剪 torch |
| **运行时内存** | 1.5–3 GB | 完整版 <2GB | torch 常驻内存高，按约束完整保留 |
| **检索延迟** | FTS5 <100ms；bge 向量 200–500ms | FTS5 <80ms；bge 向量 <200ms | 向量维度变化时会触发全量重建 |
| **RAG 首 token** | 2–8 秒（取决于 LLM 与上下文长度） | ≤2 秒 | 当前为整段返回，无流式 |
| **AI 管线成本** | 每批次 1 次 LLM 调用（最多 12000 字符） | 保持或再降 30% | 已做批量提取，但 token 预算可更精细 |
| **测试覆盖** | 14 个用例 | ≥60% | 未覆盖 pipeline、RAG、前端、配置 |
| **代码质量** | 51 处 `print`、66 处裸 `except Exception` | 关键路径全部接入日志；异常分类处理 | 影响线上排障 |
| **崩溃/指标** | 无埋点、无上报 | 关键路径 100% 埋点 | 完全黑盒 |
| **torch 环境验证** | 无自动化验证 | 每次启动/CI 自动验证 torch 加载、张量计算、CUDA/CPU 加速 | 保障 torch 完整可用 |

> 注：基线数据来自 README 与代码静态分析；精确数据需在落地阶段通过性能埋点采集。torch 框架及 bge 预训练模型按约束完整保留，不剥离、不裁剪。

### 2.3 关键瓶颈定位

#### 瓶颈 1：启动链路串行阻塞
`SplashController._run_init()` 按固定顺序执行：读配置 → 初始化 DB → 初始化向量库 → 加载 embedding 模型 → 启动 Flask。任一环节慢都会阻塞后续环节，且模型加载在启动期强制完成，即使用户进入主界面后暂不检索也会等待。

#### 瓶颈 2：embedding 模型在启动期强制加载
`vector_db.py` 优先加载 `sentence-transformers` + `torch` + `bge-small-zh`。按项目约束，torch 框架及 bge 预训练模型必须完整保留，不可剥离。当前问题在于模型在启动期被强制加载，阻塞了主界面展示：
- 启动耗时增加 3–10 秒；
- 即使用户进入主界面后暂不检索，也需等待模型加载完成。

解决方向改为**延迟加载 + 后台预热**：启动期不加载模型，进入主界面后在后台空闲线程预热，或在首次检索时加载。

#### 瓶颈 3：向量索引维度变化触发全量重建
`VectorDB.add()` 在 `vectors.shape[1] != vec.shape[1]` 时会调用 `_rebuild_all()`。TF-IDF 模式下 vocab 会随新文档增长，导致旧文档被反复重编码；大库时成本不可忽略。

#### 瓶颈 4：RAG 对话无流式输出
`pewm/web/app.py` 的 `/api/chat` 与 `rag_answer()` 均为同步整段返回。长答案时用户等待 5–10 秒才看到结果，且 UI 显示「正在检索并生成回答…」无法反馈真实进度。

#### 瓶颈 5：桌面窗口缺少原生操作
`desktop.py` 创建 `frameless=True` 窗口，但前端未实现自定义标题栏，用户无法拖动窗口、最小化/最大化，只能点击右上角唯一的关闭按钮。

#### 瓶颈 6：可观测性完全缺失
- 51 处 `print` 输出散落各模块，无法按级别过滤、无法持久化；
- 66 处 `except Exception` 吞掉大量错误细节；
- 无启动耗时、检索耗时、LLM 调用耗时等关键指标；
- 无崩溃日志本地记录，更无上报通道。

#### 瓶颈 7：测试覆盖薄弱
14 个用例集中在 database/merge/retrieval/vector_db/web 的轻量路径，未覆盖：
- AI 管线端到端流程；
- RAG 提示词组装与 LLM fallback；
- OCR 双模式；
- 前端 JS 交互；
- PyInstaller 打包产物验证。

---

## 三、分阶段优化目标

### 阶段一：启动与运行时优化（P0，第 1–4 周）

| 目标 | 成功标准 | 时间节点 | 优先级 |
|------|---------|---------|--------|
| 模型延迟加载 | embedding 模型改为首次检索/首次重建时才真正加载 | 第 2 周末 | P0 |
| 启动链路并行化 | 配置/DB/向量库/Flask 并行初始化，启动耗时 ≤3 秒（本地模型已存在） | 第 3 周末 | P0 |
| torch 环境自动验证 | 启动时/CI 中自动验证 torch 加载、张量计算、CUDA/CPU 可用性 | 第 4 周末 | P0 |
| torch 完整性保障 | `build.spec` 保留全部 torch 核心模块与 bge 模型，打包后验证 torch 可加载 | 第 4 周末 | P0 |

### 阶段二：交互体验升级（P1，第 5–7 周）

| 目标 | 成功标准 | 时间节点 | 优先级 |
|------|---------|---------|--------|
| RAG 流式输出 | `/api/chat` 支持 SSE，首 token ≤2 秒，前端逐字渲染 | 第 6 周末 | P1 |
| 原生窗口壳 | 自定义标题栏支持拖动、最小化、最大化/还原、关闭 | 第 7 周末 | P1 |
| 主题与动画 | 深色/浅色切换无闪烁；页面切换、Toast、弹窗有 200ms 过渡动画 | 第 5 周末 | P1 |
| 启动画面信息丰富 | splash 展示当前阶段耗时、模型下载进度、可取消按钮 | 第 6 周末 | P1 |

### 阶段三：检索与管线效率（P1，第 8–11 周）

| 目标 | 成功标准 | 时间节点 | 优先级 |
|------|---------|---------|--------|
| 增量向量索引 | 仅新增/修改/删除文档触发向量计算，维度变化不触发全量重建 | 第 9 周末 | P1 |
| 检索结果缓存 | 最近 100 条查询 LRU 缓存，缓存命中延迟 <20ms | 第 10 周末 | P1 |
| 检索性能优化 | bge 向量检索 <200ms（1000 文档规模） | 第 10 周末 | P1 |
| LLM 批量提取调优 | 单批次 token 预算动态控制，批量失败率 <5% | 第 11 周末 | P1 |

### 阶段四：稳定性与可观测性（P2，第 12–14 周）

| 目标 | 成功标准 | 时间节点 | 优先级 |
|------|---------|---------|--------|
| 统一日志体系 | 用 `loguru` 或标准 logging 替代 80% 以上 `print`，按天轮转保留 7 天 | 第 12 周末 | P2 |
| 性能埋点 | 启动/检索/对话/管线关键路径 100% 埋点，`/api/metrics` 可查询 | 第 12 周末 | P2 |
| 崩溃日志 | 本地记录未捕获异常，可选匿名上报 | 第 13 周末 | P2 |
| 测试覆盖 ≥60% | 新增 pipeline、RAG、OCR、前端单元测试，CI 全绿 | 第 14 周末 | P2 |

---

## 四、具体落地实施步骤

### 4.1 启动与运行时优化

#### 4.1.1 模型延迟加载（第 1–2 周）

**改动点：**
1. 修改 `SplashController._run_init()`，移除 `_phase_load_embedder()` 的强制调用；启动期不再加载 embedding 模型。
2. 将 `_phase_load_embedder()` 暴露为后台预热接口，启动完成后在空闲线程中预加载模型（不影响主界面进入）。
3. 在 `VectorDB.search()`、`VectorDB.add()`、`VectorDB.rebuild()` 首次调用 `_load_embedder()` 时显示可中断的进度提示。

**验收标准：**
- 本地模型存在时，从双击 exe 到主界面展示 ≤3 秒。
- 进入主界面后首次点「检索」或「对话」时，模型在 1–3 秒内完成加载并有进度提示。

#### 4.1.2 启动链路并行化（第 2–3 周）

**改动点：**
1. 在 `SplashController` 中引入 `concurrent.futures.ThreadPoolExecutor`。
2. 将无依赖的初始化阶段并行化：
   - 线程 A：读配置（LLM/OCR/用户/提示词）。
   - 线程 B：初始化 SQLite 数据库。
   - 线程 C：初始化向量库（仅建表/加载已有向量，不加载模型）。
3. 待 A/B/C 完成后，再串行启动 Flask（Flask 依赖 DB 已就绪）。
4. 前端 splash 进度条改为按「已完成阶段数」而非固定百分比展示。

**验收标准：**
- 本地模型存在时，启动总耗时较基线降低 ≥50%。
- 任一阶段失败时，错误信息能定位到具体阶段。

#### 4.1.3 torch 环境有效性自动验证（第 2–4 周）

**改动点：**
1. 新增 `pewm/processors/torch_validator.py`：
   - 检测 `torch` 是否可导入；
   - 验证张量创建与基础运算（`torch.tensor`、`torch.matmul`、`torch.nn.functional`）；
   - 检测 CUDA 可用性，若不可用则确认 CPU 后端可用；
   - 验证 `sentence_transformers` 可导入；
   - 验证本地 `bge-model/` 目录完整（config.json、pytorch_model.bin 等存在）。
2. 在 `SplashController._run_init()` 中，完成基础初始化后调用 `torch_validator.validate()`，将结果写入 `logs/torch-validation.log` 与指标表。
3. 若验证失败，启动画面明确提示用户 torch 环境异常，并提供排查建议。
4. 新增 `tests/test_torch_validator.py`：mock 正常/异常场景，确保验证逻辑可靠。

**验收标准：**
- 每次启动自动输出 torch 验证报告。
- CI 中 `pytest tests/test_torch_validator.py` 通过。
- 人为破坏 bge 模型文件时，启动画面给出明确错误提示。

#### 4.1.4 torch 完整性保障与打包验证（第 3–4 周）

**改动点：**
1. 保留 `build.spec` 中所有 torch 相关 `hiddenimports`（`torch`、`torch.nn`、`torch.nn.functional`、`transformers`、`sentence_transformers`、`tokenizers`、`huggingface_hub`、`safetensors`），不删除、不排除。
2. 在 `build.py` 打包完成后，自动运行验证脚本：
   - 解压/运行 exe 到临时目录；
   - 检查 `torch` 可导入；
   - 检查 `bge-model/` 数据文件完整；
   - 输出 `dist/torch-validation-report.json`。
3. 在「设置」页「模型管理」面板显示：
   - 当前 torch 版本；
   - CUDA/CPU 后端状态；
   - bge 模型加载状态。

**验收标准：**
- 打包后的 exe 能正常导入 torch 并完成张量计算。
- `build.spec` 中不减少任何 torch 相关 hiddenimports。
- 设置页可实时查看 torch 运行状态。

### 4.2 交互体验升级

#### 4.2.1 RAG 流式输出（第 5–6 周）

**改动点：**
1. 后端：
   - 修改 `llm_client.chat_completion()`，支持 `stream=True` 返回生成器。
   - 新增 `rag_answer_stream()` 生成器函数，逐段 yield `{"delta": "...", "sources": [...]}`。
   - 在 `app.py` 新增 `/api/chat/stream` SSE 端点，Content-Type 为 `text/event-stream`。
2. 前端：
   - `sendChat()` 优先调用 `/api/chat/stream`，使用 `EventSource` 监听。
   - 收到 delta 时逐字追加到 assistant 消息气泡。
   - 保留 `/api/chat` 作为非流式 fallback（弱网或旧版前端兼容）。

**验收标准：**
- 配置有效 LLM 后，点击发送后 ≤2 秒出现第一个字。
- 网络中断时自动 fallback 到非流式接口，用户无感知。
- 来源链接在流式输出结束后追加。

#### 4.2.2 原生窗口操作（第 6–7 周）

**改动点：**
1. 前端 `index.html` 顶部增加自定义标题栏：
   - 左侧：应用图标 + 标题。
   - 右侧：最小化、最大化/还原、关闭按钮。
2. 通过 `pywebview.js_api` 调用：
   - `window.pywebview.api.minimize_window()`
   - `window.pywebview.api.maximize_window()` / `window.pywebview.api.restore_window()`
   - `window.pywebview.api.close_window()`
3. 标题栏支持鼠标按下拖动窗口：`window.pywebview.api.start_drag()` 监听鼠标移动并调用 `window.move()`。
4. 在 `desktop.py` 对应的 `SplashController` 或新增 `WindowController` 中实现上述 API。

**验收标准：**
- 用户可按住标题栏拖动窗口。
- 最小化/最大化/关闭按钮功能正常。
- 双击标题栏在最大化与还原之间切换。

#### 4.2.3 主题与动画（第 5 周）

**改动点：**
1. `style.css` 使用 CSS 变量统一定义颜色、圆角、阴影，移除硬编码色值。
2. 给 `.page`、`.modal`、`.toast` 增加 `transition`。
3. 主题切换时通过 `data-theme` 属性切换，避免整页重绘导致的闪烁。

**验收标准：**
- 深色/浅色切换无可见闪烁。
- 所有页面元素颜色均由 CSS 变量控制。

### 4.3 检索与管线效率

#### 4.3.1 增量向量索引（第 8–9 周）

**改动点：**
1. 在 `vectors` 表中已有 `content_hash` 字段，利用它判断文档是否变更。
2. 修改 `VectorDB.add()` 逻辑：
   - 新文档：仅编码该文档并 append 到矩阵。
   - 变更文档：仅更新对应行。
   - 删除文档：仅标记 `deleted_at`（已支持）。
3. **维度不变性改造**：TF-IDF 模式下，预先定义固定维度（如取训练集 top 65536 个 2-gram），避免后续新增文档导致维度膨胀触发全量重建。
4. 新增 `VectorDB.add_batch()` 接口，批量编码、批量写入 SQLite，减少事务开销。
5. `vectorizer.index_documents()` 改为调用 `add_batch()`。

**验收标准：**
- 1000 文档库新增 1 篇文档，向量更新耗时 <1 秒。
- 维度变化不再触发全量重建。
- 数据一致性：重建后的检索结果与逐条添加一致。

#### 4.3.2 检索缓存（第 9–10 周）

**改动点：**
1. 在 `retrieval.py` 增加基于 `functools.lru_cache` 或自定义 TTLCache 的查询缓存。
2. 缓存键：`hash(query + entity_type + top_k + vec_k + rerank)`。
3. 缓存失效：
   - 文档增删改时清空缓存。
   - 提供最大条目数 100，LRU 淘汰。
4. 对 FTS5 单独结果也做缓存。

**验收标准：**
- 重复查询命中缓存时，端到端延迟 <20ms。
- 文档变更后下一次查询结果立即反映变更。

#### 4.3.3 检索性能优化（第 10 周）

**改动点：**
1. 对 bge 向量检索使用 `numpy` 矩阵乘法已足够快，但可在以下方面优化：
   - 预先将 `vectors` 矩阵按 `entity_type` 分块，按类型过滤时避免全量计算。
   - 对超大库（>1 万文档）引入 IVF/倒排索引或启用 `faiss-cpu` 作为可选加速后端。
2. 限制单次检索返回的 `top_k` 默认值为 10，避免前端渲染压力。

**验收标准：**
- 1000 文档规模下，bge 向量检索 <200ms。
- 不引入强制重依赖（faiss 作为可选）。

#### 4.3.4 LLM 批量提取调优（第 10–11 周）

**改动点：**
1. 当前 `_llm_extract_batch()` 已实现，但单批次硬编码 12000 字符上限。改为按 token 估算动态控制（使用 tokenizer 或字符数/4 估算）。
2. 批量结果后校验：
   - 若某 source_index 缺失关键字段，自动对该文件走 `_llm_extract()` 单文件兜底。
   - 记录批量失败率指标。
3. 在管线中增加「批量 vs 单文件」耗时对比埋点。

**验收标准：**
- 批量提取失败率 <5%。
- 同等文档量下，LLM 调用次数较逐文件降低 ≥40%。

### 4.4 稳定性与可观测性

#### 4.4.1 统一日志体系（第 12 周）

**改动点：**
1. 引入 `loguru`（或标准库 `logging`）作为全局日志器。
2. 配置日志输出：
   - 控制台：`INFO` 级别。
   - 文件：`logs/app.log`，按天轮转，保留 7 天。
3. 将 `pewm/` 下 51 处 `print` 逐步替换为 `logger.debug/info/warning/error`。
4. 对 66 处 `except Exception` 增加日志记录，至少打印 traceback。

**验收标准：**
- 运行后 `logs/app.log` 自动生成并按天轮转。
- 关键错误可定位到具体模块与行号。

#### 4.4.2 性能埋点（第 12 周）

**改动点：**
1. 新增 `pewm/processors/metrics.py`：
   - 本地 SQLite 表 `metrics(event, duration_ms, success, error_msg, created_at)`。
   - 装饰器 `@timed("event_name")` 用于函数耗时统计。
2. 在以下路径接入埋点：
   - `start_desktop_app` 总启动时间。
   - `SplashController` 各阶段耗时。
   - `VectorDB.search` / `VectorDB.add_batch`。
   - `hybrid_search` / `_embedding_rerank`。
   - `rag_answer` / `chat_completion`。
   - `run_pipeline` 各阶段。
3. 在「设置」页新增「诊断信息」面板，展示最近 100 条指标。

**验收标准：**
- 关键路径均有耗时指标记录。
- `/api/metrics` 返回最近 N 条指标供调试。

#### 4.4.3 崩溃日志（第 13 周）

**改动点：**
1. 在 `start.py` 与 `desktop.py` 顶层增加 `sys.excepthook`，将未捕获异常写入 `logs/crash-YYYY-MM-DD-HHMMSS.log`。
2. 增加可选「匿名上报」开关：用户同意后，将 crash 日志摘要 POST 到指定服务器（需后续部署接收端）。
3. 对 PyInstaller 打包产物，确保崩溃日志写在 exe 同目录的 `logs/` 下，而不是临时目录。

**验收标准：**
- 手动触发异常后，`logs/` 下生成 crash 日志。
- 崩溃信息包含 traceback、Python 版本、应用版本。

#### 4.4.4 测试扩展（第 13–14 周）

**改动点：**
1. 新增 `tests/test_pipeline.py`：
   - mock LLM 与 OCR，测试 Inbox 文件 → 实体提取 → 索引全流程。
2. 新增 `tests/test_rag.py`：
   - mock `hybrid_search` 与 `chat_completion`，测试提示词组装与 fallback 逻辑。
3. 新增 `tests/test_ocr.py`：
   - 测试 OCR 配置读写、云端 OCR mock 调用。
4. 前端测试：引入 Vitest + jsdom，覆盖 `app.js` 中的 API 封装、主题切换、聊天消息追加。
5. CI：配置 GitHub Actions，每次 PR 跑 `pytest` 与前端测试。

**验收标准：**
- 测试覆盖率从当前 <10% 提升到 ≥60%。
- GitHub Actions 全绿方可合并。

---

## 五、优化效果验证机制

### 5.1 A/B 与对照测试

| 优化项 | 对照组（A） | 实验组（B） | 核心指标 | 判定标准 |
|--------|------------|------------|---------|---------|
| torch 环境验证 | 无验证 | 启动/CI 自动验证 | torch 导入成功率、张量运算通过率、模型文件完整性 | 验证通过率 100% |
| 启动并行化 | 串行启动 | 并行启动 | 启动总耗时、阶段耗时分布 | B 启动时间 ≤A 的 50% |
| 模型延迟加载 | 启动期强制加载模型 | 首次检索时加载模型 | 到主界面时间、首次检索时间 | 到主界面时间 ≤3s；首次检索 ≤5s |
| RAG 流式输出 | `/api/chat` 整段返回 | `/api/chat/stream` SSE | 首 token 时间、用户主观满意度 | 首 token ≤2s |
| 增量向量索引 | 全量重建 | 增量更新 | 管线耗时、CPU 占用、内存波动 | 单文档更新耗时 <1s |
| 检索缓存 | 无缓存 | LRU 缓存 | 重复查询延迟、缓存命中率 | 命中延迟 <20ms；命中率 ≥30% |

### 5.2 灰度发布

1. **内部测试（每阶段完成后 1 周）**：
   - 团队内部使用最新 build 处理真实 Inbox 数据。
   - 收集 `logs/app.log` 与 `/api/metrics` 数据。
2. **Beta Channel（第 8 周起）**：
   - GitHub Release 发布 `个人企业世界模型-beta.exe`，标注已知问题。
   - 提供「回退到上一稳定版」入口。
3. **Stable Channel（第 14 周）**：
   - 所有 P0/P1 目标达成后发布稳定版。
   - 保留旧版本下载链接至少 1 个月。

### 5.3 指标监控与告警

1. **本地指标表**：
   ```sql
   CREATE TABLE metrics (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       event TEXT NOT NULL,
       duration_ms INTEGER,
       success BOOLEAN,
       error_msg TEXT,
       created_at TEXT NOT NULL
   );
   ```
2. **关键指标看板（设置页「诊断信息」）**：
   - 启动总耗时趋势（最近 10 次）。
   - 检索平均延迟 / P95 延迟。
   - LLM 调用成功率 / 平均耗时。
   - 管线处理成功率。
3. **告警规则（可选上报）**：
   - 连续 3 次启动 >10 秒触发日志告警。
   - LLM 调用成功率 <80% 时提示用户检查 API Key。

---

## 六、风险预判与应急预案

| 风险 | 影响 | 概率 | 规避/应急预案 |
|------|------|------|--------------|
| torch 验证误报导致启动失败 | 正常环境无法进入主界面 | 低 | 验证逻辑仅记录并提示，不阻塞启动；提供「忽略并继续」按钮；日志保留完整验证报告 |
| SSE 流式改造引入连接不稳定 | 对话中断、前端卡死 | 中 | 保留 `/api/chat` 非流式 fallback；前端 5 秒内未收到首 token 自动切换；增加心跳包 |
| 增量向量逻辑 bug 导致索引不一致 | 检索不到新内容 | 中 | 保留「重建向量索引」按钮；每周自动全量校验一次；提供 `--rebuild-vector` 命令行修复 |
| 新窗口壳导致拖动/关闭失效 | 用户体验倒退 | 低 | 保留旧版无边框模式配置项；出现问题时用户可回退到旧版 build |
| 日志文件无限增长 | 磁盘占满 | 低 | 日志按天轮转，单文件 ≤50MB，保留 7 天；定期清理旧日志 |
| 测试不足引发回归 | 核心功能损坏 | 中 | 每次提交跑 pytest；PR 必须全绿；Beta 阶段跑满 1 周再发 Stable |
| Python 3.14 兼容性问题 | 打包/运行失败 | 低 | 在 CI 中增加 3.11/3.12/3.14 多版本测试；README 明确推荐 3.11–3.12 |
| 第三方 LLM API 变更 | RAG 不可用 | 低 | llm_client 层做 provider 适配；失败时明确提示用户检查 base_url/model |

---

## 七、资源配置与协作

### 7.1 人员分工建议

| 角色 | 负责内容 |
|------|---------|
| 后端工程师 | 启动优化、向量索引、日志/埋点、测试 |
| 前端工程师 | SSE 流式、自定义标题栏、主题动画、前端测试 |
| 算法/AI 工程师 | embedding 模型选型、检索精度评估、LLM 批量策略 |
| 测试/运维 | CI 配置、打包验证、灰度发布、崩溃日志接收端 |

### 7.2 工具与依赖

| 用途 | 工具/库 |
|------|---------|
| 日志 | `loguru`（推荐）或标准库 `logging` |
| 性能埋点 | 自研 `metrics.py` + SQLite |
| 前端测试 | `Vitest` + `jsdom` |
| 后端测试 | `pytest` + `pytest-cov` |
| CI | GitHub Actions |
| 打包 | PyInstaller（已有） |
| 可选向量加速 | `faiss-cpu`（可选依赖） |

### 7.3 本周即可启动的动作

1. **立即做**：在 `pewm/processors/metrics.py` 实现 `@timed` 装饰器，并在 `start_desktop_app`、`rag_answer`、`hybrid_search` 接入埋点（工作量小，收益大）。
2. **本周做**：将 `SplashController` 中 `_phase_load_embedder()` 改为延迟加载，验证启动时间变化，同时确保 torch 仍能在首次检索时完整加载。
3. **本周做**：新增 `pewm/processors/torch_validator.py` 与 `tests/test_torch_validator.py`，建立 torch 环境自动化验证基线。
4. **并行做**：给主窗口加自定义标题栏（拖动、最小化、最大化、关闭）。

---

## 八、文档与复盘

### 8.1 需要补充/更新的文档

| 文档 | 用途 | 负责人 |
|------|------|--------|
| `docs/architecture.md` | 模块关系图、数据流、启动时序 | 后端 |
| `docs/performance.md` | 性能 baseline、优化前后对比、调优方法 | 后端 |
| `docs/releasing.md` | 打包/发布/回滚流程、版本号规则 | 运维 |
| `docs/frontend.md` | 前端组件、API 约定、主题变量 | 前端 |
| `CHANGELOG.md` | 每版本变更记录 | 维护人 |

### 8.2 复盘节奏

- **每阶段结束**：召开 30 分钟复盘会，检查目标达成情况、指标变化、新引入问题。
- **每两周**：更新一次本方案文档，记录实际耗时与偏差原因。
- **第 14 周末**：做整体复盘，决定是否进入下一批优化（如 Roadmap 中的语音转文字、多模态 RAG）。

---

## 九、附录

### 9.1 关键代码位置

| 模块 | 文件 |
|------|------|
| 启动器 | `pewm/web/desktop.py` |
| 启动控制器 | `pewm/web/splash_controller.py` |
| Flask API | `pewm/web/app.py` |
| 向量库 | `pewm/processors/vector_db.py` |
| 混合检索 | `pewm/processors/retrieval.py` |
| RAG | `pewm/processors/rag.py` |
| LLM 客户端 | `pewm/processors/llm_client.py` |
| 提取器 | `pewm/processors/extractor.py` |
| 管线入口 | `pewm/processors/__main__.py` |
| 打包配置 | `build.spec` |
| 前端 JS | `pewm/web/static/js/app.js` |

### 9.2 当前静态分析摘要

- Python 代码总行数：约 6,500 行（含注释与空行）。
- `print` 调用：51 处。
- 裸 `except Exception`：66 处。
- 测试用例：14 个。
- 前端 JS：`pewm/web/static/js/app.js` 约 720 行。

---

*文档版本：v2.0*  
*创建/更新时间：2026-07-15*  
*维护人：PEWM-Assistant*  
*状态：待评审，评审通过后可进入阶段一实施*
