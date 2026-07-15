# TODO: P2 阶段剩余工作与长期任务

## 本阶段已完成

✅ **torch 环境部署**：
- 安装 torch 2.13.0+cpu、sentence-transformers、transformers
- 通过 `torch_validator` 验证健康
- 完整保留 bge 模型与依赖

✅ **P0/P1 全部落地**：
- 启动并行化 + embedding 延迟加载
- RAG 流式输出（SSE）
- 原生窗口标题栏（拖动/最小化/最大化/关闭）
- 增量向量索引 + 检索缓存
- 性能埋点 + 统一日志
- 测试扩展至 37 项（36 过，1 跳）

✅ **CI 配置**：新增 `.github/workflows/ci.yml`

## 剩余待办（P2 阶段）

### 1. 统一日志全面替换
- 替换 `pewm/processors/extractor.py` 中的 print
- 替换 `pewm/processors/llm_client.py` 中的 print
- 替换 `pewm/processors/vector_db.py` 剩余 print（如有）
- 添加 `logger.exception` 替代裸 `except Exception` 语句（共 ~66 处）

### 2. 崩溃日志本地记录与可选上报
- 在 `desktop.py` 和 `start.py` 配置 `sys.excepthook`
- 崩溃日志写入 `logs/crash-YYYY-MM-DD-HHMMSS.log`
- 在设置页新增可选上报开关（预留接口，无需立即部署服务端）

### 3. 前端单元测试
- 引入 Vitest + jsdom
- 测试 `app.js` 主要逻辑（API 封装、状态管理、主题切换）

### 4. 验证优化效果
- 使用 `pytest --cov` 验证代码覆盖率目标 ≥60%
- 在本地启动应用，实测启动时间、RAG 首 token、检索延迟

### 5. 打包与分发验证
- 运行 `python build.py`，验证打包产物中 torch 完整
- 轻量版与完整版仍需？（当前方案完整保留 torch）

### 6. 完善文档
- 输出独立的 `torch-install-report.md`
- 输出独立的 `p2-execution-report.md`
- 更新 `README.md` 说明新增功能

## 长期迭代

- 若有 CUDA 环境，支持 cuda 版 torch 安装
- 新增语音转文字、多模态 RAG（按原 Roadmap）
- 性能埋点可视化仪表盘
