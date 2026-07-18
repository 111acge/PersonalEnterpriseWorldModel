"""测试 Flask Web 层与启动控制器。"""
import time
from unittest.mock import patch

import pytest

from pewm.web.app import create_app
from pewm.web.splash_controller import SplashController


@pytest.fixture(scope="function")
def client(temp_project):
    """带有效 X-Token 的测试客户端（模拟已注入令牌的前端）。"""
    app = create_app()
    c = app.test_client()
    # 为所有请求默认携带访问令牌
    c.environ_base["HTTP_X_TOKEN"] = app.config["API_TOKEN"]
    return c


def test_flask_404_returns_error_page(client):
    resp = client.get("/nonexistent-page")
    assert resp.status_code == 404
    html = resp.data.decode("utf-8")
    assert "404" in html
    assert "返回首页" in html
    assert "关闭应用" in html


def test_flask_error_route_exists(client):
    resp = client.get("/error")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "页面未找到" in html


def test_flask_stats_route(client):
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "data" in data


def test_flask_search_route(client):
    # 空关键词返回 400
    resp = client.get("/api/search")
    assert resp.status_code == 400


def test_flask_config_llm_route(client):
    resp = client.get("/api/config/llm")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "data" in data


def test_flask_config_ocr_route(client):
    resp = client.get("/api/config/ocr")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "data" in data


def test_flask_chat_stream_without_api_key(client):
    """未配置 API Key 时，流式接口应返回有限数据。"""
    with patch("pewm.web.app.load_config", return_value={"api_key": ""}):
        resp = client.post(
            "/api/chat/stream",
            json={"q": "hello", "session_id": "test"},
        )
        assert resp.status_code == 200


