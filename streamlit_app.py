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

import datetime
import io
import json
import os
import tempfile
from pathlib import Path

import streamlit as st

try:  # クリップボード貼り付けボタン(未導入環境でも動くよう任意依存)
    from streamlit_paste_button import paste_image_button
except Exception:  # noqa: BLE001
    paste_image_button = None

try:  # ドラッグ並べ替え(任意依存)
    from streamlit_sortables import sort_items
except Exception:  # noqa: BLE001
    sort_items = None

from src import audio_fx as fx_mod
from src import tts as tts_mod
from src.config import DEFAULT_OUTPUT_FORMAT, CharacterBook, Settings

st.set_page_config(page_title="ボイス生成（共有版）", page_icon="🎙️", layout="wide")

# トーン(声の調子)タグのみ。(日本語ラベル, 実際に挿入するv3タグ)。
# 反応・効果音系([laughs]/[sigh]/[gasps]等)は非言語音が入るので含めない。
# v3 は英語タグのみ解釈するため、ボタンは日本語表示・挿入は英語タグにする。
TAG_CHOICES = [
    ("興奮", "[excited]"), ("うれしい", "[happy]"), ("明るい", "[cheerful]"),
    ("緊張", "[nervous]"), ("悲しげ", "[sad]"), ("怒り", "[angry]"),
    ("落ち着き", "[calm]"), ("真剣", "[serious]"), ("驚き", "[surprised]"),
    ("皮肉", "[sarcastic]"), ("やさしい", "[warm]"), ("ささやき", "[whispers]"),
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


def _insert_tone_tag(block_id: int, tag: str) -> None:
    """トーンタグを文頭に挿入する(v3はタグ直後の言い方を変えるため先頭が基本)。"""
    key = f"txt_{block_id}"
    cur = (st.session_state.get(key, "") or "").lstrip()
    st.session_state[key] = f"{tag} {cur}".rstrip()


def _postprocess(audio: bytes, preset: str | None = None) -> bytes:
    """整音(preset)＋再生スピード(session_state['speed'])をかけて bytes を返す。"""
    try:
        speed = float(st.session_state.get("speed", 1.0) or 1.0)
    except Exception:  # noqa: BLE001
        speed = 1.0
    if preset is None and abs(speed - 1.0) < 1e-3:
        return audio
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "raw.mp3"
        dst = Path(d) / "out.mp3"
        src.write_bytes(audio)
        fx_mod.apply_fx(src, dst, preset=preset, speed=speed)
        return dst.read_bytes() if dst.exists() else audio


def _reapply_effects() -> int:
    """保持してある素の声(raw)に、現在の整音/速度を再適用(TTSなし=課金なし)。"""
    preset = st.session_state.get("preset", "natural")
    do_fx = st.session_state.get("do_fx", True)
    n = 0
    for bid in st.session_state.get("block_ids", []):
        raw = st.session_state.get(f"raw_{bid}")
        if raw:
            st.session_state[f"audioN_{bid}"] = _postprocess(raw, "natural")
            st.session_state[f"audioW_{bid}"] = _postprocess(raw, "warm")
            n += 1
    raw_all = st.session_state.get("raw_all")
    if raw_all:
        st.session_state["audio_all"] = _postprocess(raw_all, preset if do_fx else None)
        n += 1
    return n


def _effective_text(speaker: str, text: str) -> str:
    """タグが無いセリフに、キャラの基本トーン(サイドバー設定)を自動付与する。"""
    text = (text or "").strip()
    if not text or text.startswith("["):
        return text
    tone = st.session_state.get(f"chartone_{speaker}", "")
    return f"{tone} {text}".strip() if tone else text


def _gen_block(settings: Settings, chars: dict, bid: int) -> None:
    """1ブロックだけ合成して session_state[audio_<bid>] に保存(整音設定はsecrets/UI値)。"""
    spk = (st.session_state.get(f"spk_{bid}", "") or "").strip()
    txt = (st.session_state.get(f"txt_{bid}", "") or "").strip()
    vid = chars[spk].voice_id if spk in chars else spk
    if not txt or not vid:
        st.warning("セリフとキャラ（ボイス）を入れてください。")
        return
    stab = chars[spk].stability if spk in chars else "natural"
    gen_txt = _effective_text(spk, txt)
    try:
        with st.spinner("生成中…（聴き比べ用に2版）"):
            # TTSは1回。整音だけ natural / warm の2版を作って聴き比べ
            raw = tts_mod.synthesize_one(settings, gen_txt, vid, stab, None, DEFAULT_OUTPUT_FORMAT)
            st.session_state[f"raw_{bid}"] = raw  # 再適用用に素の声を保持
            st.session_state[f"audioN_{bid}"] = _postprocess(raw, "natural")
            st.session_state[f"audioW_{bid}"] = _postprocess(raw, "warm")
            _add_history(f"{spk}「{txt[:16]}」", st.session_state[f"audioN_{bid}"])
    except Exception as e:  # noqa: BLE001
        st.error(f"生成に失敗しました: {e}")


def _analyze_images(settings: Settings, items: list[tuple[str, bytes]]):
    """漫画画像(拡張子, バイト列)を Claude Vision で解析し Script を返す。"""
    from src import assets as assets_mod
    from src.analyze import analyze as analyze_inputs
    from src.config import ASSETS_DIR, CharacterBook

    with tempfile.TemporaryDirectory() as d:
        for k, (ext, data) in enumerate(items):
            (Path(d) / f"page_{k:03d}{(ext or '.png').lower()}").write_bytes(data)
        book = CharacterBook.load()
        bible = assets_mod.load_character_bible(ASSETS_DIR, book)
        return analyze_inputs(settings, Path(d), language=book.language,
                              character_bible=bible)


def _lines_of(script, char_names: list[str]) -> list[tuple[str, str]]:
    out = []
    for scene in script.scenes:
        for line in scene.lines:
            spk = line.speaker if line.speaker in char_names else (
                char_names[0] if char_names else line.speaker)
            out.append((spk, line.resolved_tts_text()))
    return out


def _add_history(label: str, audio: bytes) -> None:
    """生成した音声をセッションの履歴に積む(最新12件・リロードまで保持)。"""
    hist = st.session_state.setdefault("history", [])
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    hist.append({"label": f"{ts} {label}", "audio": audio})
    del hist[:-12]


def _project_pairs() -> list[tuple[str, str]]:
    """現在のブロック内容(speaker, text)を順に取り出す。"""
    return [(st.session_state.get(f"spk_{b}", ""), st.session_state.get(f"txt_{b}", ""))
            for b in st.session_state.get("block_ids", [])]


def _set_blocks(pairs: list[tuple[str, str]]) -> None:
    """(speaker, text) の並びでブロックを作り直す(idは0から振り直し)。"""
    for k in [k for k in list(st.session_state.keys())
              if str(k).startswith(("audio", "raw"))]:
        del st.session_state[k]
    if not pairs:
        pairs = [("", "")]
    ids = []
    for seq, (spk, txt) in enumerate(pairs):
        st.session_state[f"spk_{seq}"] = spk
        st.session_state[f"txt_{seq}"] = txt
        ids.append(seq)
    st.session_state.block_ids = ids
    st.session_state.block_seq = len(ids)


def _transcribe_images(settings: Settings, items: list[tuple[str, bytes]],
                       char_names: list[str]) -> None:
    """画像群を解析し、台本全体を作り直す(トップの取り込み用)。"""
    _set_blocks(_lines_of(_analyze_images(settings, items), char_names))


def _transcribe_into_block(settings: Settings, items, char_names: list[str], bid: int) -> None:
    """画像を解析し、その結果を bid の位置に差し込む(ブロックへのD&D用)。"""
    new = _lines_of(_analyze_images(settings, items), char_names)
    if not new:
        return
    snap = [(st.session_state.get(f"spk_{b}", ""), st.session_state.get(f"txt_{b}", ""))
            for b in st.session_state.block_ids]
    pos = st.session_state.block_ids.index(bid)
    _set_blocks(snap[:pos] + new + snap[pos + 1:])


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

    if "block_ids" not in st.session_state:
        st.session_state.block_ids = [0]
        st.session_state.block_seq = 1

    # サイドバーを少し広げる
    st.markdown(
        "<style>section[data-testid='stSidebar']{width:360px !important;}</style>",
        unsafe_allow_html=True)

    # ===== 左サイドバー: タブ(取り込み / 設定 / 感情 / 履歴 / プロジェクト) =====
    with st.sidebar:
        tab_in, tab_cfg, tab_char, tab_hist, tab_proj = st.tabs(
            ["📥 取り込み", "⚙️ 設定", "🎭 感情", "🕘 履歴", "💾 プロジェクト"])

        with tab_in:
            st.caption("画像から感情付きで文字起こし")
            if not settings.anthropic_api_key:
                st.info("Secrets に ANTHROPIC_API_KEY（Claude）を追加すると使えます。")
            if paste_image_button is not None:
                res = paste_image_button("📋 画像を貼り付け", key="paste_btn")
                if getattr(res, "image_data", None) is not None:
                    buf = io.BytesIO()
                    res.image_data.save(buf, format="PNG")
                    st.session_state["pasted_img"] = buf.getvalue()
            else:
                st.caption("※ 貼り付け未導入（streamlit-paste-button）")
            if st.session_state.get("pasted_img"):
                st.image(st.session_state["pasted_img"], use_container_width=True, caption="貼り付け画像")
                if st.button("貼り付けを取り消し", key="clear_paste", use_container_width=True):
                    st.session_state.pop("pasted_img", None)
                    st.rerun()
            ups = st.file_uploader("またはD&D／ファイル選択（複数可）",
                                   type=["png", "jpg", "jpeg", "webp"],
                                   accept_multiple_files=True, key="tr_imgs")
            items: list[tuple[str, bytes]] = []
            for f in ups or []:
                items.append((Path(f.name).suffix or ".png", f.getvalue()))
            if st.session_state.get("pasted_img"):
                items.append((".png", st.session_state["pasted_img"]))
            if st.button("文字起こし→台本に反映", use_container_width=True,
                         disabled=not (settings.anthropic_api_key and items)):
                try:
                    with st.spinner("解析中…（Claudeが画像を読み取り）"):
                        _transcribe_images(settings, items, char_names)
                    st.session_state.pop("pasted_img", None)
                    st.success("台本に反映しました。")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"文字起こしに失敗しました: {e}")

        with tab_cfg:
            st.selectbox("整音プリセット", list(fx_mod.PRESETS.keys()), index=0,
                         format_func=lambda k: PRESET_LABELS.get(k, k), key="preset")
            st.checkbox("整音エフェクトをかける", value=True, key="do_fx")
            st.slider("再生スピード", 0.5, 2.0, 1.0, 0.05, key="speed",
                      help="1.0が等速。生成時に反映され、ダウンロードにも適用されます")
            if st.button("🎛 現在の設定で再適用（再生成なし）", use_container_width=True,
                         key="reapply", help="生成済みの声に整音/速度をかけ直す（ElevenLabs課金なし）"):
                cnt = _reapply_effects()
                st.success(f"{cnt} 件に再適用しました（TTSなし）")
                st.rerun()
            st.caption("モデル: eleven_v3 固定")

        with tab_char:
            st.caption("キャラの基本トーン（タグが無いセリフに自動付与）")
            label_to_tag = {lab: tag for lab, tag in TAG_CHOICES}
            tone_opts = ["なし"] + [lab for lab, _ in TAG_CHOICES]
            for name in char_names:
                sel = st.selectbox(name, tone_opts, key=f"chartone_sel_{name}")
                st.session_state[f"chartone_{name}"] = (
                    "" if sel == "なし" else label_to_tag.get(sel, ""))
            if not char_names:
                st.caption("（割当済みキャラがありません）")

        with tab_hist:
            hist = st.session_state.get("history", [])
            if not hist:
                st.caption("まだありません（生成すると追加）")
            else:
                if st.button("履歴をクリア", use_container_width=True, key="hist_clear"):
                    st.session_state["history"] = []
                    st.rerun()
                for h in reversed(hist):
                    st.caption(h["label"])
                    st.audio(h["audio"], format="audio/mp3")

        with tab_proj:
            proj = {"version": 1,
                    "blocks": [{"speaker": s, "text": t} for s, t in _project_pairs()]}
            st.download_button("⬇️ 保存（JSON）", json.dumps(proj, ensure_ascii=False, indent=2),
                               file_name="project.json", mime="application/json",
                               use_container_width=True, key="proj_dl")
            upj = st.file_uploader("📂 読み込み（JSON）", type=["json"], key="proj_up")
            if upj is not None and st.button("読み込む", use_container_width=True, key="proj_load"):
                try:
                    data = json.loads(upj.getvalue().decode("utf-8"))
                    _set_blocks([(b.get("speaker", ""), b.get("text", ""))
                                 for b in data.get("blocks", [])])
                    st.success("読み込みました。")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"読み込み失敗: {e}")

    # ===== 全ブロックを収集(出力バー用) =====
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
        lines.append({"text": _effective_text(spk, txt), "voice_id": voice_id})
        stabs.append(chars[spk].stability if spk in chars else "natural")
        total += len(txt)

    # ===== メイン: 出力バー + 台本(全幅) =====
    st.title("🎙️ ボイス生成（共有版）")
    over = total > max_chars
    if st.button("🔊 全部つなげて生成", type="primary",
                 disabled=not lines or over, use_container_width=True):
        try:
            with st.spinner("生成中…（数秒）"):
                if len(lines) == 1:
                    audio = tts_mod.synthesize_one(
                        settings, lines[0]["text"], lines[0]["voice_id"],
                        stabs[0], None, DEFAULT_OUTPUT_FORMAT)
                else:
                    audio = tts_mod.synthesize_dialogue_bytes(settings, lines, DEFAULT_OUTPUT_FORMAT)
                st.session_state["raw_all"] = audio  # 再適用用に素の声を保持
                preset = st.session_state.get("preset", "natural")
                do_fx = st.session_state.get("do_fx", True)
                st.session_state["audio_all"] = _postprocess(audio, preset if do_fx else None)
                _add_history(f"掛け合い {len(lines)}行", st.session_state["audio_all"])
        except Exception as e:  # noqa: BLE001
            st.session_state.pop("audio_all", None)
            st.error(f"生成に失敗しました: {e}")
    if over:
        st.warning("文字数が上限を超えています。減らしてください。")
    if st.session_state.get("audio_all"):
        st.audio(st.session_state["audio_all"], format="audio/mp3")
        st.download_button("⬇️ まとめてDL", st.session_state["audio_all"],
                           file_name="story.mp3", mime="audio/mp3", key="dl_all")

    st.divider()

    # ドラッグで並べ替え(コンポーネントがある時のみ・失敗してもアプリは落とさない)
    if sort_items is not None and len(st.session_state.block_ids) > 1:
        with st.expander("🔀 ドラッグで並べ替え"):
            try:
                lab2bid = {}
                labels = []
                for pos, bid in enumerate(st.session_state.block_ids):
                    spk = st.session_state.get(f"spk_{bid}", "")
                    txt = st.session_state.get(f"txt_{bid}", "")
                    lab = f"[{bid}] {pos + 1}. {spk}｜{txt[:14]}"
                    labels.append(lab)
                    lab2bid[lab] = bid
                ordered = sort_items(labels, direction="vertical", key="sorter")
                new_ids = [lab2bid[x] for x in ordered if x in lab2bid]
                if len(new_ids) == len(st.session_state.block_ids) and new_ids != st.session_state.block_ids:
                    st.session_state.block_ids = new_ids
                    st.rerun()
            except Exception:  # noqa: BLE001 - コンポーネント障害でも落とさない
                st.caption("（並べ替えコンポーネントが使えません。各ブロックで編集してください）")

    remove_id = None
    for i, bid in enumerate(st.session_state.block_ids):
        with st.container(border=True):
            # 上段: 番号 + 話者(左寄せ) + アイコン操作(画像/感情タグ/生成/削除) + 余白
            hdr = st.columns([1, 2, 1, 1, 1, 1, 4])
            hdr[0].markdown(f"### {i + 1}")
            if char_names:
                hdr[1].selectbox("キャラ", char_names, key=f"spk_{bid}",
                                 label_visibility="collapsed")
            else:
                hdr[1].text_input("voice_id", key=f"spk_{bid}",
                                  label_visibility="collapsed")
            with hdr[2].popover("🖼", use_container_width=True, help="画像から文字起こしして差し込む"):
                bup = st.file_uploader("画像をドロップ", type=["png", "jpg", "jpeg", "webp"],
                                       accept_multiple_files=True, key=f"bimg_{bid}")
                bitems = [(Path(f.name).suffix or ".png", f.getvalue()) for f in (bup or [])]
                if st.button("このブロックに反映", key=f"bdo_{bid}",
                             disabled=not (settings.anthropic_api_key and bitems)):
                    try:
                        with st.spinner("解析中…"):
                            _transcribe_into_block(settings, bitems, char_names, bid)
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(f"文字起こしに失敗: {e}")
            with hdr[3].popover("🎭", use_container_width=True, help="感情タグを挿入"):
                for j, (label, tag) in enumerate(TAG_CHOICES):
                    st.button(label, key=f"tag_{bid}_{j}", use_container_width=True,
                              on_click=_insert_tone_tag, args=(bid, tag))
            if hdr[4].button("🔊", key=f"gen_{bid}", use_container_width=True,
                             help="このブロックを生成"):
                _gen_block(settings, chars, bid)
            if hdr[5].button("🗑", key=f"del_{bid}", use_container_width=True, help="削除"):
                remove_id = bid
            # セリフ(全幅)
            st.text_area("セリフ", key=f"txt_{bid}", height=80,
                         label_visibility="collapsed", placeholder="セリフ…")
            # 生成済み音声: ナチュラル / ウォーム を聴き比べ
            an = st.session_state.get(f"audioN_{bid}")
            aw = st.session_state.get(f"audioW_{bid}")
            if an or aw:
                cmp = st.columns(2)
                if an:
                    cmp[0].caption("ナチュラル")
                    cmp[0].audio(an, format="audio/mp3")
                    cmp[0].download_button("⬇️ ナチュラル", an, key=f"dlN_{bid}",
                                           file_name=f"block_{bid}_natural.mp3", mime="audio/mp3")
                if aw:
                    cmp[1].caption("ウォーム")
                    cmp[1].audio(aw, format="audio/mp3")
                    cmp[1].download_button("⬇️ ウォーム", aw, key=f"dlW_{bid}",
                                           file_name=f"block_{bid}_warm.mp3", mime="audio/mp3")

    if st.button("＋ ブロックを追加", use_container_width=True):
        st.session_state.block_ids.append(st.session_state.block_seq)
        st.session_state.block_seq += 1
        st.rerun()

    if remove_id is not None and len(st.session_state.block_ids) > 1:
        st.session_state.block_ids = [b for b in st.session_state.block_ids if b != remove_id]
        st.rerun()


main()
