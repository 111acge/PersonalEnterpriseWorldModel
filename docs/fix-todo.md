# PEWM 全量问题修复 TODO

来源：2026-07-18 全量代码排查报告（72 条确认问题：4 critical / 22 major / 46 minor）。
**状态：72/72 已全部修复完成（2026-07-18）。** 回归结果：后端 `pytest tests/` 179 项全部通过；前端 `pewm/web/static` 下 `npm test` 14 项全部通过。

## 修复约定

- 所有修复需保证 `pytest tests/` 通过；涉及前端改动的需保证 `pewm/web/static` 下 `npm test` 通过。
- 文件路径均相对项目根目录。

---

## Critical（4）— 已全部修复

- [x] **#1 合并机制失效（merge-policy 死代码）** — `pewm/processors/merge.py:51-63` + `pewm/config/schemas/*.yaml`
  根因：schema 的 content_template 无 YAML frontmatter，`_parse_frontmatter` 恒返回 `{}`，同名实体二次提取全量覆盖旧内容。
  修复：① 7 个 schema 的 content_template 头部加 `---\n{{ frontmatter }}\n---` 块；② `merge_entity`/`write_entity` 把合并后的 frontmatter 字典序列化进文件头（yaml.safe_dump，allow_unicode=True）；③ `_parse_frontmatter` 保持不变即可解析；④ 读取侧（database 索引、retrieval 展示）跳过 frontmatter 块只索引正文。
  **完成情况**：7 个 schema 模板已加 frontmatter 块；merge.py 新增 `_dump_frontmatter()`/`_render_with_frontmatter()`，合并策略（append/union/max）实测正确往返；`vectorizer.strip_frontmatter()` 在索引时剥离 frontmatter，仅正文入 FTS 与向量库。

- [x] **#2 管线写循环无异常隔离、标记先于索引** — `pewm/processors/__main__.py:207-230`
  修复：① 单文件处理（提取+写盘）包 try/except，失败记日志且不 `mark_inbox_processed`，按 `skip_errors` 决定继续/中止；② `mark_inbox_processed` 移到该文件实体成功索引之后；③ 运行结束时补一轮"磁盘存在但 documents 表无记录"的补索引扫描。
  **完成情况**：写盘循环整体 try/except；改为按文件即时索引成功后再标记；新增 `backfill_missing_indexes()` 补索引扫描。

- [x] **#3 硬删软删文档崩库 malformed** — `pewm/processors/database.py:198-218`
  修复：删除 `soft_delete_document` 中手动清 FTS 逻辑；软删仅 UPDATE deleted_at；提供一次性修复函数对存量库执行 FTS rebuild。
  **完成情况**：手动清 FTS 已删除；新增 `rebuild_fts()` 维护函数；补测试覆盖 add→soft_delete→hard_delete 不崩溃、restore 后 FTS 可命中（#10 同步修复）。

- [x] **#4 双库路径口径不一致（相对 vs 绝对）** — `pewm/processors/vectorizer.py:27-46`
  修复：① `index_documents` 构造向量 batch 时对 path 相对化；② `VectorDB` 写删入口统一相对化归一；③ 一次性迁移存量绝对路径；④ 新增两库 path 集合一致断言。
  **完成情况**：全部落实；`_init_db` 时执行 `_migrate_paths_to_rel` 一次性迁移（冲突去重）；测试断言通过。

## Major（22）— 已全部修复

