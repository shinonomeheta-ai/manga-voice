"""作品(work)ごとの設定分離。

1作品 = キャラ割当(characters.json) + キャラバイブル(assets/characters/) +
シナリオ制作ルール(scenario_rules.md) のまとまり。`works/<名前>/` に置く。

run は options["work"] に作品名を持ち、各エージェントはその作品の設定を読む。
作品名が無い場合は従来のグローバル設定(config/, assets/characters/)を既定作品として使う。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import (
    ASSETS_DIR,
    CHARACTERS_PATH,
    DEFAULT_OUTPUT_FORMAT,
    SCENARIO_RULES_PATH,
    WORKS_DIR,
)

DEFAULT_WORK = "(default)"

_CHARACTERS_TEMPLATE = """{
  "_comment": "この作品のキャラ -> ElevenLabs voice 対応表。cast --apply で自動補完されます。",
  "defaults": {"language": "ja", "stability": "natural", "output_format": "%s"},
  "characters": {
    "ナレーター": {"voice_id": "", "voice_name": "", "description": "落ち着いた語り手",
                   "gender": "neutral", "age": "middle_aged", "stability": "robust", "seed": 1001}
  }
}
""" % DEFAULT_OUTPUT_FORMAT

_RULES_TEMPLATE = """# シナリオ制作ルール（この作品用）

文体・構成・世界観・禁止事項など「毎回守ること」を書いてください。
（ここに制作ルールを書いてください ← この目印を残すと未設定として無視されます）
"""


@dataclass
class Work:
    name: str
    characters_path: Path
    assets_dir: Path
    rules_path: Path
    base: Path | None = None  # 既定作品は None


def resolve_work(name: str | None) -> Work:
    """作品名から設定パスを解決。None/既定はグローバル設定を指す。"""
    if not name or name == DEFAULT_WORK:
        return Work(DEFAULT_WORK, CHARACTERS_PATH, ASSETS_DIR, SCENARIO_RULES_PATH, None)
    base = WORKS_DIR / name
    if not base.exists():
        raise SystemExit(
            f"作品が見つかりません: {name}（works/ 配下に無い）。"
            f"`pipeline works` で一覧、`pipeline init-work {name}` で作成できます。")
    return Work(name, base / "characters.json", base / "assets" / "characters",
                base / "scenario_rules.md", base)


def list_works() -> list[str]:
    if not WORKS_DIR.exists():
        return []
    return sorted(p.name for p in WORKS_DIR.iterdir()
                  if p.is_dir() and not p.name.startswith("."))


def init_work(name: str) -> Work:
    """works/<name>/ を雛形付きで作成する(既存ファイルは保持)。"""
    base = WORKS_DIR / name
    (base / "assets" / "characters").mkdir(parents=True, exist_ok=True)
    cj = base / "characters.json"
    if not cj.exists():
        cj.write_text(_CHARACTERS_TEMPLATE, encoding="utf-8")
    rl = base / "scenario_rules.md"
    if not rl.exists():
        rl.write_text(_RULES_TEMPLATE, encoding="utf-8")
    return resolve_work(name)


def work_of(options: dict) -> Work:
    """エージェントの ctx.options から作品を解決するショートカット。"""
    return resolve_work(options.get("work"))
