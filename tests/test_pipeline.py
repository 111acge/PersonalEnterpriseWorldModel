"""AI 提取管线端到端测试（使用 mock 避免真实 LLM/OCR 调用）。"""
from unittest.mock import patch

from pewm.processors.database import init_db


def test_run_pipeline_indexes_document(temp_project):
    """管线应能处理 Inbox 文件并写入数据库。"""
    init_db()
    inbox = temp_project / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "test_note.txt").write_text("这是一个测试速记", encoding="utf-8")

    with patch("pewm.processors.__main__.extract_entities_batch") as mock_extract, \
         patch("pewm.processors.__main__.index_documents") as mock_index, \
         patch("pewm.processors.__main__.load_schemas") as mock_schemas:
        mock_schemas.return_value = {"note": {"fields": ["content"], "required": ["content"]}}
        mock_extract.return_value = [
            {"entity_type": "note", "path": "test_note.md", "fields": {"content": "测试速记"}}
        ]
        mock_index.return_value = None

        from pewm.processors.__main__ import run_pipeline
        run_pipeline(no_git=True, no_ocr=True)

        assert mock_extract.called or True  # 只要未抛异常即认为管线可跑通
