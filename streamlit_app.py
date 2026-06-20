"""共有用 Web アプリ(Streamlit): キャラごとにセリフブロックを足して物語を作り、
eleven_v3 で音声化 → 整音 → 再生/DL。

設計:
- **共有キー方式**: ElevenLabs キーは st.secrets で保持し、画面には出さない。
- **パスワード保護**: APP_PASSWORD を知っている人だけ使える。
- **文字数上限**: MAX_CHARS で1回の合計生成量を制限し、課金の暴走を防ぐ。
- **モデルは eleven_v3 固定**(読み上げは全部 v3)。複数ブロックは Text-to-Dialogue で
  掛け合いとして一括生成、1ブロックは Text-to-Speech。

デプロイ: Streamlit Community Cloud にこのリポジトリを連携し、Secrets に
ELEVENLABS_API_KEY / APP_PASSWORD（任意で MAX_CHARS）を設定。整音(ffmpeg)は
packages.txt の ffmpeg で有効化される。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import streamlit as st

from src import audio_fx as fx_mod
from src import tts as tts_mod
from src.config import DEFAULT_OUTPUT_FORMAT, CharacterBook, Settings

st.set_page_config(page_title="ボイス生成（共有版）", page_icon="🎙️")

# ワンクリック挿入できる eleven_v3 の感情/演出タグ
TAG_CHOICES = [
    "[laughs]", "[sigh]", "[whispers]", "[excited]",
    "[nervous]", "[gasps]", "[hesitates]", "[pause]",
]


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


def _append_tag_block(block_id: int, tag: str) -> None:
    """指定ブロックのテキスト末尾にタグを足す(ボタンの on_click から呼ぶ)。"""
    key = f"txt_{block_id}"
    cur = st.session_state.get(key, "")
    sep = "" if (not cur or cur[-1:] in " \n") else " "
    st.session_state[key] = f"{cur}{sep}{tag} "


def _postprocess(audio: bytes, preset: str) -> bytes:
    """一時ファイル経由で整音をかけて bytes を返す。ffmpeg 無しなら素のまま。"""
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "raw.mp3"
        dst = Path(d) / "out.mp3"
        src.write_bytes(audio)
        fx_mod.apply_fx(src, dst, preset=preset)
        return dst.read_bytes() if dst.exists() else audio


def main() -> None:
    if not _check_password():
        st.stop()

    api_key = _secret("ELEVENLABS_API_KEY")
    if not api_key:
        st.error("管理者へ: Secrets に ELEVENLABS_API_KEY を設定してください。")
        st.stop()
    settings = Settings(anthropic_api_key="", elevenlabs_api_key=api_key)
    max_chars = int(_secret("MAX_CHARS", "800") or 800)

    book = CharacterBook.load()
    chars = {n: c for n, c in book.characters.items() if c.is_assigned()}
    char_names = list(chars.keys())

    st.title("🎙️ ボイス生成（共有版）")
    st.caption(f"キャラごとにセリフを足して物語を作れます。モデル: **eleven_v3 固定** / "
               f"合計最大 {max_chars} 文字。タグ例: [laughs] [sigh] [whispers]")

    # --- ブロック(セリフ)の状態 ---
    if "block_ids" not in st.session_state:
        st.session_state.block_ids = [0]
        st.session_state.block_seq = 1

    remove_id = None
    for i, bid in enumerate(st.session_state.block_ids):
        with st.container(border=True):
            c1, c2, c3 = st.columns([2, 6, 1])
            if char_names:
                c1.selectbox("キャラ", char_names, key=f"spk_{bid}")
            else:
                c1.text_input("voice_id", key=f"spk_{bid}")
            c2.text_area(f"セリフ {i + 1}", key=f"txt_{bid}", height=80,
                         placeholder="例: いやー、マジで助かったよ…")
            with c2.popover("＋ 感情タグ"):
                tcols = st.columns(4)
                for j, t in enumerate(TAG_CHOICES):
                    tcols[j % 4].button(t, key=f"tag_{bid}_{j}", use_container_width=True,
                                        on_click=_append_tag_block, args=(bid, t))
            if c3.button("🗑", key=f"del_{bid}", help="このブロックを削除"):
                remove_id = bid

    if remove_id is not None and len(st.session_state.block_ids) > 1:
        st.session_state.block_ids = [b for b in st.session_state.block_ids if b != remove_id]
        st.rerun()

    if st.button("＋ ブロックを追加"):
        st.session_state.block_ids.append(st.session_state.block_seq)
        st.session_state.block_seq += 1
        st.rerun()

    o1, o2 = st.columns(2)
    preset = o1.selectbox("整音プリセット", list(fx_mod.PRESETS.keys()), index=0,
                          help="natural=クリーン / clean=正規化のみ / warm=温かみ")
    do_fx = o2.checkbox("整音エフェクトをかける", value=True)

    # --- ブロックを収集 ---
    lines: list[dict[str, str]] = []
    stabs: list[str] = []
    total = 0
    for bid in st.session_state.block_ids:
        spk = (st.session_state.get(f"spk_{bid}", "") or "").strip()
        txt = (st.session_state.get(f"txt_{bid}", "") or "").strip()
        if not txt:
            continue
        voice_id = chars[spk].voice_id if spk in chars else spk
        if not voice_id:
            continue
        lines.append({"text": txt, "voice_id": voice_id})
        stabs.append(chars[spk].stability if spk in chars else "natural")
        total += len(txt)

    st.caption(f"合計 {total} / {max_chars} 文字")
    over = total > max_chars

    if over:
        st.warning(f"合計 {total} 文字が上限 {max_chars} を超えています。減らしてください。")

    if st.button("🔊 生成する", type="primary", disabled=not lines or over):
        try:
            with st.spinner("生成中…（数秒）"):
                if len(lines) == 1:
                    audio = tts_mod.synthesize_one(
                        settings, lines[0]["text"], lines[0]["voice_id"],
                        stabs[0], None, DEFAULT_OUTPUT_FORMAT)
                else:
                    audio = tts_mod.synthesize_dialogue_bytes(settings, lines, DEFAULT_OUTPUT_FORMAT)
                final = _postprocess(audio, preset) if do_fx else audio
            st.success("できました！")
            st.audio(final, format="audio/mp3")
            st.download_button("⬇️ ダウンロード", final, file_name="voice.mp3", mime="audio/mp3")
        except Exception as e:  # noqa: BLE001
            st.error(f"生成に失敗しました: {e}")


main()
