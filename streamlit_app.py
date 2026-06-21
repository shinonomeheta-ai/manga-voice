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

st.set_page_config(page_title="ボイス生成（共有版）", page_icon="🎙️", layout="wide")

# ワンクリック挿入できる感情/演出タグ。(日本語ラベル, 実際に挿入するv3タグ)。
# v3 は英語タグのみ解釈するため、ボタンは日本語表示・挿入は英語タグのままにする。
TAG_CHOICES = [
    ("笑い", "[laughs]"), ("ため息", "[sigh]"), ("ささやき", "[whispers]"),
    ("興奮", "[excited]"), ("緊張", "[nervous]"), ("息をのむ", "[gasps]"),
    ("ためらい", "[hesitates]"), ("間", "[pause]"),
]

# 整音プリセットの日本語表示(内部キーは英語のまま)
PRESET_LABELS = {
    "natural": "ナチュラル（クリーン整音）",
    "clean": "最小（音量そろえのみ）",
    "warm": "ウォーム（温かみ・存在感）",
}


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


def _gen_block(settings: Settings, chars: dict, bid: int) -> None:
    """1ブロックだけ合成して session_state[audio_<bid>] に保存(整音設定はsecrets/UI値)。"""
    spk = (st.session_state.get(f"spk_{bid}", "") or "").strip()
    txt = (st.session_state.get(f"txt_{bid}", "") or "").strip()
    vid = chars[spk].voice_id if spk in chars else spk
    if not txt or not vid:
        st.warning("セリフとキャラ（ボイス）を入れてください。")
        return
    preset = st.session_state.get("preset", "natural")
    do_fx = st.session_state.get("do_fx", True)
    stab = chars[spk].stability if spk in chars else "natural"
    try:
        with st.spinner("生成中…"):
            a = tts_mod.synthesize_one(settings, txt, vid, stab, None, DEFAULT_OUTPUT_FORMAT)
            st.session_state[f"audio_{bid}"] = _postprocess(a, preset) if do_fx else a
    except Exception as e:  # noqa: BLE001
        st.error(f"生成に失敗しました: {e}")


def _script_to_blocks(script, char_names: list[str]) -> None:
    """解析結果(Script)を、編集可能なブロック(session_state)へ展開する。"""
    for k in [k for k in list(st.session_state.keys()) if str(k).startswith("audio_")]:
        del st.session_state[k]  # 旧ブロックの生成音声をクリア
    seq = 0
    st.session_state.block_ids = []
    for scene in script.scenes:
        for line in scene.lines:
            bid = seq
            seq += 1
            st.session_state.block_ids.append(bid)
            spk = line.speaker if line.speaker in char_names else (
                char_names[0] if char_names else line.speaker)
            st.session_state[f"spk_{bid}"] = spk
            st.session_state[f"txt_{bid}"] = line.resolved_tts_text()
    if not st.session_state.block_ids:
        st.session_state.block_ids = [0]
    st.session_state.block_seq = max(seq, 1)


def _transcribe_images(settings: Settings, files, char_names: list[str]) -> None:
    """ドラッグ&ドロップした漫画画像を Claude Vision で解析し、感情付き台本に変換。"""
    import tempfile
    from src import assets as assets_mod
    from src.analyze import analyze as analyze_inputs
    from src.config import ASSETS_DIR, CharacterBook

    with tempfile.TemporaryDirectory() as d:
        for k, f in enumerate(files):
            ext = (Path(f.name).suffix or ".png").lower()
            (Path(d) / f"page_{k:03d}{ext}").write_bytes(f.getvalue())
        book = CharacterBook.load()
        bible = assets_mod.load_character_bible(ASSETS_DIR, book)
        script = analyze_inputs(settings, Path(d), language=book.language,
                                character_bible=bible)
    _script_to_blocks(script, char_names)