def test_flask_torch_status_route(client):
    resp = client.get("/api/torch/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True


def test_flask_metrics_route(client):
    resp = client.get("/api/metrics")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True


def test_flask_metrics_summary_requires_event(client):
    resp = client.get("/api/metrics/summary")
    assert resp.status_code == 400


def test_flask_greeting_route(client):
    resp = client.get("/api/greeting")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True


def test_flask_documents_route(client):
    resp = client.get("/api/documents")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert isinstance(data["data"], list)


def test_flask_inbox_route(client):
    resp = client.post("/api/inbox", json={"title": "t", "content": "c"})
    assert resp.status_code == 200


def test_flask_inbox_empty_rejected(client):
    resp = client.post("/api/inbox", json={"title": "", "content": ""})
    assert resp.status_code == 400


def test_flask_pipeline_status_route(client):
    resp = client.get("/api/pipeline/status")
    assert resp.status_code == 200


def test_flask_pipeline_run_returns_running(client):
    """管线启动端点应返回 200，但不真实启动后台线程（避免并发访问测试数据库）。"""
    with patch("pewm.web.app.threading.Thread") as MockThread:
        MockThread.return_value.start = lambda: None
        resp = client.post("/api/pipeline/run", json={"options": ["--no-git", "--no-ocr"]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True


def test_flask_providers_route(client):
    resp = client.get("/api/config/providers")
    assert resp.status_code == 200


def test_flask_ocr_providers_route(client):
    resp = client.get("/api/config/ocr/providers")
    assert resp.status_code == 200


def test_flask_prompt_fields_route(client):
    resp = client.get("/api/config/prompt/fields")
    assert resp.status_code == 200


def test_flask_chat_history_route(client):
    resp = client.get("/api/chat/history")
    assert resp.status_code == 200


# ========== 安全修复回归（#15 #16 #17 #19 #63） ==========

def test_api_requires_token(temp_project):
    """未携带 X-Token 的 /api/* 请求应返回 401。"""
    app = create_app()
    c = app.test_client()
    resp = c.get("/api/stats")
    assert resp.status_code == 401
    resp = c.get("/api/config/llm")
    assert resp.status_code == 401


def test_api_wrong_token_rejected(temp_project):
    app = create_app()
    c = app.test_client()
    resp = c.get("/api/stats", headers={"X-Token": "wrong-token"})
    assert resp.status_code == 401


def test_auth_token_endpoint_exempt(temp_project):
    """令牌发放接口本身不需要令牌，且返回的令牌可用于后续请求。"""
    app = create_app()
    c = app.test_client()
    resp = c.get("/api/auth/token")
    assert resp.status_code == 200
    token = resp.get_json()["data"]["token"]
    resp2 = c.get("/api/stats", headers={"X-Token": token})
    assert resp2.status_code == 200


def test_non_localhost_host_rejected(temp_project):
    """Host 头非本机时一律 403。"""
    app = create_app()
    c = app.test_client()
    resp = c.get("/api/stats", headers={
        "Host": "evil.example.com",
        "X-Token": app.config["API_TOKEN"],
    })
    assert resp.status_code == 403


def test_non_json_post_rejected(client):
    """非 GET 的 /api/* 必须携带 JSON 请求体（防 HTML 表单 CSRF）。"""
    resp = client.post(
        "/api/documents/purge",
        data="x=1",
        content_type="application/x-www-form-urlencoded",
    )
    assert resp.status_code == 415


def test_llm_config_masks_api_key(client):
    """GET /api/config/llm 不返回明文 api_key。"""
    client.post("/api/config/llm", json={
        "provider": "deepseek",
        "api_key": "sk-1234567890abcdef",
        "base_url": "",
        "model": "",
    })
    resp = client.get("/api/config/llm")
    data = resp.get_json()["data"]
    assert data["api_key"] != "sk-1234567890abcdef"
    assert data["api_key"].startswith("***")
    assert data["api_key"].endswith("cdef")


def test_llm_save_masked_key_keeps_original(client):
    """保存时收到打码值/空值不覆盖原 key。"""
    client.post("/api/config/llm", json={
        "provider": "deepseek",
        "api_key": "sk-real-secret-key-9999",
        "base_url": "",
        "model": "m1",
    })
    # 打码值保存 → 保留原 key
    client.post("/api/config/llm", json={
        "provider": "deepseek",
        "api_key": "***9999",
        "base_url": "",
        "model": "m2",
    })
    from pewm.processors.llm_client import load_config
    cfg = load_config()
    assert cfg["api_key"] == "sk-real-secret-key-9999"
    assert cfg["model"] == "m2"
    # 空值保存 → 同样保留原 key
    client.post("/api/config/llm", json={
        "provider": "deepseek",
        "api_key": "",
        "base_url": "",
        "model": "m3",
    })
    cfg = load_config()
    assert cfg["api_key"] == "sk-real-secret-key-9999"


def test_pipeline_run_conflict_returns_409(client):
    """管线互斥锁被占用时返回 409。"""
    with patch("pewm.web.app.threading.Thread") as MockThread:
        MockThread.return_value.start = lambda: None  # 任务不执行 → 锁不释放
        first = client.post("/api/pipeline/run", json={"options": []})
        second = client.post("/api/pipeline/run", json={"options": []})
    assert first.status_code == 200
    assert second.status_code == 409


def test_config_export_rejects_path_outside_home(client):
    """导出路径必须在用户目录下。"""
    from pathlib import Path

    outside = str(Path(Path.home().anchor))  # 系统盘根目录，必在用户目录之外
    resp = client.post("/api/config/export", json={"path": outside})
    assert resp.status_code == 400
    resp = client.post("/api/config/export", json={"path": "../../etc/evil.json"})
    assert resp.status_code == 400


def test_config_export_defaults_no_api_keys(client, tmp_path):
    """导出默认不包含 API Key。"""
    import json as _json
    from pathlib import Path

    client.post("/api/config/llm", json={
        "provider": "deepseek",
        "api_key": "sk-should-not-export-0000",
        "base_url": "",
        "model": "",
    })
    dest = Path.home() / f"pewm-test-export-{int(time.time() * 1000)}.json"
    try:
        resp = client.post("/api/config/export", json={"path": str(dest)})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        payload = _json.loads(dest.read_text(encoding="utf-8"))
        assert "api_key" not in payload.get("llm", {})
    finally:
        dest.unlink(missing_ok=True)


def test_ocr_save_rejects_invalid_mode(client):
    resp = client.post("/api/config/ocr", json={"mode": "evil", "provider": "baidu"})
    assert resp.status_code == 400


def test_ocr_save_rejects_invalid_provider(client):
    resp = client.post("/api/config/ocr", json={"mode": "local", "provider": "evil"})
    assert resp.status_code == 400


def test_splash_controller_timeout_under_weak_network(temp_project):
    """模拟弱网：初始化长时间不完成，应触发超时错误。"""
    c = SplashController(timeout=0.5)
    c.state.phase = "loading"
    c.state.start_time = time.time() - 1.0  # 假装已过去 1 秒
    c.state.progress = 30
    data = c.get_progress()
    assert data["phase"] == "error"
    assert "超时" in data["error"]
    assert data["canRetry"] is True


def test_splash_controller_retry_resets_state(temp_project):
    c = SplashController(timeout=5.0)
    c.state.phase = "error"
    c.state.error = "超时"
    c.retry()
    assert c.state.phase == "loading"
    assert c.state.error == ""
    # retry() 会立即在后台启动初始化线程，进度可能已被更新为 5
    assert c.state.progress >= 0


def test_splash_controller_go_home_binding(temp_project):
    c = SplashController()
    called = {"ok": False}

    def fake_go_home():
        called["ok"] = True

    c._do_go_home = fake_go_home
    result = c.go_home()
    assert result["success"] is True
    assert called["ok"] is True


def test_splash_controller_minimize_maximize_close_bindings(temp_project):
    c = SplashController()
    c._window = None
    assert c.minimize_window()["success"] is True
    assert c.maximize_window()["success"] is True
    assert c.close_window()["success"] is True


def test_splash_controller_get_progress_format(temp_project):
    c = SplashController()
    data = c.get_progress()
    assert "progress" in data
    assert "status" in data
    assert "phase" in data
    assert "error" in data
    assert "canRetry" in data
    assert "canExit" in data
    assert "version" in data
    assert "elapsed" in data
