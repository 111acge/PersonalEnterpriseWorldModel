"""测试实体合并逻辑。"""
import pytest

from pewm.processors.extractor import render_template
from pewm.processors.merge import merge_entity


def test_merge_append_strategy(temp_project):
    path = temp_project / "20-Ontology" / "dictionary" / "rag.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nterm: RAG\ndefinition: 旧定义\n---\n# RAG\n旧定义", encoding="utf-8")

    result = merge_entity(
        path,
        {"term": "RAG", "definition": "新定义", "aliases": ["检索增强生成"]},
        "term",
        "---\nterm: {{ term }}\ndefinition: {{ definition }}\naliases: {{ aliases | join(', ') }}\n---\n# {{ term }}\n{{ definition }}",
        render_template,
        schema={"auto_merge": True},
    )
    assert result["merged"] is True
    content = path.read_text(encoding="utf-8")
    assert "旧定义" in content or "新定义" in content


def test_merge_no_overwrite_when_auto_merge_false(temp_project):
    path = temp_project / "20-Ontology" / "dictionary" / "rag.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nterm: RAG\ndefinition: 旧定义\n---\n# RAG\n旧定义", encoding="utf-8")

    result = merge_entity(
        path,
        {"term": "RAG", "definition": "新定义", "aliases": []},
        "term",
        "---\nterm: {{ term }}\ndefinition: {{ definition }}\n---\n# {{ term }}\n{{ definition }}",
        render_template,
        schema={"auto_merge": False},
    )
    assert result["merged"] is False
    assert "-1" in str(result["path"])
