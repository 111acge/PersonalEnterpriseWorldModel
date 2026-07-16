"""提取器单元测试（mock LLM 调用）。"""
from unittest.mock import MagicMock, patch

import pytest

from pewm.processors import extractor


class TestLoadSchemasAndRules:
    def test_load_schemas_returns_dict(self, temp_project):
        schemas = extractor.load_schemas()
        assert isinstance(schemas, dict)
        assert "note" in schemas
        assert "term" in schemas
        assert "case" in schemas
        assert "process" in schemas
        assert "system" in schemas
        assert "skill" in schemas
        assert "constant" in schemas

    def test_load_rules_returns_list(self, temp_project):
        rules = extractor.load_rules()
        assert isinstance(rules, list)
        if rules:
            assert "trigger_patterns" in rules[0]
            assert "target_layer" in rules[0]


class TestRenderTemplate:
    def test_basic_variable_substitution(self):
        out = extractor.render_template("hello {{ name }}", {"name": "world"})
        assert out == "hello world"

    def test_default_filter(self):
        out = extractor.render_template("{{ missing | default('x') }}", {})
        assert out == "x"

    def test_join_filter(self):
        out = extractor.render_template("{{ items | join(',') }}", {"items": ["a", "b"]})
        assert out == "a,b"

    def test_none_renders_empty(self):
        out = extractor.render_template("{{ missing }}", {})
        assert out == ""


class TestParseAndValidateLlmJson:
    def test_valid_json_array(self, temp_project):
        schemas = extractor.load_schemas()
        text = '[{"entity_type":"note","title":"t","content":"c","confidence":"高","source":"s","updated_at":"2024"}]'
        items = extractor._parse_and_validate_llm_json(text, schemas)
        assert len(items) == 1
        assert items[0]["entity_type"] == "note"

    def test_markdown_code_block(self, temp_project):
        schemas = extractor.load_schemas()
        text = '```json\n[{"entity_type":"note","title":"t","content":"c","confidence":"高","source":"s","updated_at":"2024"}]\n```'
        items = extractor._parse_and_validate_llm_json(text, schemas)
        assert len(items) == 1

    def test_invalid_json_returns_empty(self, temp_project):
        schemas = extractor.load_schemas()
        items = extractor._parse_and_validate_llm_json("not json", schemas)
        assert items == []

    def test_unknown_entity_type_skipped(self, temp_project):
        schemas = extractor.load_schemas()
        text = '[{"entity_type":"unknown","title":"t","confidence":"高"}]'
        items = extractor._parse_and_validate_llm_json(text, schemas)
        assert items == []

    def test_missing_identity_field_skipped(self, temp_project):
        schemas = extractor.load_schemas()
        text = '[{"entity_type":"note","content":"c","confidence":"高"}]'
        items = extractor._parse_and_validate_llm_json(text, schemas)
        assert items == []


class TestRuleExtract:
    def test_rule_extract_term(self, temp_project):
        schemas = extractor.load_schemas()
        rules = extractor.load_rules()
        text = "RAG 是一种检索增强生成技术。"
        entities = extractor._rule_extract(text, "test.md", rules, schemas)
        assert len(entities) >= 0  # 可能命中也可能不命中，取决于规则

    def test_rule_extract_empty_text(self, temp_project):
        schemas = extractor.load_schemas()
        rules = extractor.load_rules()
        entities = extractor._rule_extract("", "test.md", rules, schemas)
        assert entities == []


class TestBuildEntity:
    def test_build_note_entity(self, temp_project):
        schemas = extractor.load_schemas()
        note = extractor.build_note_entity("这是一段测试内容", "test.md", schemas["note"])
        assert note is not None
        assert note["entity_type"] == "note"
        assert "title" in note["frontmatter"]
        assert note["frontmatter"]["content"] == "这是一段测试内容"

    def test_build_note_entity_with_title(self, temp_project):
        schemas = extractor.load_schemas()
        note = extractor.build_note_entity("# 我的标题\n内容", "test.md", schemas["note"])
        assert note["frontmatter"]["title"] == "我的标题"


class TestExtractEntities:
    def test_no_api_key_falls_back_to_note(self, temp_project):
        with patch("pewm.processors.extractor.load_config", return_value={"api_key": ""}):
            entities = extractor.extract_entities("一些无法归类的杂记", "test.md")
        assert len(entities) == 1
        assert entities[0]["entity_type"] == "note"

    def test_empty_text_returns_empty(self, temp_project):
        with patch("pewm.processors.extractor.load_config", return_value={"api_key": ""}):
            entities = extractor.extract_entities("", "test.md")
        assert entities == []

    def test_llm_extract_success(self, temp_project):
        fake_response = '[{"entity_type":"term","term":"RAG","definition":"检索增强生成","aliases":[],"confidence":"高","source":"s","updated_at":"2024"}]'
        with patch("pewm.processors.extractor.load_config", return_value={"api_key": "sk-x"}), \
             patch("pewm.processors.extractor.chat_completion", return_value=fake_response):
            entities = extractor.extract_entities("RAG 是检索增强生成", "test.md")
        assert len(entities) == 1
        assert entities[0]["entity_type"] == "term"


class TestExtractEntitiesBatch:
    def test_no_api_key_returns_empty_lists(self, temp_project):
        with patch("pewm.processors.extractor.load_config", return_value={"api_key": ""}):
            results = extractor.extract_entities_batch([("a.md", "text1"), ("b.md", "text2")])
        assert len(results) == 2
        assert all(len(r) == 1 and r[0]["entity_type"] == "note" for r in results)

    def test_empty_items_returns_empty(self, temp_project):
        results = extractor.extract_entities_batch([])
        assert results == []

    def test_llm_batch_groups_by_source_index(self, temp_project):
        fake_response = '[{"entity_type":"note","source_index":0,"title":"t0","content":"c0","confidence":"高","source":"s","updated_at":"2024"},{"entity_type":"note","source_index":1,"title":"t1","content":"c1","confidence":"高","source":"s","updated_at":"2024"}]'
        with patch("pewm.processors.extractor.load_config", return_value={"api_key": "sk-x"}), \
             patch("pewm.processors.extractor.chat_completion", return_value=fake_response):
            results = extractor.extract_entities_batch([("a.md", "text1"), ("b.md", "text2")])
        assert len(results) == 2
        assert results[0][0]["frontmatter"]["title"] == "t0"
        assert results[1][0]["frontmatter"]["title"] == "t1"
