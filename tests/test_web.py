"""测试 Flask Web 层与启动控制器。"""
import time

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
    assert c.state.progress == 0


def test_splash_controller_go_home_binding(temp_project):
    c = SplashController()
    called = {"ok": False}

    def fake_go_home():
        called["ok"] = True

    c._do_go_home = fake_go_home
    result = c.go_home()
    assert result["success"] is True
    assert called["ok"] is True
