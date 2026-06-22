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
import re
import tempfile
import zipfile
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
from src import notion as notion_mod
from src import tts as tts_mod
from src.config import DEFAULT_OUTPUT_FORMAT, CharacterBook, Settings
from src.models import Character

st.set_page_config(page_title="ボイス生成", page_icon="🎙️", layout="wide")

# 画像/Notion の文字起こし(Claude Vision)に使うモデル。感情つき書き起こしには
# Haiku で十分で、Opus の数分の1のコストで済む。APIキーは共有のまま、TTS など
# 他処理は通常モデルのまま(モデルは呼び出しごとに指定するため切替できる)。
TRANSCRIBE_MODEL = "claude-haiku-4-5"

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


def _block_stability(bid: int, spk: str, chars: dict) -> str:
    """ブロックの安定性: ブロック個別の上書きがあれば優先、無ければキャラ既定。"""
    ov = st.session_state.get(f"bstab_{bid}", "")
    if ov in ("creative", "natural", "robust"):
        return ov
    return chars[spk].stability if spk in chars else "natural"


def _gen_block(settings: Settings, chars: dict, bid: int) -> None:
    """1ブロックだけ合成して session_state[audio_<bid>] に保存(整音設定はsecrets/UI値)。"""
    spk = (st.session_state.get(f"spk_{bid}", "") or "").strip()
    txt = (st.session_state.get(f"txt_{bid}", "") or "").strip()
    vid = chars[spk].voice_id if spk in chars else spk
    if not txt or not vid:
        st.warning("セリフとキャラ（ボイス）を入れてください。")
        return
    stab = _block_stability(bid, spk, chars)
    gen_txt = _effective_text(spk, txt)
    prog = st.progress(0, text="生成の準備中…")
    stage = "準備"
    try:
        # TTSは1回。整音だけ natural / warm の2版を作って聴き比べ
        stage = "音声生成（ElevenLabs）"
        prog.progress(25, text=f"🎙️ {stage}…")
        raw = tts_mod.synthesize_one(settings, gen_txt, vid, stab, None, DEFAULT_OUTPUT_FORMAT)
        st.session_state[f"raw_{bid}"] = raw  # 再適用用に素の声を保持
        stage = "整音（ナチュラル）"
        prog.progress(60, text=f"🎚️ {stage}…")
        st.session_state[f"audioN_{bid}"] = _postprocess(raw, "natural")
        stage = "整音（ウォーム）"
        prog.progress(85, text=f"🎚️ {stage}…")
        st.session_state[f"audioW_{bid}"] = _postprocess(raw, "warm")
        _add_history(f"{spk}「{txt[:16]}」", st.session_state[f"audioN_{bid}"])
        prog.progress(100, text="✅ 完了")
    except Exception as e:  # noqa: BLE001
        st.error(f"生成に失敗しました（{stage}）: {e}")
    finally:
        prog.empty()


def _shrink_image(data: bytes, max_edge: int = 1568, quality: int = 85) -> tuple[bytes, str]:
    """送信サイズ削減のため画像を長辺max_edgeに縮小しJPEG化する(失敗時は元データ)。

    漫画スキャンは高解像度で、全画像を1リクエストに載せると413(大きすぎ)になる。
    文字起こしには長辺1568pxで十分(Haikuでも読める)。
    """
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((max_edge, max_edge))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality)
        return buf.getvalue(), ".jpg"
    except Exception:  # noqa: BLE001 - PIL無し/壊れ画像でも止めない
        return data, ".png"


