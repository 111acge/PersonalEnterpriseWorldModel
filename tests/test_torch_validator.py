"""torch 环境有效性验证测试。"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pewm.processors.torch_validator import validate_torch_environment


def test_validate_torch_environment_reports_basic_fields(temp_project):
    """验证报告应包含关键字段。"""
    report = validate_torch_environment()
    assert "torch_available" in report
    assert "tensor_op_ok" in report
    assert "cuda_available" in report
    assert "cpu_available" in report
    assert "sentence_transformers_available" in report
    assert "bge_model_files_ok" in report
    assert "errors" in report


def test_validate_torch_environment_detects_missing_bge_model(temp_project):
    """当 torch 可用且 bge 模型文件缺失时，报告应标记为不完整。"""
    try:
        import torch
    except ImportError:
        pytest.skip("当前环境未安装 torch，跳过 bge 文件完整性测试")

    from pewm.processors.torch_validator import _resource_path

    bge_path = _resource_path("bge-model")
    # 临时移走关键文件
    config_file = bge_path / "config.json"
    backup = None
    if config_file.exists():
        backup = config_file.read_text(encoding="utf-8")
        config_file.unlink()

    try:
        report = validate_torch_environment()
        assert report["bge_model_files_ok"] is False
        assert any("config.json" in err for err in report["errors"])
    finally:
        if backup is not None:
            config_file.write_text(backup, encoding="utf-8")


def test_validate_torch_environment_records_metric(temp_project):
    """验证结果应写入 metrics 表。"""
    from pewm.processors.metrics import get_recent, init_metrics_table

    init_metrics_table()
    validate_torch_environment()
    rows = get_recent(event="torch.validation", limit=10)
    assert len(rows) >= 1
    latest = rows[0]
    assert latest["event"] == "torch.validation"
    assert "success" in latest


def test_get_torch_status_returns_healthy_flag(temp_project):
    """get_torch_status 应返回 healthy 标志。"""
    from pewm.processors.torch_validator import get_torch_status

    status = get_torch_status()
    assert "healthy" in status
    assert isinstance(status["healthy"], bool)


def test_get_torch_status_caches_result(temp_project):
    """get_torch_status 应缓存验证结果，refresh=True 时强制重新验证。"""
    import pewm.processors.torch_validator as tv

    tv._status_cache.clear()
    with patch.object(tv, "validate_torch_environment", wraps=tv.validate_torch_environment) as spy:
        tv.get_torch_status()
        tv.get_torch_status()
        assert spy.call_count == 1
        tv.get_torch_status(refresh=True)
        assert spy.call_count == 2
