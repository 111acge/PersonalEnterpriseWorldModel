"""日志配置测试。"""
from pewm.processors.log_config import get_logger, setup_logging


def test_setup_logging_idempotent(temp_project):
    setup_logging()
    setup_logging()  # 不应重复添加 handler
    root = get_logger("")
    # 由于可能已有默认 handler，只检查我们自己的 handler 不超过一组
    handlers = [h for h in root.handlers if hasattr(h, "baseFilename")]
    assert len(handlers) <= 1


def test_get_logger_returns_logger(temp_project):
    logger = get_logger("test.module")
    assert logger.name == "test.module"