- [x] **#5 `_find_existing_path` 前缀误合并** — `merge.py:113-115`：已改为仅匹配 `^stem-\d+$`（命中多个取最大序号），优先返回 stem 本体。
- [x] **#6 watcher 无重入保护** — `watcher.py:36-49`：已加 `_pipeline_running`/`_pending_rerun` 标志+锁，运行中事件只置待重跑标记，结束后补跑一次。
- [x] **#7 批量提取 source_index 归因错误** — `extractor.py:222-228`：新增 `_parse_source_index()` 容错转换；缺失/越界记 warning 交逐文件兜底，不再默认归 0。
- [x] **#8 sanitize_filename 不处理保留名/超长** — `utils.py:32-35`：保留名追加 `_`；截断 60 字符，空结果回退 `untitled`。
- [x] **#9 --reset 对 auto_merge=false 类型无限生成重复文件** — `merge.py:148-155`：已存在文件时做归一化内容比较（抹时间戳/自动 ID），未变则跳过新建。
- [x] **#10 restore_document 不重加 FTS** — 随 #3 修复（软删不再清 FTS，恢复即自然可见），补测试 `test_restore_then_fts_hit`。
- [x] **#11 检索缓存 GUI 端不失效** — `retrieval.py:14-38`：失效钩子已下沉到 database.py（`_notify_search_changed`）与 vector_db.py（`_notify_vector_changed`）全部写路径。
- [x] **#12 TF-IDF vocab 把 IDF 当列索引** — `vector_db.py:215-244`：vocab 改连续编号，IDF 另存字典相乘；测试断言 vocab 值集合==range(len(vocab))。
- [x] **#13 向量维度不向下兼容致静默停更** — `vector_db.py:346-377`：新增 `_align_vectors` 双向补零；kind 切换时 warning + 全量重编码。
- [x] **#14 LIKE 无排序、FTS 无 score** — `database.py:313-349`：LIKE 加 `ORDER BY updated_at DESC`；FTS 拆出 `_search_fts` 带 `bm25` score；2-gram 展开限 20 词。
- [x] **#15 Web 零鉴权、明文返回 API Key** — `web/app.py`：Host 头白名单 + 启动随机 token（`GET /api/auth/token` 发放，其余 /api/* 校验 X-Token）；LLM 配置返回打码，打码值/空值保存时不覆盖。
- [x] **#16 危险 POST 可被 CSRF 表单触发** — `web/app.py`：非 GET 的 /api/* 强制 `request.is_json` 否则 415。
- [x] **#17 管线锁伪互斥** — `web/app.py:483-518`：改 acquire(blocking=False) 失败即 409、任务 finally release；OCR/向量重建共用同锁并维护 running 标志。
- [x] **#18 进程级 sys.stdout 重定向错乱** — web/gui 两侧均改 `contextlib.redirect_stdout`，配合 #17 互斥。
- [x] **#19 配置导出/导入/备份任意路径 + 明文 Key** — `web/app.py:695-720`：新增 `_resolve_user_path` 限制在 Path.home() 下；导出默认 `include_api_keys=False`。
- [x] **#20 Markdown 渲染存储型 XSS** — `app.js`：escapeHtml 转义 `"` `'`；链接 URL 协议白名单（http/https/mailto），非法降级纯文本。
- [x] **#21 设置页表单 value 拼接 XSS** — `app.js`：动态表单改 createElement + element.value；其余未转义插入点一并修复。
- [x] **#22 tkinter 工作线程读控件** — `gui/tabs.py`：线程启动前主线程读值存局部变量。
- [x] **#23 用户笔记被打包进 exe** — `build.spec:26-31`：build.py 新增 `prepare_staging()`（仅含 .gitkeep 骨架），datas 改指 staging；.gitignore 增加内容目录 `/**/*.md`。**遗留**：需手动 `git rm -r --cached 00-Inbox 10-Theory 20-Ontology 30-Instances 40-Skills` 清理 git 索引中的历史笔记（文件保留在磁盘）。
- [x] **#24 `"***"` 占位符覆盖真实 OCR 密钥** — `config_manager.py`：导入逐字段识别 `"***"` 跳过保留原值。
- [x] **#25 LFS 指针通过模型完整性校验** — `build.py:74-81`：增加指针特征检测 + 大小下限（>1MB），命中提示 `git lfs pull`。
- [x] **#26 PaddleOCR 3.x 参数不兼容** — `ocr.py` + `requirements.txt`：依赖钉 `>=2.7.0,<3.0`；TypeError 捕获并提示安装 2.x。

## Minor（46）— 已全部修复

