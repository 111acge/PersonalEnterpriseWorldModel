"""torch 环境有效性验证。

按项目约束，torch 框架及 bge 预训练模型必须完整保留、可正常加载。
本模块在启动期与 CI 中自动验证：
- torch 是否可导入；
- 张量创建与基础运算是否正常；
- CUDA/CPU 加速后端是否可用；
- sentence-transformers 是否可导入；
- 本地 bge-model/ 目录是否完整。

验证结果通过 `record()` 写入指标表，并在启动失败时提供排查信息。
"""
import sys
from pathlib import Path
from typing import Any, Dict

from pewm.paths import ROOT
from pewm.processors.metrics import record


def _resource_path(*parts: str) -> Path:
    """返回资源文件的绝对路径。兼容源码模式与 PyInstaller 单文件模式。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS).joinpath(*parts)
    return ROOT.joinpath(*parts)


def validate_torch_environment() -> Dict[str, Any]:
    """验证 torch 环境有效性，返回验证报告字典。

    该函数设计为即使部分验证失败也不抛出异常，而是将结果写入指标表，
    由调用方决定是否阻塞启动。
    """
    report: Dict[str, Any] = {
        "torch_available": False,
        "torch_version": None,
        "tensor_op_ok": False,
        "cuda_available": False,
        "cpu_available": False,
        "sentence_transformers_available": False,
        "bge_model_files_ok": False,
        "bge_model_path": str(_resource_path("bge-model")),
        "errors": [],
    }

    # 1. torch 可导入性
    try:
        import torch
        report["torch_available"] = True
        report["torch_version"] = torch.__version__
    except Exception as e:
        report["errors"].append(f"torch 导入失败：{e}")
        _write_report(report)
        return report

    # 2. 张量基础运算
    try:
        import torch
        import torch.nn.functional as F

        a = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
        b = torch.tensor([[1.0, 0.0], [0.0, 1.0]])
        c = torch.matmul(a, b)
        assert c.shape == (2, 2)
        # 验证 softmax 等常用算子
        d = F.softmax(a, dim=-1)
        assert d.shape == (2, 2)
        report["tensor_op_ok"] = True
    except Exception as e:
        report["errors"].append(f"张量运算失败：{e}")

    # 3. CUDA / CPU 后端
    try:
        import torch

        if torch.cuda.is_available():
            report["cuda_available"] = True
            # 尝试在 CUDA 上创建张量
            try:
                x = torch.tensor([1.0, 2.0]).cuda()
                _ = x + 1
            except Exception as e:
                report["errors"].append(f"CUDA 张量运算失败：{e}")
                report["cuda_available"] = False
        # CPU 始终应可用
        x = torch.tensor([1.0, 2.0])
        _ = x + 1
        report["cpu_available"] = True
    except Exception as e:
        report["errors"].append(f"CPU 后端不可用：{e}")

    # 4. sentence-transformers 可导入性
    try:
        import sentence_transformers
        report["sentence_transformers_available"] = True
    except Exception as e:
        report["errors"].append(f"sentence_transformers 导入失败：{e}")

    # 5. bge 模型文件完整性
    try:
        bge_path = _resource_path("bge-model")
        required_files = ["config.json", "pytorch_model.bin", "tokenizer.json", "vocab.txt"]
        missing = [f for f in required_files if not (bge_path / f).exists()]
        if missing:
            report["errors"].append(f"bge 模型缺少文件：{missing}")
        else:
            report["bge_model_files_ok"] = True
    except Exception as e:
        report["errors"].append(f"bge 模型文件检查失败：{e}")

    _write_report(report)
    return report


def _write_report(report: Dict[str, Any]) -> None:
    """将验证结果写入指标表。"""
    success = (
        report["torch_available"]
        and report["tensor_op_ok"]
        and (report["cuda_available"] or report["cpu_available"])
        and report["bge_model_files_ok"]
    )
    record(
        "torch.validation",
        success=success,
        error_msg="; ".join(report["errors"]) if report["errors"] else "",
        meta=report,
    )


def get_torch_status() -> Dict[str, Any]:
    """获取当前 torch 环境状态（用于设置页展示）。"""
    report = validate_torch_environment()
    return {
        "torch_version": report.get("torch_version"),
        "backend": "CUDA" if report.get("cuda_available") else "CPU",
        "cpu_available": report.get("cpu_available"),
        "cuda_available": report.get("cuda_available"),
        "sentence_transformers_available": report.get("sentence_transformers_available"),
        "bge_model_files_ok": report.get("bge_model_files_ok"),
        "bge_model_path": report.get("bge_model_path"),
        "healthy": (
            report.get("torch_available", False)
            and report.get("tensor_op_ok", False)
            and (report.get("cuda_available", False) or report.get("cpu_available", False))
            and report.get("bge_model_files_ok", False)
        ),
        "errors": report.get("errors", []),
    }


if __name__ == "__main__":
    import json

    status = get_torch_status()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    sys.exit(0 if status["healthy"] else 1)
