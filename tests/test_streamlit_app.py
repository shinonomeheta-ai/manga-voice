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


def test_project_save_zip_load_roundtrip():
    """プロジェクト(台本＋設定＋音声)をZIP保存し、読み込みで復元できる。"""
    import importlib
    import zipfile

    app = importlib.import_module("streamlit_app")
    pairs = [("太郎", "[excited] やったー"), ("花子", "おめでとう")]
    settings = {"preset": "warm", "speed": 1.25, "do_fx": True,
                "char_tone": {"太郎": "[happy]"}}
    castmap = {"太郎": {"voice_id": "vid_taro", "stability": "natural"}}
    proj = app._build_project("第1話テスト", pairs, settings, castmap)
    assert proj["name"] == "第1話テスト"
    assert proj["blocks"][0] == {"speaker": "太郎", "text": "[excited] やったー"}
    assert proj["characters"]["太郎"]["voice_id"] == "vid_taro"

    blob = app._project_zip(proj, [("audio_all.mp3", b"ID3audio"),
                                   ("01_taro_natural.mp3", b"clip")])
    # ZIPの中身が正しいこと
    with zipfile.ZipFile(__import__("io").BytesIO(blob)) as z:
        assert "project.json" in z.namelist()
        assert z.read("audio_all.mp3") == b"ID3audio"

    # ZIPから読み戻すと元のプロジェクトdictに戻る
    restored = app._parse_project("第1話テスト.zip", blob)
    assert restored["blocks"] == proj["blocks"]
    assert restored["settings"]["speed"] == 1.25
    assert restored["characters"]["太郎"]["voice_id"] == "vid_taro"  # キャストも復元

    # 設定 -> ウィジェットキーへの変換(感情タグはラベルに戻る)
    state = app._settings_to_state(restored["settings"])
    assert state["preset"] == "warm"
    assert state["speed"] == 1.25
    assert state["chartone_sel_太郎"] == "うれしい"  # [happy] のラベル


def test_merge_cast_prefers_current_voices():
    """プロジェクトを開いても、現在(既定)のボイス割り当てが優先される。"""
    import importlib

    app = importlib.import_module("streamlit_app")
    project = {"あかり": {"voice_id": "OLD", "stability": "natural"},
               "脇役": {"voice_id": "vid_sub", "stability": "natural"}}
    current = {"あかり": {"voice_id": "NEW", "stability": "creative"}}
    merged = app._merge_cast(project, current)
    assert merged["あかり"]["voice_id"] == "NEW"      # 既定が勝つ
    assert merged["脇役"]["voice_id"] == "vid_sub"     # projectのみのキャラは取り込む


def test_match_speaker_tolerates_honorifics():
    """キャラシートの「あかりちゃん」をキャスト「あかり」にゆるく一致させる。"""
    import importlib

    app = importlib.import_module("streamlit_app")
    names = ["あかり", "ななし", "桐原"]
    assert app._match_speaker("あかりちゃん", names) == "あかり"
    assert app._match_speaker("ななし", names) == "ななし"
    assert app._match_speaker("桐原さん", names) == "桐原"
    # 全く未知の名前は先頭にフォールバック(selectboxの選択肢に収めるため)
    assert app._match_speaker("謎の人物", names) == "あかり"


def test_analyze_images_batches_and_merges(monkeypatch):
    """画像が多いと batch 枚ずつに分割して解析し、結果を1本に結合する(413回避)。"""
    import importlib
    from pathlib import Path

    import src.analyze as analyze_mod
    from src.config import Settings
    from src.models import Line, Scene, Script

    app = importlib.import_module("streamlit_app")
    calls = {"n": 0}

    def fake_analyze(settings, inputs_dir, **kwargs):
        calls["n"] += 1
        n_imgs = len(list(Path(inputs_dir).glob("page_*")))
        assert n_imgs <= 6  # 1バッチは batch 枚以下
        return Script(scenes=[Scene(id=f"s{calls['n']}",
                                    lines=[Line(speaker="X", text=f"b{calls['n']}")])])

    monkeypatch.setattr(analyze_mod, "analyze", fake_analyze)
    settings = Settings(anthropic_api_key="x", elevenlabs_api_key="y")
    items = [(".png", b"img-%d" % i) for i in range(13)]
    script = app._analyze_images(settings, items, batch=6)
    assert calls["n"] == 3            # 13枚 → 6+6+1 = 3バッチ
    assert len(script.scenes) == 3    # 分割結果が結合されている
