"""自動シナリオ生成: 資料(実在事件)＋チェックポイント → 7パートのネーム指示書(md)。

作品キット(assets/pachinko-manga)の設定・出力形式・鉄則を読み込み、Claudeに渡して
『ななしちゃん@がんばらない』形式のネーム指示書を生成する。出力は
scenario_ingest.parse_neme が読める形式(## ページ別ネーム指示 / ### P1 /
コマN:【画】…／【セリフ・名前】「…」 / ## 監修メモ)に揃え、`pipeline --neme` で
そのまま音声化に繋がるようにする。

- プロンプト組み立て(load_kit/build_system/build_user)は純関数 → オフラインでテスト可。
- generate_neme() だけが Anthropic を呼ぶ(遅延import・キーは生成時のみ必要)。
"""
from __future__ import annotations

from pathlib import Path

from .config import ROOT, Settings, require_anthropic

DEFAULT_KIT = ROOT / "assets" / "pachinko-manga"

# キット内ファイル(プロンプトに載せる順)。無いものはスキップして落とさない。
KIT_FILES: list[tuple[str, str]] = [
    ("作品コンセプト", "設定/作品コンセプト.md"),
    ("キャラクター設定", "設定/キャラクター設定.md"),
    ("パチンコ知識メモ", "設定/パチンコ知識メモ.md"),
    ("制作の鉄則", "CLAUDE.md"),
    ("出力形式(7パート)", "テンプレート/シナリオ出力形式.md"),
]

# 読み物（小説・脚本のように読める台本）で出力するためのフォーマット指定。
OUTPUT_SPEC = """# 出力フォーマット（読み物として読める台本）

漫画のコマ割り・ページ割りは考えず、小説や脚本のように通して読める台本にする。
次の見出し構造の markdown を、この順で過不足なく出力すること（前置き・説明・
コードフェンスは禁止、本文のみ）。

# 第NN話「タイトル」

**ログライン**: （この回が何を見せる話かを一文で）
**登場キャラ**: ななし / あかり / 桐原 / 九重 / 九頭竜（出る人だけ）

---

## ① ツカミ
（情景・状況・心情を地の文で描写する。読んで場面が浮かぶ文章で書く。）
あかり「セリフ本文」
ななし「セリフ本文」
（地の文を続けてよい）

## ② 疑問
…

## ③ 相談
…

## ④ 解説
（桐原が元業界紙記者として実在事件を紹介。手口は教則化しない。）
桐原「……」

## ⑤ 事件
（九重・九頭竜らの再現ドラマ。地の文＋セリフで。）

## ⑥ 現代に戻る
…

## ⑦ オチ
…

---

## 監修メモ
- モデルにした実在事件:
- 架空名への置換: （実 → 架空。店舗・人物・メーカー）
- 創作・脚色した点:
- 要・事実確認: （年号・数字・固有名詞など裏取りが必要な箇所）
- 配慮した点: （教則化回避／名誉毀損回避／依存症の扱い／反社の扱い）

書き方のルール:
- セリフは必ず「話者名「セリフ本文」」の形（行頭に話者名、続けて「」でくくる。1行1セリフ）。
- 地の文（ナレーション・情景・心情）は普通の文章で書く（【】などの記号タグは使わない）。
- 7パート構成(①ツカミ→②疑問→③相談→④解説→⑤事件→⑥現代→⑦オチ)を必ず満たす。
- 実在事件は桐原の解説として扱い、店舗・人物・メーカー名は架空名に置換する。
- 不正の手口を教則化しない。未確認の事実は断定せず監修メモに「要・事実確認」で残す。
"""

SYSTEM_INTRO = (
    "あなたは『ななしちゃん@がんばらない』のシナリオライター兼ネーム作家です。"
    "以下の作品設定・制作の鉄則・出力形式を厳守し、与えられた実在事件の資料を"
    "桐原の解説として作品化したネーム指示書を書きます。"
)


def load_kit(kit_dir: Path = DEFAULT_KIT) -> str:
    """作品キットの設定群を1つのテキストに連結する(無いファイルはスキップ)。"""
    parts: list[str] = []
    for label, rel in KIT_FILES:
        p = kit_dir / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            parts.append(f"# 【{label}】\n{text}")
    return "\n\n".join(parts)


def build_system(kit_text: str) -> str:
    """システムプロンプト = 役割 + 作品キット + 出力フォーマット厳守ルール。"""
    return f"{SYSTEM_INTRO}\n\n{kit_text}\n\n{OUTPUT_SPEC}"


def build_user(material: str, checkpoints: list[str]) -> str:
    """ユーザープロンプト = 資料(実在事件) + チェックポイント(必ず入れるシーン)。"""
    material = (material or "").strip()
    cps = [c.strip() for c in (checkpoints or []) if c and c.strip()]
    block = ""
    if cps:
        lst = "\n".join(f"- {c}" for c in cps)
        block = (
            "\n\n# 必ず入れるシーン（チェックポイント）\n"
            "以下のシーン/ビートは“関所”として必ず通過すること。実在事件の流れを軸に、"
            "これらを自然に繋いで7パートへ配置する（順番は事件の流れに合わせて調整可）。\n"
            f"{lst}"
        )
    return f"# 題材の資料（実在事件のメモ）\n{material}{block}"


def generate_neme(
    settings: Settings,
    material: str,
    checkpoints: list[str] | None = None,
    *,
    kit_dir: Path = DEFAULT_KIT,
    model: str | None = None,
    max_tokens: int = 32000,
    timeout: float = 600.0,
) -> str:
    """資料＋チェックポイントから7パートのネーム指示書(markdown)を生成して返す。"""
    require_anthropic(settings)
    from anthropic import Anthropic  # 遅延import(生成時のみ)

    client = Anthropic(api_key=settings.anthropic_api_key, timeout=timeout, max_retries=1)
    system = build_system(load_kit(kit_dir))
    user = build_user(material, checkpoints or [])
    mdl = model or getattr(settings, "model", "claude-opus-4-8")
    # 長文出力＋構成の推論が要るためストリーミング＋adaptive thinking。
    with client.messages.stream(
        model=mdl,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        msg = stream.get_final_message()
    return "".join(
        b.text for b in msg.content if getattr(b, "type", None) == "text"
    ).strip()
