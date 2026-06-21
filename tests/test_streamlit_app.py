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


def test_tone_tag_button_inserts_at_front():
    """ブロックのトーンタグボタンを押すと、そのブロックの文頭にタグが入る。"""
    at = AppTest.from_file("streamlit_app.py")
    at.secrets["ELEVENLABS_API_KEY"] = "dummy"   # パスワード無し→本体UIに到達
    at.run()
    assert not at.exception
    btn = next(b for b in at.button if b.key == "tag_0_0")  # ブロック0の [excited]
    btn.click().run()
    assert at.session_state["txt_0"].startswith("[excited]")


def test_add_block_increases_blocks():
    """「ブロックを追加」で行が増える(キャラごとに足せる)。"""
    at = AppTest.from_file("streamlit_app.py")
    at.secrets["ELEVENLABS_API_KEY"] = "dummy"
    at.run()
    assert at.session_state["block_ids"] == [0]
    add = next(b for b in at.button if "ブロックを追加" in (b.label or ""))
    add.click().run()
    assert len(at.session_state["block_ids"]) == 2


def test_transcription_uses_cheaper_model(monkeypatch):
    """画像/Notion の文字起こしだけ安価モデル(Haiku)に切り替わる(APIは呼ばない)。

    TTS など他処理は元の settings.model のまま。共有キーは変えずモデルだけ差し替える。
    """
    import importlib

    import src.analyze as analyze_mod
    from src.config import Settings

    app = importlib.import_module("streamlit_app")

    captured: dict[str, str] = {}

    def fake_analyze(settings, inputs_dir, **kwargs):
        captured["model"] = settings.model
        raise RuntimeError("stop")  # 解析直前で止める(実API/応答処理に進ませない)

    monkeypatch.setattr(analyze_mod, "analyze", fake_analyze)
    settings = Settings(anthropic_api_key="x", elevenlabs_api_key="y", model="claude-opus-4-8")
    try:
        app._analyze_images(settings, [(".png", b"fake")])
    except RuntimeError:
        pass
    assert captured["model"] == app.TRANSCRIBE_MODEL == "claude-haiku-4-5"
    assert settings.model == "claude-opus-4-8"  # 元の設定は不変
