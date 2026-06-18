"""キャラバイブル: 事前に用意したキャラ設定資料(顔画像+プロフィール)を読み込む。

`assets/characters/` に次を置くと、解析(analyze)の精度が上がる:
- `<キャラ名>.png|jpg|...`  … 顔リファレンス(漫画コマの人物同定に使う)
- `<キャラ名>.txt|md`       … プロフィール(性格・口調・声質など。任意)

プロフィールは characters.json の description ともマージされる。画像が無く
テキストだけ、テキストが無く画像だけ、のどちらでも可。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import CharacterBook

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PROFILE_EXT = [".md", ".txt"]


@dataclass
class CharacterAsset:
    name: str
    image_path: Path | None = None
    profile: str = ""


def load_character_bible(assets_dir: Path, book: CharacterBook | None = None) -> list[CharacterAsset]:
    """assets_dir と characters.json からキャラ設定資料を集約する。"""
    names: dict[str, CharacterAsset] = {}

    if assets_dir.exists():
        for p in sorted(assets_dir.iterdir()):
            if not p.is_file() or p.name.startswith("."):
                continue
            stem = p.stem
            if stem.lower() == "readme":  # フォルダ説明はキャラ扱いしない
                continue
            asset = names.setdefault(stem, CharacterAsset(name=stem))
            ext = p.suffix.lower()
            if ext in IMAGE_EXT and asset.image_path is None:
                asset.image_path = p
            elif ext in (".md", ".txt"):
                text = p.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    asset.profile = (asset.profile + "\n" + text).strip() if asset.profile else text

    # characters.json の description を補完(資料に無い説明を拾う)
    if book:
        for name, char in book.characters.items():
            if not char.description:
                continue
            asset = names.setdefault(name, CharacterAsset(name=name))
            if char.description not in asset.profile:
                asset.profile = (asset.profile + "\n" + char.description).strip() if asset.profile else char.description

    return [a for a in names.values() if a.image_path or a.profile]