- [x] **#27** `extractor.py:197-206`：空文本改 `continue`；额度耗尽 break 时 logger.info 记录跳过数量。
- [x] **#28** `__main__.py:209-211`：已删除重复的第三层兜底（每文件最多 2 次 LLM 调用）。
- [x] **#29** `extractor.py:161-163`：超 6000 字符截断时强制追加整篇 note 保留全文，并记录截断长度。
- [x] **#30** `extractor.py:494-497`：按连词区分语序——"叫/称为/简称/又称"取宾语，"定义为/指的是"取主语。
- [x] **#31** `extractor.py` + `__main__.py`：新增 `run_pipeline(offline=...)` 与 CLI `--offline`，无 LLM 时走规则+note 兜底。
- [x] **#32** `utils.py`：`list_inbox_files` 同时收 `*.md` 与 `*.txt`，与 watcher 一致。
- [x] **#33** `__main__.py:59-71`：遍历所有锚点；无锚点路径保守不软删并记 warning。
- [x] **#34** `__main__.py:271-272`：删除恒真判断，改 try/except ImportError。
- [x] **#35** `watcher.py:96-107`：`stop()` 持锁 cancel `_timer` 再停 observer。
- [x] **#36** `database.py:284-308`：FTS 查询逐词双引号包裹；except 记 logger.warning。
- [x] **#37** `__main__.py:246-250`：git commit 检查 returncode，真实失败记 warning。
- [x] **#38** `retrieval.py`：VectorDB 改模块级单例 + 写后惰性 refresh。
- [x] **#39** `database.py` + `vector_db.py`：建连后 `PRAGMA journal_mode=WAL`、`busy_timeout=10000`。
- [x] **#40** `vector_db.py`：`_save_doc` 增加 commit 参数统一收口；维护 `{path: index}` 字典。
- [x] **#41** `vector_db.py:42-54`：三个 @property 坏死别名已删除。
- [x] **#42** `rag.py`：检索异常返回 `mode='retrieval_error'`，不再伪装空库。
- [x] **#43** `llm_client.py`：`OpenAI(..., timeout=60, max_retries=2)`；流式响应 with 包裹。
- [x] **#44** `web/app.py:414-449`：SSE 全程 try/except + 错误块；历史保存移 finally；加 no-cache 响应头。
- [x] **#45** `web/app.py`：流式端点读取 `use_rag` 条件化传参。
- [x] **#46** `web/app.py`：`request.args.get(..., type=int)` + 上限 1000。
- [x] **#47** `web/app.py:133-147`：白名单改 `relative_to` try/except。
- [x] **#48** `web/app.py`：文件名截断 50 字符；`open(..., 'x')` 排他写递增序号。
- [x] **#49** `web/app.py:42`：改 `app.json.ensure_ascii = False`。
- [x] **#50** `splash_controller.py`：retry 检测 Flask 线程存活则复用跳过重启。
- [x] **#51** `vector_db.py`：`_load_embedder` 加模块级锁（double-checked）。
- [x] **#52** `crash_handler.py`：同步设置 `threading.excepthook`。
- [x] **#53** `crash_handler.py`：崩溃日志文件名加毫秒（`%H%M%S-%f`）。
- [x] **#54** `desktop.py`/`gui.py`/`pewm/gui/app.py`：各入口补调幂等 `setup_logging()`。
- [x] **#55** `metrics.py`：模块级标志 + 双检锁确保建表一次。
- [x] **#56** `torch_validator.py`：`get_torch_status(refresh=False)` 模块级缓存。
- [x] **#57** `environment_status.py`：API 模式校验 credentials 非空；healthy 改显式布尔。
- [x] **#58** `gui/tabs.py`：改调 `pewm.processors.__main__.run_pipeline`，删除 runpy 依赖。
- [x] **#59** `progress_dialog.py`：控件操作改 `after(0,...)`；捕获 tk.TclError。
- [x] **#60** `config_manager.py`：导出默认 `include_api_keys=False`，JSON 增加 `contains_api_keys` 元字段，含密钥时返回安全提醒。
- [x] **#61** `ci.yml:19`：checkout 加 `lfs: true`。
- [x] **#62** `build.spec`：datas 增加 `('VERSION', '.')`。
- [x] **#63** `web/app.py:641-650`：OCR 保存缺省 `local` + mode/provider 白名单校验。
- [x] **#64** `web/app.py:695-711`：导入请求体加 `overwrite` 参数（默认 false）。
- [x] **#65** `ocr.py:107-113`：图片匹配收紧为 `== stem` 或 `startswith(stem + '-')`。
- [x] **#66** `ocr.py`：OCR 失败占位文本不含异常详情（仅记日志）。
- [x] **#67** `ocr.py`：`_load_paddle` 加 threading.Lock。
- [x] **#68** `ocr_api.py`：百度 token 按密钥缓存 + 过期前复用（减 1 小时余量）。
- [x] **#69** `ocr_api.py`：`_ocr_test.png` 用后 finally 删除。
- [x] **#70** `config_manager.py`：导入前 `_validate_payload` 整体校验；落盘改临时文件 + os.replace 原子写。
- [x] **#71** `config_manager.py`：新增 `restore_from_dir()`（先快照再还原）。
- [x] **#72** `.gitignore`：增加 `config-backup-*/`、`*config-export*.json`、`world-model-config-*.json`。

---

## 进度记录

- [x] 批次 A：管线核心（#1 #2 #5-9 #27-37）— merge/__main__/extractor/utils/watcher/schemas — 24 项测试通过
- [x] 批次 B：存储检索（#3 #4 #10-14 #36 #38-43 #51）— database/vector_db/vectorizer/retrieval/rag/llm_client — 59 项测试通过
- [x] 批次 C：Web/GUI（#15-22 #44-59 #63 #64）— web/gui/前端 — 后端 56 项 + 前端 14 项测试通过
- [x] 批次 D：配置/OCR/打包（#23-26 #60-62 #65-72）— config_manager/ocr/build/CI — 31 项测试通过
- [x] 全量测试回归：`pytest tests/` **179 passed**；`pewm/web/static` `npm test` **14 passed**；本 TODO 全部勾选
- [x] README 重写（已按修复后现状重写：pywebview 桌面端、7 页面、frontmatter 合并、--offline、安全加固、193 项测试）

## 遗留需手动跟进

1. `git rm -r --cached 00-Inbox 10-Theory 20-Ontology 30-Instances 40-Skills` 并提交，清理 git 索引中的历史笔记（#23，磁盘文件保留）。
2. 存量 FTS 损坏库可调用 `database.rebuild_fts()` 修复；存量向量库绝对路径已在初始化时自动迁移（#4）；旧 TF-IDF vocab 可用 `--rebuild-vector` 重建（#12）。
