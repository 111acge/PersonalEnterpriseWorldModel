"""原生窗口控制 API 测试。"""
from pewm.web.splash_controller import SplashController


def test_minimize_window_api_exists():
    c = SplashController()
    assert hasattr(c, "minimize_window")
    result = c.minimize_window()
    assert result["success"] is True


def test_maximize_window_api_exists():
    c = SplashController()
    assert hasattr(c, "maximize_window")
    result = c.maximize_window()
    assert result["success"] is True


def test_close_window_api_exists():
    c = SplashController()
    assert hasattr(c, "close_window")
    result = c.close_window()
    assert result["success"] is True