def main() -> None:
    if not _check_password():
        st.stop()

    api_key = _secret("ELEVENLABS_API_KEY")
    if not api_key:
        st.error("管理者へ: Secrets に ELEVENLABS_API_KEY を設定してください。")
        st.stop()
    settings = Settings(anthropic_api_key=_secret("ANTHROPIC_API_KEY"),
                        elevenlabs_api_key=api_key)
    max_chars = int(_secret("MAX_CHARS", "800") or 800)

    book = CharacterBook.load()
    chars = {n: c for n, c in book.characters.items() if c.is_assigned()}
    char_names = list(chars.keys())

    st.title("🎙️ ボイス生成（共有版）")
    st.caption(f"キャラごとにセリフを足して物語を作れます。モデルは **eleven_v3 固定**。"
               f"合計最大 {max_chars} 文字。")

    if "block_ids" not in st.session_state:
        st.session_state.block_ids = [0]
        st.session_state.block_seq = 1

    left, right = st.columns([4, 1])

    # ===== 左: セリフ（キャラごとのブロック）=====
    remove_id = None
    with left:
        # 画像から感情付きで自動台本化(Claude Vision)
        with st.expander("🖼 画像から自動で台本化（感情付き文字起こし）"):
            if not settings.anthropic_api_key:
                st.info("この機能は Secrets に ANTHROPIC_API_KEY（Claude）を追加すると使えます。")
            ups = st.file_uploader("漫画画像をドラッグ&ドロップ（複数可）",
                                   type=["png", "jpg", "jpeg", "webp"],
                                   accept_multiple_files=True, key="tr_imgs")
            if st.button("文字起こし→台本に反映", disabled=not (settings.anthropic_api_key and ups)):
                try:
                    with st.spinner("解析中…（Claudeが画像を読み取り）"):
                        _transcribe_images(settings, ups, char_names)
                    st.success("台本に反映しました。下のブロックを確認・編集してください。")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"文字起こしに失敗しました: {e}")

        st.caption("台本（番号順に読み上げ）")
        for i, bid in enumerate(st.session_state.block_ids):
            with st.container(border=True):
                # 上段: 番号 + 話者 + アイコン操作(感情タグ/生成/削除)
                hdr = st.columns([1, 5, 1, 1, 1])
                hdr[0].markdown(f"### {i + 1}")
                if char_names:
                    hdr[1].selectbox("キャラ", char_names, key=f"spk_{bid}",
                                     label_visibility="collapsed")
                else:
                    hdr[1].text_input("voice_id", key=f"spk_{bid}",
                                      label_visibility="collapsed")
                with hdr[2].popover("🎭", use_container_width=True, help="感情タグを挿入"):
                    for j, (label, tag) in enumerate(TAG_CHOICES):
                        st.button(label, key=f"tag_{bid}_{j}", use_container_width=True,
                                  on_click=_append_tag_block, args=(bid, tag))
                if hdr[3].button("🔊", key=f"gen_{bid}", use_container_width=True,
                                 help="このブロックを生成"):
                    _gen_block(settings, chars, bid)
                if hdr[4].button("🗑", key=f"del_{bid}", use_container_width=True, help="削除"):
                    remove_id = bid
                # セリフ(全幅)
                st.text_area("セリフ", key=f"txt_{bid}", height=80,
                             label_visibility="collapsed", placeholder="セリフ…")
                # 生成済み音声
                if st.session_state.get(f"audio_{bid}"):
                    st.audio(st.session_state[f"audio_{bid}"], format="audio/mp3")
                    st.download_button("⬇️ DL", st.session_state[f"audio_{bid}"],
                                       file_name=f"block_{bid}.mp3", mime="audio/mp3", key=f"dl_{bid}")
        if st.button("＋ ブロックを追加", use_container_width=True):
            st.session_state.block_ids.append(st.session_state.block_seq)
            st.session_state.block_seq += 1
            st.rerun()

    if remove_id is not None and len(st.session_state.block_ids) > 1:
        st.session_state.block_ids = [b for b in st.session_state.block_ids if b != remove_id]
        st.rerun()

    # 全ブロックを収集
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

    # ===== 右: 設定 ＋ まとめて生成 =====
    with right:
        st.subheader("設定")
        preset = st.selectbox("整音プリセット", list(fx_mod.PRESETS.keys()), index=0,
                              format_func=lambda k: PRESET_LABELS.get(k, k), key="preset")
        do_fx = st.checkbox("整音エフェクトをかける", value=True, key="do_fx")
        st.caption(f"合計 {total} / {max_chars} 文字")
        over = total > max_chars
        if over:
            st.warning("文字数が上限を超えています。減らしてください。")
        if st.button("🔊 全部つなげて生成", type="primary", disabled=not lines or over,
                     use_container_width=True):
            try:
                with st.spinner("生成中…（数秒）"):
                    if len(lines) == 1:
                        audio = tts_mod.synthesize_one(
                            settings, lines[0]["text"], lines[0]["voice_id"],
                            stabs[0], None, DEFAULT_OUTPUT_FORMAT)
                    else:
                        audio = tts_mod.synthesize_dialogue_bytes(settings, lines, DEFAULT_OUTPUT_FORMAT)
                    st.session_state["audio_all"] = _postprocess(audio, preset) if do_fx else audio
            except Exception as e:  # noqa: BLE001
                st.session_state.pop("audio_all", None)
                st.error(f"生成に失敗しました: {e}")
        if st.session_state.get("audio_all"):
            st.audio(st.session_state["audio_all"], format="audio/mp3")
            st.download_button("⬇️ まとめてDL", st.session_state["audio_all"],
                               file_name="story.mp3", mime="audio/mp3", key="dl_all")


main()
