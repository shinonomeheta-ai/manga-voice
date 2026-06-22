"""シナリオ生成アプリ(scenario_app.py)の起動スモークテスト。実APIは呼ばない。"""
import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def test_scenario_app_runs_and_shows_title():
    """secrets 未設定でも例外なく起動し、シナリオ生成画面が出る。"""
    at = AppTest.from_file("scenario_app.py").run()
    assert not at.exception
    titles = " ".join(t.value for t in at.title)
    assert "シナリオ生成" in titles


def test_scenario_app_password_gate():
    """APP_PASSWORD 設定時は未認証でログイン画面で止まる。"""
    at = AppTest.from_file("scenario_app.py")
    at.secrets["APP_PASSWORD"] = "secret"
    at.run()
    assert not at.exception
    titles = " ".join(t.value for t in at.title)
    assert "ログイン" in titles
