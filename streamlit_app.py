"""共有用 Web アプリ(Streamlit): テキスト → ElevenLabs(eleven_v3) → 整音 → 再生/DL。

友達とリンク共有して使う想定。設計:
- **共有キー方式**: ElevenLabs キーは st.secrets で保持し、画面には出さない。
- **パスワード保護**: APP_PASSWORD を知っている人だけ使える。
- **文字数上限**: MAX_CHARS で1回の生成量を制限し、課金の暴走を防ぐ。

デプロイ: Streamlit Community Cloud にこのリポジトリを連携し、Secrets に
ELEVENLABS_API_KEY / APP_PASSWORD（任意で MAX_CHARS, DEFAULT_VOICE_ID）を設定。
整音(ffmpeg)は packages.txt の ffmpeg で有効化される。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import streamlit as st

from src import audio_fx as fx_mod
from src import tts as tts_mod
from src import voices as voices_mod
from src.config import DEFAULT_OUTPUT_FORMAT, CharacterBook, Settings

st.set_page_config(page_title="ボイス生成（共有版）", page_icon="🎙️")


def _secret(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # noqa: BLE001 - secrets未設定でも動くように
        pass
    return os.getenv(name, default)


def _check_password() -> bool:
    expected = _secret("APP_PASSWORD")
    if not expected:
        return True  # 未設定ならゲートなし(ローカル開発用)
    if st.session_state.get("auth_ok"):
        return True
    st.title("🔒 ログイン")
    pw = st.text_input("パスワード", type="password")
    if pw:
        if pw == expected:
            st.session_state["auth_ok"] = True
            st.rerun()
        st.error("パスワードが違います。")
    return False


@st.cache_data(show_spinner=False)
def _account_voices(api_key: str) -> list[tuple[str, str]]:
    """(表示名, voice_id) の一覧。失敗したら空。"""
    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        out = []
        for v in voices_mod.fetch_account_voices(client):
            vid = voices_mod._voice_field(v, "voice_id", "voiceId")
            name = voices_mod._voice_field(v, "name") or vid
            if vid:
                out.append((f"{name}", vid))
        return out
    except Exception:  # noqa: BLE001
        return []


def main() -> None:
    if not _check_password():
        st.stop()

    api_key = _secret("ELEVENLABS_API_KEY")
    if not api_key:
        st.error("管理者へ: Secrets に ELEVENLABS_API_KEY を設定してください。")
        st.stop()
    settings = Settings(anthropic_api_key="", elevenlabs_api_key=api_key)
    max_chars = int(_secret("MAX_CHARS", "500") or 500)

    st.title("🎙️ ボイス生成（共有版）")
    st.caption(f"テキストを入れて生成。1回あたり最大 {max_chars} 文字。")

    text = st.text_area("読み上げるテキスト", height=150, max_chars=max_chars,
                        placeholder="やったー、ついにテスト終わったぞ！")

    # ボイス選択: ①characters.json のキャラ表(voice_id登録済み) → ②アカウント一覧 → ③手入力
    char_stability = "natural"
    char_seed = None
    chars = [(n, c) for n, c in CharacterBook.load().characters.items() if c.is_assigned()]
    if chars:
        labels = [f"{n}（{c.voice_name or c.voice_id[:8]}）" for n, c in chars]
        labels.append("― voice_id を直接入力 ―")
        sel = st.selectbox("キャラ / ボイス", range(len(labels)), format_func=lambda i: labels[i])
        if sel < len(chars):
            c = chars[sel][1]
            voice_id, char_stability, char_seed = c.voice_id, c.stability, c.seed
        else:
            voice_id = st.text_input("voice_id", value=_secret("DEFAULT_VOICE_ID"))
    else:
        voices = _account_voices(api_key)
        if voices:
            labels = [f"{n}（{vid[:8]}…）" for n, vid in voices]
            default_id = _secret("DEFAULT_VOICE_ID")
            idx = next((i for i, (_, vid) in enumerate(voices) if vid == default_id), 0)
            choice = st.selectbox("ボイス", range(len(voices)),
                                  format_func=lambda i: labels[i], index=idx)
            voice_id = voices[choice][1]
        else:
            voice_id = st.text_input("voice_id", value=_secret("DEFAULT_VOICE_ID"))

    c1, c2 = st.columns(2)
    preset = c1.selectbox("整音プリセット", list(fx_mod.PRESETS.keys()), index=0,
                          help="natural=クリーン / clean=正規化のみ / warm=温かみ")
    stab_opts = ["natural", "creative", "robust"]
    stability = c2.selectbox("声の安定度", stab_opts,
                             index=stab_opts.index(char_stability) if char_stability in stab_opts else 0)
    do_fx = st.checkbox("整音エフェクトをかける", value=True)

    if st.button("🔊 生成する", type="primary",
                 disabled=not (text and text.strip() and voice_id)):
        try:
            with st.spinner("生成中…（数秒）"):
                audio = tts_mod.synthesize_one(
                    settings, text.strip(), voice_id, stability, char_seed, DEFAULT_OUTPUT_FORMAT)
                final = _postprocess(audio, preset) if do_fx else audio
            st.success("できました！")
            st.audio(final, format="audio/mp3")
            st.download_button("⬇️ ダウンロード", final, file_name="voice.mp3", mime="audio/mp3")
        except Exception as e:  # noqa: BLE001
            st.error(f"生成に失敗しました: {e}")


def _postprocess(audio: bytes, preset: str) -> bytes:
    """一時ファイル経由で整音をかけて bytes を返す。ffmpeg 無しなら素のまま。"""
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "raw.mp3"
        dst = Path(d) / "out.mp3"
        src.write_bytes(audio)
        fx_mod.apply_fx(src, dst, preset=preset)
        return dst.read_bytes() if dst.exists() else audio


main()
