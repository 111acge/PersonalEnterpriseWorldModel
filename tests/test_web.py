"""测试 Flask Web 层与启动控制器。"""
import time
from unittest.mock import patch

from pewm.web.app import create_app
from pewm.web.splash_controller import SplashController


def test_flask_404_returns_error_page(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/nonexistent-page")
    assert resp.status_code == 404
    html = resp.data.decode("utf-8")
    assert "404" in html
    assert "返回首页" in html
    assert "关闭应用" in html


def test_flask_error_route_exists(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/error")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "页面未找到" in html


def test_flask_stats_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "data" in data


def test_flask_search_route(temp_project):
    app = create_app()
    client = app.test_client()
    # 空关键词返回 400
    resp = client.get("/api/search")
    assert resp.status_code == 400


def test_flask_config_llm_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/config/llm")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "data" in data


def test_flask_config_ocr_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/config/ocr")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "data" in data


def test_flask_chat_stream_without_api_key(temp_project):
    """未配置 API Key 时，流式接口应返回有限数据。"""
    app = create_app()
    client = app.test_client()
    with patch("pewm.web.app.load_config", return_value={"api_key": ""}):
        resp = client.post(
            "/api/chat/stream",
            json={"q": "hello", "session_id": "test"},
        )
        assert resp.status_code == 200


def test_flask_torch_status_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/torch/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True


def test_flask_metrics_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/metrics")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True


def test_flask_metrics_summary_requires_event(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/metrics/summary")
    assert resp.status_code == 400


def test_flask_greeting_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/greeting")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True


def test_flask_documents_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/documents")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert isinstance(data["data"], list)


def test_flask_inbox_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.post("/api/inbox", json={"title": "t", "content": "c"})
    assert resp.status_code == 200


def test_flask_inbox_empty_rejected(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.post("/api/inbox", json={"title": "", "content": ""})
    assert resp.status_code == 400


def test_flask_pipeline_status_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/pipeline/status")
    assert resp.status_code == 200


def test_flask_pipeline_run_returns_running(temp_project):
    """管线启动端点应返回 200，但不真实启动后台线程（避免并发访问测试数据库）。"""
    app = create_app()
    client = app.test_client()
    with patch("pewm.web.app.threading.Thread") as MockThread:
        MockThread.return_value.start = lambda: None
        resp = client.post("/api/pipeline/run", json={"options": ["--no-git", "--no-ocr"]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True


def test_flask_providers_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/config/providers")
    assert resp.status_code == 200


def test_flask_ocr_providers_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/config/ocr/providers")
    assert resp.status_code == 200


def test_flask_prompt_fields_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/config/prompt/fields")
    assert resp.status_code == 200


def test_flask_chat_history_route(temp_project):
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/chat/history")
    assert resp.status_code == 200


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
