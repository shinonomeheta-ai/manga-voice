"""シナリオ生成 共有Webアプリ（音声アプリとは別サイト）。

資料（実在事件のメモ）＋必ず入れたいシーン（チェックポイント）から、
『ななしちゃん@がんばらない』の7パート構成のネーム台本(md)を生成する。
出力は音声アプリの「📥 取り込み → 📝 台本(md)」にそのまま貼って音声化できる。

Streamlit Community Cloud で、このファイルを別アプリとしてデプロイ（別URL）する想定。
必要な Secrets: ANTHROPIC_API_KEY（生成に必須）/ APP_PASSWORD（任意・合言葉）。
"""
from __future__ import annotations

import os

import streamlit as st

from src import scenario_gen as sgen
from src.config import Settings

st.set_page_config(page_title="シナリオ生成", page_icon="📝", layout="centered")

# スマホ前提: 余白を詰める
st.markdown(
    "<style>.block-container{padding:0.8rem 0.8rem 4rem;max-width:780px;}"
    ".stButton button{min-height:2.6rem;}</style>",
    unsafe_allow_html=True)


def _secret(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # noqa: BLE001 - secrets未設定でも動く
        pass
    return os.getenv(name, default)


def _check_password() -> bool:
    expected = _secret("APP_PASSWORD")
    if not expected:
        return True  # 未設定なら認証不要
    if st.session_state.get("authed"):
        return True
    st.title("🔒 ログイン")
    pw = st.text_input("合言葉", type="password", key="pw")
    if st.button("入る", use_container_width=True):
        if pw == expected:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("合言葉が違います。")
    return False


def main() -> None:
    if not _check_password():
        st.stop()

    # プロジェクト読込等は無し。資料の読み込みだけウィジェット前に適用。
    if "_pending_material" in st.session_state:
        st.session_state["material"] = st.session_state.pop("_pending_material")

    settings = Settings(anthropic_api_key=_secret("ANTHROPIC_API_KEY"),
                        elevenlabs_api_key="")

    st.title("📝 シナリオ生成")
    st.caption("資料（実在事件）＋必ず入れたいシーンから、7パート構成の台本を作ります")
    if not settings.anthropic_api_key:
        st.info("管理者へ: Secrets に ANTHROPIC_API_KEY を設定してください。")
    if st.session_state.get("_flash"):
        st.success(st.session_state.pop("_flash"))

    st.markdown("**① 資料（実在事件のメモ）**")
    mat_dir = sgen.DEFAULT_KIT / "資料"
    picks = sorted(p.name for p in mat_dir.glob("*.md")) if mat_dir.exists() else []
    if picks:
        c = st.columns([3, 1])
        pick = c[0].selectbox("資料から選ぶ", ["（手入力）"] + picks, key="pick",
                              label_visibility="collapsed")
        if c[1].button("読み込む", use_container_width=True, key="load",
                       disabled=pick == "（手入力）"):
            st.session_state["_pending_material"] = (mat_dir / pick).read_text(
                encoding="utf-8", errors="replace")
            st.rerun()
    st.text_area("資料テキスト", key="material", height=170,
                 label_visibility="collapsed",
                 placeholder="実在事件のメモを貼り付け、または上の資料から読み込み")

    st.markdown("**② 必ず入れるシーン（チェックポイント・1行に1つ）**")
    st.text_area("チェックポイント", key="checkpoints", height=110,
                 label_visibility="collapsed",
                 placeholder="例:\nあかりが大負けして「遠隔だ」と騒ぐ\n桐原が当時の新聞記事を出す")

    material = (st.session_state.get("material", "") or "").strip()
    cps = [ln for ln in (st.session_state.get("checkpoints", "") or "").splitlines()
           if ln.strip()]
    if st.button("📝 シナリオを生成", type="primary", use_container_width=True,
                 disabled=not (settings.anthropic_api_key and material)):
        prog = st.progress(0, text="準備中…")
        try:
            prog.progress(30, text="🧠 Claudeが7パート構成で執筆中…（数十秒〜数分）")
            md = sgen.generate_neme(settings, material, cps)
            st.session_state["result"] = md
            st.session_state["result_ver"] = st.session_state.get("result_ver", 0) + 1
            st.session_state["_flash"] = "シナリオを生成しました。下で確認・編集できます。"
            prog.progress(100, text="✅ 完了")
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"生成に失敗しました: {e}")
        finally:
            prog.empty()

    md = st.session_state.get("result", "")
    if md:
        st.divider()
        st.markdown("**生成結果（編集できます）**")
        ver = st.session_state.get("result_ver", 0)
        edited = st.text_area("台本(md)", value=md, key=f"edit_{ver}", height=400,
                              label_visibility="collapsed")
        st.download_button("⬇️ md保存", edited, file_name="scenario.md",
                           mime="text/markdown", use_container_width=True, key="dl")
        st.caption("音声化: 音声アプリの「📥 取り込み → 📝 台本(md)」にこの内容を貼り付け。")


main()
