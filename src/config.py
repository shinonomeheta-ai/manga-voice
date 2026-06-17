"""設定の読み込み: .env (APIキー/モデル) と characters.json (キャラ割当)。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .models import Character

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"

# プロジェクトルート (このファイルの2つ上 = src/ の親)
ROOT = Path(__file__).resolve().parent.parent
CHARACTERS_PATH = ROOT / "config" / "characters.json"
ASSETS_DIR = ROOT / "assets" / "characters"
INPUTS_DIR = ROOT / "inputs"
OUTPUT_DIR = ROOT / "output"
SCRIPT_PATH = OUTPUT_DIR / "script.json"
CLIPS_DIR = OUTPUT_DIR / "clips"
SCENES_DIR = OUTPUT_DIR / "scenes"


@dataclass
class Settings:
    anthropic_api_key: str
    elevenlabs_api_key: str
    model: str = DEFAULT_MODEL
    notion_token: str = ""


def load_settings(model: str | None = None) -> Settings:
    """環境変数からAPIキー等を読む(検証はしない)。

    検証は各コマンドが必要なキーだけ require_* で行う。これにより
    fetch-notion を ElevenLabs/Anthropic キーなしで実行したり、dry-run を
    キーなしで回したりできる。
    """
    load_dotenv(ROOT / ".env")
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", "").strip(),
        model=(model or os.getenv("MANGA_VOICE_MODEL") or DEFAULT_MODEL).strip(),
        notion_token=os.getenv("NOTION_TOKEN", "").strip(),
    )


def _require(value: str, env_name: str, hint: str) -> str:
    if not value:
        raise SystemExit(
            f"{env_name} が未設定です。{hint}\n"
            f".env.example をコピーして .env を作成し、キーを記入してください。"
        )
    return value


def require_anthropic(settings: Settings) -> str:
    return _require(settings.anthropic_api_key, "ANTHROPIC_API_KEY",
                    "漫画/シナリオの解析に必要です。")


def require_elevenlabs(settings: Settings) -> str:
    return _require(settings.elevenlabs_api_key, "ELEVENLABS_API_KEY",
                    "音声合成(eleven_v3)に必要です。")


def require_notion_token(settings: Settings) -> str:
    return _require(settings.notion_token, "NOTION_TOKEN",
                    "Notion インテグレーションのトークンを設定してください。")


@dataclass
class CharacterBook:
    """characters.json 全体。defaults とキャラ辞書を保持し書き戻しできる。"""

    defaults: dict[str, Any] = field(default_factory=dict)
    characters: dict[str, Character] = field(default_factory=dict)
    path: Path = CHARACTERS_PATH

    @property
    def language(self) -> str:
        return str(self.defaults.get("language", "ja"))

    @property
    def output_format(self) -> str:
        return str(self.defaults.get("output_format", DEFAULT_OUTPUT_FORMAT))

    @property
    def default_stability(self) -> str:
        return str(self.defaults.get("stability", "natural"))

    def get(self, name: str) -> Character | None:
        return self.characters.get(name)

    def ensure(self, name: str) -> Character:
        """未登録キャラを defaults を引き継いで新規作成しつつ取得する。"""
        if name not in self.characters:
            self.characters[name] = Character(
                name=name, stability=self.default_stability
            )
        return self.characters[name]

    def save(self) -> None:
        data = {
            "_comment": "キャラ名 -> ElevenLabs voice の対応表。cast --apply で自動補完されます。",
            "defaults": self.defaults,
            "characters": {n: c.to_dict() for n, c in self.characters.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path = CHARACTERS_PATH) -> "CharacterBook":
        if not path.exists():
            return cls(defaults={"language": "ja", "stability": "natural",
                                 "output_format": DEFAULT_OUTPUT_FORMAT}, path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        chars = {
            name: Character.from_dict(name, d)
            for name, d in raw.get("characters", {}).items()
        }
        return cls(defaults=raw.get("defaults", {}), characters=chars, path=path)


def ensure_dirs() -> None:
    for d in (INPUTS_DIR, OUTPUT_DIR, CLIPS_DIR, SCENES_DIR):
        d.mkdir(parents=True, exist_ok=True)
