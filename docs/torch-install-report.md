# torch 环境安装与验证报告

> 生成时间：2026-07-16
> 验证工具：`pewm/processors/torch_validator.py`

## 一、环境信息

| 项目 | 值 |
|------|-----|
| Python | 3.12.10（Windows） |
| torch | 2.13.0+cpu |
| sentence-transformers | 已安装 |
| 向量模型 | bge-small-zh-v1.5（本地 `bge-model/`，512 维） |
| 计算后端 | CPU（CUDA 不可用） |

## 二、安装过程

本项目按约束**完整保留** torch 框架与 bge 预训练模型，不做任何剥离或裁剪。

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install sentence-transformers transformers
```

依赖同时写入 `requirements.txt`，CI 通过 `pip install -r requirements.txt` 复现。

## 三、自动化验证

### 3.1 验证项

`torch_validator.validate_torch_environment()` 每次启动时自动执行以下检查：

1. torch 可导入（`import torch`）
2. 张量基础运算（`torch.tensor` / `torch.matmul` / `F.softmax`）
3. CUDA / CPU 后端可用性
4. sentence-transformers 可导入
5. 本地 `bge-model/` 必要文件完整（`config.json` / `pytorch_model.bin` / `tokenizer.json` / `vocab.txt`）

### 3.2 最近一次验证结果

```json
{
  "torch_version": "2.13.0+cpu",
  "backend": "CPU",
  "cpu_available": true,
  "cuda_available": false,
  "sentence_transformers_available": true,
  "bge_model_files_ok": true,
  "healthy": true,
  "errors": []
}
```

验证结果写入指标表（`metrics` 表，事件 `torch.validation`），并可在「设置 → 诊断」面板实时查看。

### 3.3 测试覆盖

`tests/test_torch_validator.py` 共 4 项用例，全部通过：

- 验证报告包含基础字段
- bge 模型缺失时正确报错
- 验证结果写入指标表
- `get_torch_status()` 返回 healthy 标志

## 四、打包完整性

`build.spec` 保留全部 torch 相关 `hiddenimports`：

```
torch, torch.nn, torch.nn.functional,
transformers, sentence_transformers,
tokenizers, huggingface_hub, safetensors
```

`build.py` 打包完成后自动执行 `verify_build_artifact()`，输出 `dist/torch-validation-report.json`，确认：

- exe 存在且非空
- `build.spec` 未裁剪任何 torch hiddenimports
- `bge-model/` 必要文件完整
- 当前环境 torch 可导入

最近一次验证：`healthy = true`，exe 体积约 288 MB。

## 五、已知限制

- 当前安装为 CPU 版 torch，CUDA 环境需另行安装 CUDA 版 wheel
- 首次在无本地模型的机器上启动时，bge 模型会在线下载（约 100 MB），已有本地 `bge-model/` 时直接加载