def _analyze_images(settings: Settings, items: list[tuple[str, bytes]], batch: int = 6):
    """漫画画像を Claude Vision で解析し Script を返す(縮小+分割でサイズ上限を回避)。

    文字起こし(画像/Notion)は安価な Haiku で十分なため、ここだけ TRANSCRIBE_MODEL
    にモデルを差し替える。APIキーは共有のまま、TTS など他の処理には影響しない。
    画像は送信前に縮小し、batch 枚ずつに分けて解析→結合する(413 request_too_large 回避)。
    """
    import dataclasses

    from src import assets as assets_mod
    from src.analyze import analyze as analyze_inputs
    from src.config import ASSETS_DIR, CharacterBook
    from src.models import Script

    settings = dataclasses.replace(settings, model=TRANSCRIBE_MODEL)
    book = CharacterBook.load()
    bible = assets_mod.load_character_bible(ASSETS_DIR, book)
    shrunk = [_shrink_image(data) for (_ext, data) in items]

    step = max(1, batch)
    combined: Script | None = None
    for start in range(0, len(shrunk), step):
        with tempfile.TemporaryDirectory() as d:
            for k, (data, ext) in enumerate(shrunk[start:start + step]):
                (Path(d) / f"page_{start + k:03d}{ext}").write_bytes(data)
            # 文字起こしは固まらないよう短めのタイムアウトで上限化(失敗は即エラー表示)。
            script = analyze_inputs(settings, Path(d), language=book.language,
                                    character_bible=bible, timeout=120.0, max_retries=1)
        if combined is None:
            combined = script
        else:
            combined.scenes.extend(script.scenes)  # 分割結果を1本に結合
    return combined if combined is not None else Script(language=book.language)


_HONORIFICS = ("ちゃん", "くん", "君", "さん", "様", "先生")


def _norm_name(s: str) -> str:
    """話者名の照合用に末尾の敬称/呼称を落として正規化(あかりちゃん→あかり)。"""
    s = (s or "").strip()
    for h in _HONORIFICS:
        if s.endswith(h) and len(s) > len(h):
            return s[: -len(h)]
    return s


def _match_speaker(name: str, char_names: list[str]) -> str:
    """Claudeが返した話者名を、キャスト名にゆるく一致させる(敬称差を吸収)。"""
    if name in char_names:
        return name
    target = _norm_name(name)
    for c in char_names:  # あかりちゃん↔あかり 等を一致させる
        if c == target or _norm_name(c) == target or _norm_name(c) == name:
            return c
    return char_names[0] if char_names else name


def _merge_cast(project_cast: dict, current_cast: dict) -> dict:
    """プロジェクトのキャストに現在(既定)のキャストを重ね、現在を優先する。

    プロジェクトを開き直しても、既定として保存した最新のボイス割り当てが
    保たれる(プロジェクトにしか居ないキャラだけ取り込む)。
    """
    return {**(project_cast or {}), **(current_cast or {})}


def _lines_of(script, char_names: list[str]) -> list[tuple[str, str]]:
    out = []
    for scene in script.scenes:
        for line in scene.lines:
            out.append((_match_speaker(line.speaker, char_names),
                        line.resolved_tts_text()))
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


def _build_project(name: str, pairs: list[tuple[str, str]], settings: dict,
                   characters: dict | None = None) -> dict:
    """プロジェクト1件を表すdictを組む(台本＋設定＋キャスト)。保存/ZIPの共通フォーマット。"""
    return {
        "version": 3,
        "name": (name or "project").strip() or "project",
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "settings": settings,
        "characters": characters or {},
        "blocks": [{"speaker": s, "text": t} for s, t in pairs],
    }


