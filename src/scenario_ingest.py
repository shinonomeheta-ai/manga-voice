"""ネーム指示書(markdown)の取り込み: scenario.json + 作画ブリーフ へ変換する。

パチンコ制作キット(assets/pachinko-manga)の `テンプレート/シナリオ出力形式.md` に沿った
ネーム指示書を、音声パイプライン用の scenario(話者×セリフ)と、作画ステージ用の
art_brief(ページ別【画】指示)に分解する。決定的パーサでオフライン動作・テスト可能。

想定フォーマット(要点):
    # 第NN話「タイトル」
    ## ログライン
    （一文）
    ## 登場人物
    - 語り手:（名前）
    - 当事者:（名前 / 立場 / 人物像）
    ## ページ別ネーム指示
    ### P1
    - コマ1:【画】描写 ／【ナレ】「テキスト」
    - コマ2:【画】… ／【セリフ・名前】「テキスト」
    ## 監修メモ
    ...
"""
from __future__ import annotations

import re

_QUOTE = re.compile(r"[「『](.+?)[」』]", re.S)
_TAGPAIR = re.compile(r"【([^】]+)】([^【／/]*)")
_PAGE = re.compile(r"^#{2,4}\s*P(\d+)\b", re.M)
_H1_TITLE = re.compile(r"^#\s*(.+)$", re.M)


# 読み物形式: 行頭「話者名「セリフ」」を話者×セリフに分解するための正規表現。
_SCRIPT_DLG = re.compile(r"^\s*([^「」【】\n]{1,20}?)\s*[:：]?\s*「(.+)」\s*[。.!！?？]?\s*$")


def parse_script(md: str) -> dict:
    """読み物形式の台本(話者「…」＋地の文)を scenario(話者×セリフ)へ変換する。

    - 行頭「話者名「セリフ」」→ そのキャラのセリフ。
    - それ以外の本文行(地の文・ナレーション)→ ナレーター。
    - 見出し/メタ/監修メモ/箇条書きは除外。
    生成側 OUTPUT_SPEC(読み物形式)の出力をそのまま音声化に繋ぐためのパーサ。
    """
    out: list[dict] = []
    in_memo = False
    for raw in _lines(md):
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^#{1,6}\s", line):  # 見出し
            in_memo = "監修メモ" in line
            continue
        if in_memo:
            continue
        if line.startswith(("---", "**", "- ", "* ", ">")):  # 区切り/メタ/箇条書き/引用
            continue
        m = _SCRIPT_DLG.match(line)
        if m:
            speaker = re.sub(r"[（(].*?[)）]", "", m.group(1)).strip()  # （焦って）等を除去
            out.append({"speaker": speaker or "ナレーター", "text": m.group(2).strip()})
        else:
            out.append({"speaker": "ナレーター", "text": line})
    scenes = [{"id": "S1", "setting": "", "lines": out}] if out else []
    return {"version": "1.0", "title": _title(md), "logline": "",
            "language": "ja", "characters": [], "scenes": scenes}


def parse_neme(md: str) -> tuple[dict, list[dict]]:
    """ネーム markdown を (scenario_dict, art_brief_pages) に変換する。"""
    title = _title(md)
    logline = _first_nonempty(_section(md, "ログライン"))
    characters = _parse_characters(_section(md, "登場人物"))
    scenes, art_brief = _parse_pages(md)
    scenario = {
        "version": "1.0",
        "title": title,
        "logline": logline,
        "language": "ja",
        "characters": characters,
        "scenes": scenes,
    }
    return scenario, art_brief


# --- セクション抽出 ---

def _lines(md: str) -> list[str]:
    return md.replace("\r\n", "\n").split("\n")


def _heading_level(line: str) -> int:
    m = re.match(r"^(#{1,6})\s", line)
    return len(m.group(1)) if m else 0


def _section(md: str, keyword: str) -> list[str]:
    """見出しに keyword を含む節の本文行を、次の同レベル以上の見出しまで返す。"""
    out: list[str] = []
    capturing = False
    cap_level = 0
    for line in _lines(md):
        lvl = _heading_level(line)
        if lvl and keyword in line:
            capturing = True
            cap_level = lvl
            continue
        if capturing and lvl and lvl <= cap_level:
            break
        if capturing:
            out.append(line)
    return out


def _first_nonempty(rows: list[str]) -> str:
    for r in rows:
        s = re.sub(r"^[（(]|[）)]$", "", r.strip())
        if s:
            return s.strip()
    return ""


def _title(md: str) -> str:
    m = _H1_TITLE.search(md)
    if not m:
        return ""
    raw = m.group(1).strip()
    q = _QUOTE.search(raw)
    return q.group(1).strip() if q else raw


# --- 登場人物 ---

def _parse_characters(rows: list[str]) -> list[dict]:
    chars: list[dict] = []
    for r in rows:
        m = re.match(r"^\s*[-*]\s*(.+)$", r)
        if not m:
            continue
        body = m.group(1).strip()
        role, _, rest = body.partition(":")
        if not rest:
            role, _, rest = body.partition("：")
        inside = _strip_parens(rest.strip()) or role.strip()
        name = re.split(r"[/／,、]", inside)[0].strip()
        if not name:
            continue
        chars.append({"name": name, "description": inside})
    return chars


def _strip_parens(s: str) -> str:
    s = s.strip()
    m = re.match(r"^[（(](.*)[）)]$", s)
    return m.group(1).strip() if m else s


# --- ページ別ネーム ---

def _parse_pages(md: str) -> tuple[list[dict], list[dict]]:
    rows = _section(md, "ページ別ネーム")
    scenes: list[dict] = []
    art_pages: list[dict] = []
    cur_page: str | None = None
    cur_lines: list[dict] = []
    cur_panels: list[dict] = []
    panel_no = 0

    def flush():
        nonlocal cur_lines, cur_panels
        if cur_page is None:
            return
        scenes.append({"id": f"P{cur_page}", "setting": "", "lines": cur_lines})
        art_pages.append({"page": int(cur_page), "panels": cur_panels})
        cur_lines, cur_panels = [], []

    for line in rows:
        pm = _PAGE.match(line)
        if pm:
            flush()
            cur_page = pm.group(1)
            panel_no = 0
            continue
        if cur_page is None:
            continue
        pairs = _TAGPAIR.findall(line)
        if not pairs:
            continue
        panel_no += 1
        for tag, content in pairs:
            content = content.strip().strip("／/").strip()
            if "画" in tag:
                desc = _strip_parens(content)
                if desc:
                    cur_panels.append({"panel": panel_no, "art": desc})
            elif "ナレ" in tag or "セリフ" in tag or "台詞" in tag:
                text = _quote_or_text(content)
                if not text:
                    continue
                speaker = _speaker_from_tag(tag)
                cur_lines.append({"speaker": speaker, "text": text})
    flush()
    return scenes, art_pages


def _quote_or_text(content: str) -> str:
    q = _QUOTE.search(content)
    return (q.group(1) if q else content).strip()


def _speaker_from_tag(tag: str) -> str:
    """【ナレ】→ナレーター / 【セリフ・名前】→名前 / 【セリフ】→空(後段で同定)。"""
    if "ナレ" in tag:
        return "ナレーター"
    for sep in ("・", "：", ":", "／", "/"):
        if sep in tag:
            cand = tag.split(sep, 1)[1].strip()
            if cand and "セリフ" not in cand and "台詞" not in cand:
                return cand
    return ""
