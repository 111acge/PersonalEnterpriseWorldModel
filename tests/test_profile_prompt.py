"""用户画像与提示词配置测试。"""
from pewm.processors.prompt_config import load_prompt, save_prompt, PROMPT_FIELDS
from pewm.processors.user_profile import load_profile, save_profile


def test_user_profile_roundtrip(temp_project):
    save_profile({"personal_name": "张三", "company_name": "未来有限公司"})
    profile = load_profile()
    assert profile["personal_name"] == "张三"
    assert profile["company_name"] == "未来有限公司"


def test_user_profile_default_values(temp_project):
    profile = load_profile()
    assert "personal_name" in profile
    assert "company_name" in profile


def test_prompt_config_roundtrip(temp_project):
    prompt = {field["key"]: f"prompt for {field['key']}" for field in PROMPT_FIELDS}
    save_prompt(prompt)
    loaded = load_prompt()
    for key in prompt:
        assert loaded[key] == prompt[key]


def test_prompt_config_default_values(temp_project):
    loaded = load_prompt()
    for field in PROMPT_FIELDS:
        assert field["key"] in loaded
