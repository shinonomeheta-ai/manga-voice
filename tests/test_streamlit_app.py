"""共有Webアプリ(streamlit_app.py)の起動スモークテスト。

ブラウザ無しでアプリを実行し、例外を出さず正しく分岐するかを確認する。
実APIは呼ばない(キー未設定/未認証パスのみ検証)。
"""
import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def test_app_runs_without_secrets_and_warns_missing_key():
    """secrets 未設定: 例外なく起動し、キー未設定の案内を出して止まる。"""
    at = AppTest.from_file("streamlit_app.py").run()
    assert not at.exception
    msgs = " ".join(e.value for e in at.error)
    assert "ELEVENLABS_API_KEY" in msgs


def test_password_gate_blocks_without_auth():
    """APP_PASSWORD 設定時は未認証だとログイン画面で止まる(キー入力に到達しない)。"""
    at = AppTest.from_file("streamlit_app.py")
    at.secrets["APP_PASSWORD"] = "secret"
    at.secrets["ELEVENLABS_API_KEY"] = "dummy"
    at.run()
    assert not at.exception
    titles = " ".join(t.value for t in at.title)
    assert "ログイン" in titles