def _project_zip(proj: dict, audio_files: list[tuple[str, bytes]]) -> bytes:
    """project.json と生成音声(mp3群)を1つのZIPにまとめてバイト列で返す。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(proj, ensure_ascii=False, indent=2))
        for fname, data in audio_files:
            z.writestr(fname, data)
    return buf.getvalue()


def _parse_project(filename: str, raw: bytes) -> dict:
    """アップロードされた .json か .zip からプロジェクトdictを取り出す。"""
    if filename.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            raw = z.read("project.json")
    return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)


def _tag_to_label(tag: str) -> str:
    """感情タグ(例 [excited])を tab_char の選択肢ラベルに戻す(無→「なし」)。"""
    for lab, t in TAG_CHOICES:
        if t == tag:
            return lab
    return "なし"


def _settings_to_state(s: dict) -> dict:
    """保存した settings を、ウィジェットキーへ流し込む形(_pending_settings)に変換。"""
    out: dict = {}
    if "preset" in s:
        out["preset"] = s["preset"]
    if "speed" in s:
        out["speed"] = float(s["speed"])
    if "do_fx" in s:
        out["do_fx"] = bool(s["do_fx"])
    for name, tag in (s.get("char_tone") or {}).items():
        out[f"chartone_sel_{name}"] = _tag_to_label(tag)
    return out


def _restore_project(data: dict) -> None:
    """読み込んだプロジェクトdictをセッションへ復元(台本/設定/キャスト/名前)。"""
    _set_blocks([(b.get("speaker", ""), b.get("text", ""))
                 for b in data.get("blocks", [])])
    st.session_state["_pending_settings"] = _settings_to_state(data.get("settings", {}))
    if data.get("characters"):  # キャスト(キャラ→ID)も復元
        st.session_state["_pending_cast"] = data["characters"]
    st.session_state["_pending_name"] = data.get("name", "")  # ウィジェット前に適用


def _all_audio_zip() -> bytes:
    """生成済みの各ブロック音声(ナチュラル/ウォーム)＋つなげた音声をZIPで返す。"""
    files: list[tuple[str, bytes]] = []
    for pos, bid in enumerate(st.session_state.get("block_ids", [])):
        spk = (st.session_state.get(f"spk_{bid}", "") or "blk").strip() or "blk"
        safe = re.sub(r"[^0-9A-Za-z぀-ヿ一-鿿_-]+", "_", spk)[:16]
        if st.session_state.get(f"audioN_{bid}"):
            files.append((f"{pos+1:02d}_{safe}_natural.mp3", st.session_state[f"audioN_{bid}"]))
        if st.session_state.get(f"audioW_{bid}"):
            files.append((f"{pos+1:02d}_{safe}_warm.mp3", st.session_state[f"audioW_{bid}"]))
    if st.session_state.get("audio_all"):
        files.append(("00_all.mp3", st.session_state["audio_all"]))
    if not files:
        return b""
    # 音声が変わっていなければ作り直さない(毎回の再実行で再ZIP化しない)
    sig = tuple((n, len(d)) for n, d in files)
    if st.session_state.get("_zip_sig") == sig and "_zip_cache" in st.session_state:
        return st.session_state["_zip_cache"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in files:
            z.writestr(n, d)
    out = buf.getvalue()
    st.session_state["_zip_sig"] = sig
    st.session_state["_zip_cache"] = out
    return out


def _gen_all_blocks(settings: Settings, chars: dict) -> int:
    """全ブロックを1つずつ生成(各ブロックに音声を入れる)。生成できた件数を返す。"""
    ids = list(st.session_state.get("block_ids", []))
    prog = st.progress(0, text="全ブロックを生成中…")
    ok = 0
    try:
        for idx, bid in enumerate(ids):
            spk = (st.session_state.get(f"spk_{bid}", "") or "").strip()
            txt = (st.session_state.get(f"txt_{bid}", "") or "").strip()
            vid = chars[spk].voice_id if spk in chars else spk
            if not txt or not vid:
                continue
            prog.progress(int(idx / max(1, len(ids)) * 100),
                          text=f"🎙️ {idx+1}/{len(ids)}：{spk or '—'}")
            stab = _block_stability(bid, spk, chars)
            raw = tts_mod.synthesize_one(settings, _effective_text(spk, txt), vid, stab,
                                         None, DEFAULT_OUTPUT_FORMAT)
            st.session_state[f"raw_{bid}"] = raw
            st.session_state[f"audioN_{bid}"] = _postprocess(raw, "natural")
            st.session_state[f"audioW_{bid}"] = _postprocess(raw, "warm")
            _add_history(f"{spk}「{txt[:16]}」", st.session_state[f"audioN_{bid}"])
            ok += 1
        prog.progress(100, text="✅ 完了")
    except Exception as e:  # noqa: BLE001
        st.error(f"生成に失敗（{ok+1}番目あたり）: {e}")
    finally:
        prog.empty()
    return ok


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


@st.dialog("📥 取り込み")
def _import_dialog(settings: Settings, char_names: list[str]) -> None:
    """画像/Notion から文字起こしして台本に反映するモーダル(＋ボタンから開く)。"""
    st.caption("画像 / Notion / 台本(md) から台本に反映します")
    if not settings.anthropic_api_key:
        st.info("画像/Notionは Secrets に ANTHROPIC_API_KEY（Claude）が必要です。")
    t_img, t_notion, t_md = st.tabs(["🖼 画像", "🔗 Notion", "📝 台本(md)"])

    with t_md:
        st.caption("シナリオ生成サイトの台本(ネームmd)を貼り付けて取り込みます")
        md = st.text_area("台本(md)", key="dlg_md", height=200,
                          placeholder="# 第NN話「…」\n## ページ別ネーム指示\n### P1（① ツカミ）\n- コマ1:【画】… ／【セリフ・あかり】「…」")
        if st.button("台本(md)→反映", use_container_width=True, key="dlg_tr_md",
                     disabled=not (md or "").strip()):
            try:
                from src.scenario_ingest import parse_neme
                scenario, _ = parse_neme(md)
                pairs = [(_match_speaker(ln["speaker"], char_names), ln["text"])
                         for s in scenario["scenes"] for ln in s["lines"]]
                if not pairs:
                    st.warning("台本からセリフを抽出できませんでした。形式を確認してください。")
                else:
                    _set_blocks(pairs)
                    st.session_state["_flash_main"] = f"台本から{len(pairs)}行を取り込みました。"
                    st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"取り込みに失敗: {e}")

    with t_img:
        if paste_image_button is not None:
            res = paste_image_button("📋 画像を貼り付け", key="dlg_paste")
            if getattr(res, "image_data", None) is not None:
                buf = io.BytesIO()
                res.image_data.save(buf, format="PNG")
                st.session_state["pasted_img"] = buf.getvalue()
        if st.session_state.get("pasted_img"):
            st.image(st.session_state["pasted_img"], use_container_width=True)
            if st.button("貼り付けを取り消し", key="dlg_clear_paste", use_container_width=True):
                st.session_state.pop("pasted_img", None)
                st.rerun()
        ups = st.file_uploader("画像（D&D／複数可）", type=["png", "jpg", "jpeg", "webp"],
                               accept_multiple_files=True, key="dlg_imgs")
        items = [(Path(f.name).suffix or ".png", f.getvalue()) for f in (ups or [])]
        if st.session_state.get("pasted_img"):
            items.append((".png", st.session_state["pasted_img"]))
        if st.button("文字起こし→台本に反映", use_container_width=True, key="dlg_tr_img",
                     disabled=not (settings.anthropic_api_key and items)):
            prog = st.progress(0, text="準備中…")
            try:
                prog.progress(40, text=f"🧠 Claude解析…（画像{len(items)}枚 / Haiku）")
                _transcribe_images(settings, items, char_names)
                prog.progress(100, text="✅ 完了")
                st.session_state.pop("pasted_img", None)
                st.session_state["_flash_main"] = "画像から台本に反映しました。"
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"文字起こしに失敗しました（Claude解析）: {e}")
            finally:
                prog.empty()

    with t_notion:
        notion_token = _secret("NOTION_TOKEN")
        if not notion_token:
            st.caption("※ Secrets に NOTION_TOKEN を追加すると有効")
        url = st.text_input("Notion ページ URL / ID", key="dlg_notion_url")
        if st.button("Notionから取り込み→台本に反映", use_container_width=True, key="dlg_tr_notion",
                     disabled=not (settings.anthropic_api_key and notion_token and url)):
            prog = st.progress(0, text="準備中…")
            stage = "Notion取得"
            try:
                prog.progress(30, text="📥 Notionから画像を取得中…")
                n_items = notion_mod.fetch_page_image_items(notion_token, url)
                if not n_items:
                    prog.empty()
                    st.warning("ページに画像が見つかりませんでした"
                               "（画像ブロックが無い／DB・子ページは対象外／未共有の可能性）。")
                else:
                    stage = "Claude解析"
                    prog.progress(65, text=f"🧠 Claude解析…（画像{len(n_items)}枚 / Haiku）")
                    _transcribe_images(settings, n_items, char_names)
                    prog.progress(100, text="✅ 完了")
                    st.session_state["_flash_main"] = f"Notionから{len(n_items)}枚を取り込みました。"
                    st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Notion取り込みに失敗しました（{stage}）: {e}")
            finally:
                prog.empty()


def main() -> None:
    if not _check_password():
        st.stop()

    # プロジェクト読込時の設定復元: ウィジェット生成より前に流し込む
    # (生成後だと "widget instantiated 後の変更" エラーになるため最上段で適用)。
    for k, v in (st.session_state.pop("_pending_settings", None) or {}).items():
        st.session_state[k] = v
    pend_cast = st.session_state.pop("_pending_cast", None)
    if pend_cast is not None:
        # プロジェクト内の(古い)割り当てより、現在＝既定のボイス割り当てを優先。
        # プロジェクトにしか居ないキャラだけ取り込む(開き直すと声が戻る問題を防ぐ)。
        st.session_state["cast"] = _merge_cast(pend_cast, st.session_state.get("cast", {}))
        st.session_state["cast_ver"] = st.session_state.get("cast_ver", 0) + 1
    if "_pending_name" in st.session_state:
        st.session_state["proj_name"] = st.session_state.pop("_pending_name")
    if st.session_state.pop("_new_project", False):  # 新規: 台本/音声/名前をクリア
        for k in [k for k in list(st.session_state.keys())
                  if str(k).startswith(("audio", "raw", "spk_", "txt_"))]:
            del st.session_state[k]
        st.session_state["block_ids"] = [0]
        st.session_state["block_seq"] = 1
        st.session_state["proj_name"] = ""
        st.session_state["_flash_main"] = "新規プロジェクトを作成しました（台本をクリア）。"

    api_key = _secret("ELEVENLABS_API_KEY")
    if not api_key:
        st.error("管理者へ: Secrets に ELEVENLABS_API_KEY を設定してください。")
        st.stop()
    settings = Settings(anthropic_api_key=_secret("ANTHROPIC_API_KEY"),
                        elevenlabs_api_key=api_key)
    max_chars = int(_secret("MAX_CHARS", "800") or 800)

    # GitHub保存(プロジェクト/既定キャストの保存先)。トークン未設定なら None。
    gh_store = None
    if _secret("GITHUB_TOKEN"):
        from src import store_github as gh_mod
        gh_store = gh_mod.GitHubStore(
            _secret("GITHUB_TOKEN"), _secret("GITHUB_REPO", "shinonomeheta-ai/manga-voice"))

    # キャスト(キャラ→ボイスID/トーン)。初回は「既定(クラウド)」→無ければ characters.json。
    book = CharacterBook.load()
    if "cast" not in st.session_state:
        seeded = None
        if gh_store is not None:
            try:
                seeded = gh_store.load_cast()
            except Exception:  # noqa: BLE001 - クラウド未設定/失敗でも起動する
                seeded = None
        if seeded:
            # クラウド既定: 基本トーンを分離して chartone_sel に種付け(ウィジェット前)
            for n, d in seeded.items():
                tone = (d or {}).get("tone", "")
                if tone and f"chartone_sel_{n}" not in st.session_state:
                    st.session_state[f"chartone_sel_{n}"] = _tag_to_label(tone)
            seeded = {n: {"voice_id": (d or {}).get("voice_id", ""),
                          "stability": (d or {}).get("stability", "natural")}
                      for n, d in seeded.items()}
        else:
            seeded = {n: {"voice_id": c.voice_id, "stability": c.stability}
                      for n, c in book.characters.items() if c.is_assigned()}
        st.session_state["cast"] = seeded
    cast = st.session_state["cast"]
    chars = {n: Character(name=n, voice_id=d.get("voice_id", ""),
                          stability=d.get("stability", "natural"))
             for n, d in cast.items() if (d.get("voice_id") or "").strip()}
    char_names = list(chars.keys())

    def _save_project_to_cloud() -> str:
        """現在の台本＋設定＋キャストをプロジェクト名でクラウド保存し、名前を返す。"""
        pset = {
            "preset": st.session_state.get("preset", "natural"),
            "speed": float(st.session_state.get("speed", 1.0) or 1.0),
            "do_fx": bool(st.session_state.get("do_fx", True)),
            "char_tone": {n: st.session_state.get(f"chartone_{n}", "")
                          for n in char_names if st.session_state.get(f"chartone_{n}", "")},
        }
        p = _build_project(st.session_state.get("proj_name", ""), _project_pairs(), pset, cast)
        with st.spinner("クラウドに保存中…"):
            gh_store.save_project(p["name"], p)
        return p["name"]

    def _save_default_cast() -> None:
        """ボイスID＋安定性＋基本トーンを、サイト共通の既定キャストとして保存。"""
        blob = {n: {"voice_id": d.get("voice_id", ""),
                    "stability": d.get("stability", "natural"),
                    "tone": st.session_state.get(f"chartone_{n}", "")}
                for n, d in st.session_state["cast"].items()}
        with st.spinner("既定として保存中…"):
            gh_store.save_cast(blob)

    if "block_ids" not in st.session_state:
        st.session_state.block_ids = [0]
        st.session_state.block_seq = 1

    # レイアウト: PCはサイドバー広め、スマホは折り返してタップしやすく
    st.markdown(
        """
        <style>
        /* PC(広い画面)のみサイドバーを広げる。スマホは既定のオーバーレイに任せる */
        @media (min-width: 900px){
          section[data-testid='stSidebar']{ width:440px !important; }
        }
        /* スマホ: 横並び(番号/話者/アイコン列など)を折り返して押しやすく */
        @media (max-width: 640px){
          div[data-testid='stHorizontalBlock']{ flex-wrap: wrap !important; }
          div[data-testid='stHorizontalBlock'] > div[data-testid='stColumn']{
            min-width: 44px !important;
          }
          .block-container{ padding: 0.6rem 0.6rem 4rem !important; }
          .stButton button{ min-height: 2.6rem; }  /* タップ領域を確保 */
        }
        </style>
        """,
        unsafe_allow_html=True)

    # ===== 左サイドバー: タブ(取り込み / 設定 / 感情 / 履歴 / プロジェクト) =====
    with st.sidebar:
        tab_proj, tab_cfg, tab_cast, tab_hist = st.tabs(
            ["💾 プロジェクト", "⚙️ 設定", "🎭 キャラ・感情", "🕘 履歴"])

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

        with tab_cast:
            st.markdown("**ボイス割り当て**")
            st.caption("キャラ名とボイスIDの割り当て（行の追加・削除・編集ができます）")
            ver = st.session_state.get("cast_ver", 0)
            rows = [{"キャラ": n, "voice_id": d.get("voice_id", ""),
                     "stability": d.get("stability", "natural")}
                    for n, d in cast.items()]
            edited = st.data_editor(
                rows, num_rows="dynamic", use_container_width=True,
                key=f"cast_editor_{ver}",
                column_config={
                    "キャラ": st.column_config.TextColumn("キャラ", required=True),
                    "voice_id": st.column_config.TextColumn("ボイスID（ElevenLabs）"),
                    "stability": st.column_config.SelectboxColumn(
                        "安定性", options=["creative", "natural", "robust"],
                        default="natural"),
                })
            new_cast: dict = {}
            for r in edited:
                nm = str(r.get("キャラ") or "").strip()
                if not nm:
                    continue
                new_cast[nm] = {"voice_id": str(r.get("voice_id") or "").strip(),
                                "stability": r.get("stability") or "natural"}
            if new_cast != cast:  # 編集が入ったら反映して再描画(ボイス一覧を更新)
                st.session_state["cast"] = new_cast
                st.rerun()
            st.caption("空欄のキャラは生成対象から外れます。変更はプロジェクト保存に含まれます。")
            if st.button("💾 ボイス割り当てを既定として保存", use_container_width=True,
                         key="cast_save_default", disabled=gh_store is None,
                         help="ボイスID＋安定性＋基本トーンをサイト共通の既定として保存"):
                try:
                    _save_default_cast()
                    st.success("既定として保存しました（次回・新規でも自動で読み込まれます）。")
                except Exception as e:  # noqa: BLE001
                    st.error(f"保存に失敗: {e}")
            if gh_store is None:
                st.caption("※ GITHUB_TOKEN を設定すると、ボイスIDをサイトに永続保存できます")

            st.divider()
            st.markdown("**基本トーン（感情）**")
            st.caption("タグが無いセリフに自動付与される、キャラごとの感情")
            label_to_tag = {lab: tag for lab, tag in TAG_CHOICES}
            tone_opts = ["なし"] + [lab for lab, _ in TAG_CHOICES]
            for name in char_names:
                sel = st.selectbox(name, tone_opts, key=f"chartone_sel_{name}")
                st.session_state[f"chartone_{name}"] = (
                    "" if sel == "なし" else label_to_tag.get(sel, ""))
            if not char_names:
                st.caption("（割当済みキャラがありません）")
            if st.button("💾 基本トーンを既定として保存", use_container_width=True,
                         key="tone_save_default", disabled=gh_store is None,
                         help="ボイスID＋安定性＋基本トーンをサイト共通の既定として保存"):
                try:
                    _save_default_cast()
                    st.success("既定として保存しました（次回・新規でも自動で読み込まれます）。")
                except Exception as e:  # noqa: BLE001
                    st.error(f"保存に失敗: {e}")

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
            st.markdown("**📂 プロジェクト**")
            if st.session_state.get("_flash"):  # 直前の保存/開く/削除の結果(rerunで保持)
                st.success(st.session_state.pop("_flash"))
            st.text_input("プロジェクト名", key="proj_name", placeholder="無題プロジェクト")
            pc = st.columns(2)
            if pc[0].button("➕ 新規", use_container_width=True, key="proj_new"):
                st.session_state["_new_project"] = True
                st.rerun()
            if pc[1].button("💾 保存", use_container_width=True, key="proj_save_left",
                            disabled=gh_store is None):
                try:
                    nm = _save_project_to_cloud()
                    st.session_state.pop("gh_names", None)  # 一覧キャッシュを無効化
                    st.session_state["_flash"] = f"「{nm}」を保存しました。"
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"保存に失敗: {e}")

            st.divider()
            if gh_store is None:
                st.caption("※ Secrets に GITHUB_TOKEN（Contents 読み書き）を追加すると保存できます")
            else:
                # 一覧はキャッシュ(毎回の通信を避ける)。保存/削除時のみ再取得。
                if "gh_names" not in st.session_state:
                    try:
                        st.session_state["gh_names"] = gh_store.list_projects()
                    except Exception as e:  # noqa: BLE001
                        st.session_state["gh_names"] = []
                        st.error(f"一覧の取得に失敗: {e}")
                names = st.session_state["gh_names"]
                hc = st.columns([4, 1])
                hc[0].caption(f"プロジェクト一覧（{len(names)}件・クリックで開く）")
                if hc[1].button("🔄", key="gh_refresh", help="一覧を更新"):
                    st.session_state.pop("gh_names", None)
                    st.rerun()
                if names:
                    for i, nm in enumerate(names):
                        c1, c2 = st.columns([5, 1])
                        if c1.button(f"📄 {nm}", use_container_width=True, key=f"gh_open_{i}"):
                            try:
                                with st.spinner("読み込み中…"):
                                    data = gh_store.load_project(nm)
                                _restore_project(data)
                                st.session_state["_flash"] = f"「{data.get('name', nm)}」を開きました。"
                                st.rerun()
                            except Exception as e:  # noqa: BLE001
                                st.error(f"読み込みに失敗: {e}")
                        if c2.button("🗑", key=f"gh_del_{i}", help=f"{nm} を削除"):
                            try:
                                gh_store.delete_project(nm)
                                st.session_state.pop("gh_names", None)  # 一覧キャッシュ無効化
                                st.session_state["_flash"] = f"「{nm}」を削除しました。"
                                st.rerun()
                            except Exception as e:  # noqa: BLE001
                                st.error(f"削除に失敗: {e}")
                else:
                    st.caption("（まだプロジェクトがありません）")

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
        stabs.append(_block_stability(bid, spk, chars))
        total += len(txt)

    # ===== メイン: タイトル(プロジェクト名) + 操作 + 台本 =====
    pname = (st.session_state.get("proj_name", "") or "").strip()
    st.title(f"🎙️ {pname or '無題プロジェクト'}")
    fbase = re.sub(r"[^0-9A-Za-z぀-ヿ一-鿿_-]+", "_", pname)[:40] or "project"
    if st.session_state.get("_flash_main"):
        st.success(st.session_state.pop("_flash_main"))

    b1, b2, b3 = st.columns([2, 1, 1])
    gen_all = b1.button("🔊 全ブロックを生成", type="primary",
                        disabled=not st.session_state.get("block_ids"),
                        use_container_width=True,
                        help="各ブロックを1つずつ生成し、ブロックごとに確認できます")
    zipbytes = _all_audio_zip()
    b2.download_button("⬇️ 一括DL", zipbytes if zipbytes else b"",
                       file_name=f"{fbase}_audio.zip", mime="application/zip",
                       use_container_width=True, key="dl_zip_main", disabled=not zipbytes,
                       help="生成済みの音声をまとめてダウンロード")
    if b3.button("💾 保存", use_container_width=True, key="proj_save_right",
                 disabled=gh_store is None, help="プロジェクトをクラウドに保存"):
        try:
            nm = _save_project_to_cloud()
            st.session_state.pop("gh_names", None)  # 一覧キャッシュを無効化
            st.session_state["_flash_main"] = f"「{nm}」を保存しました。"
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"保存に失敗: {e}")
    if gen_all:
        n = _gen_all_blocks(settings, chars)
        if n:
            st.session_state["_flash_main"] = f"{n} ブロックを生成しました。"
        else:
            st.session_state["_flash_main"] = (
                "生成できるブロックがありません。セリフを入力し、"
                "「🎭 キャラ・感情」でキャラにボイスIDを割り当ててください。")
        st.rerun()

    # 取り込み(画像/Notion)はモーダルで開く
    if st.button("📥 取り込み（画像 / Notion）", use_container_width=True, key="open_import"):
        _import_dialog(settings, char_names)

    # つなげて1本に(任意・掛け合いを1ファイルに)
    with st.expander("🔗 つなげて1本に生成（任意）"):
        over = total > max_chars
        if over:
            st.warning("文字数が上限を超えています。減らすか「全ブロックを生成」をご利用ください。")
        if st.button("🔗 つなげて生成", disabled=not lines or over,
                     use_container_width=True, key="gen_concat"):
            prog = st.progress(0, text="生成準備中…")
            stage = "準備"
            try:
                stage = "音声生成（ElevenLabs）"
                prog.progress(30, text=f"🎙️ {stage}…（{len(lines)}行）")
                if len(lines) == 1:
                    audio = tts_mod.synthesize_one(
                        settings, lines[0]["text"], lines[0]["voice_id"],
                        stabs[0], None, DEFAULT_OUTPUT_FORMAT)
                else:
                    audio = tts_mod.synthesize_dialogue_bytes(settings, lines, DEFAULT_OUTPUT_FORMAT)
                st.session_state["raw_all"] = audio
                stage = "整音"
                prog.progress(75, text=f"🎚️ {stage}…")
                preset = st.session_state.get("preset", "natural")
                do_fx = st.session_state.get("do_fx", True)
                st.session_state["audio_all"] = _postprocess(audio, preset if do_fx else None)
                _add_history(f"つなげて {len(lines)}行", st.session_state["audio_all"])
                prog.progress(100, text="✅ 完了")
            except Exception as e:  # noqa: BLE001
                st.session_state.pop("audio_all", None)
                st.error(f"生成に失敗しました（{stage}）: {e}")
            finally:
                prog.empty()
        if st.session_state.get("audio_all"):
            st.audio(st.session_state["audio_all"], format="audio/mp3")
            st.download_button("⬇️ つなげた音声をDL", st.session_state["audio_all"],
                               file_name=f"{fbase}.mp3", mime="audio/mp3", key="dl_all")

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
    move = None  # (bid, 方向): 並べ替え
    for i, bid in enumerate(st.session_state.block_ids):
        with st.container(border=True):
            # 上段: 番号 + 話者 + アイコン操作(画像/感情/生成/削除/上下移動)
            hdr = st.columns([1, 2, 1, 1, 1, 1, 1, 1, 2])
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
            with hdr[3].popover("🎭", use_container_width=True, help="感情タグ・安定性"):
                st.selectbox("安定性（このブロック）",
                             ["キャラ既定", "creative", "natural", "robust"],
                             key=f"bstab_sel_{bid}",
                             help="creative=抑揚が出る/robust=安定だが平板")
                st.session_state[f"bstab_{bid}"] = (
                    "" if st.session_state.get(f"bstab_sel_{bid}") == "キャラ既定"
                    else st.session_state.get(f"bstab_sel_{bid}", ""))
                st.caption("感情タグ（文頭に挿入）")
                for j, (label, tag) in enumerate(TAG_CHOICES):
                    st.button(label, key=f"tag_{bid}_{j}", use_container_width=True,
                              on_click=_insert_tone_tag, args=(bid, tag))
            if hdr[4].button("🔊", key=f"gen_{bid}", use_container_width=True,
                             help="このブロックを生成"):
                _gen_block(settings, chars, bid)
            if hdr[5].button("🗑", key=f"del_{bid}", use_container_width=True, help="削除"):
                remove_id = bid
            if hdr[6].button("⬆️", key=f"up_{bid}", use_container_width=True, help="上へ"):
                move = (bid, -1)
            if hdr[7].button("⬇️", key=f"down_{bid}", use_container_width=True, help="下へ"):
                move = (bid, 1)
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

    if move is not None:  # ⬆️⬇️ で隣と入れ替え
        ids = list(st.session_state.block_ids)
        bid, d = move
        idx = ids.index(bid)
        j = idx + d
        if 0 <= j < len(ids):
            ids[idx], ids[j] = ids[j], ids[idx]
            st.session_state.block_ids = ids
            st.rerun()


main()
