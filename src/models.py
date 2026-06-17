"""データモデル: 解析結果(Script/Scene/Line)とキャラ設定(Character)。

JSON との相互変換を持ち、各パイプライン段で中間ファイルとしてやり取りする。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Line:
    """1セリフ。Claude 解析が話者・感情・v3オーディオタグまで埋める。"""

    speaker: str
    text: str
    emotion: str = "neutral"
    audio_tags: list[str] = field(default_factory=list)
    delivery_note: str = ""
    tts_text: str = ""

    def resolved_tts_text(self) -> str:
        """合成に渡す本文。tts_text 未設定なら tags + text から組み立てる。"""
        if self.tts_text.strip():
            return self.tts_text.strip()
        prefix = " ".join(self.audio_tags).strip()
        return f"{prefix} {self.text}".strip() if prefix else self.text

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Line":
        return cls(
            speaker=str(d.get("speaker", "")).strip() or "ナレーター",
            text=str(d.get("text", "")).strip(),
            emotion=str(d.get("emotion", "neutral")).strip() or "neutral",
            audio_tags=[str(t).strip() for t in d.get("audio_tags", []) if str(t).strip()],
            delivery_note=str(d.get("delivery_note", "")).strip(),
            tts_text=str(d.get("tts_text", "")).strip(),
        )


@dataclass
class Scene:
    id: str
    description: str = ""
    lines: list[Line] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Scene":
        return cls(
            id=str(d.get("id", "")).strip() or "scene",
            description=str(d.get("description", "")).strip(),
            lines=[Line.from_dict(x) for x in d.get("lines", [])],
        )


@dataclass
class Script:
    title: str = ""
    language: str = "ja"
    characters: list[str] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)

    def all_speakers(self) -> list[str]:
        """登場する話者名を出現順で重複排除して返す。"""
        seen: dict[str, None] = {}
        for name in self.characters:
            seen.setdefault(name, None)
        for scene in self.scenes:
            for line in scene.lines:
                seen.setdefault(line.speaker, None)
        return list(seen.keys())

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "language": self.language,
            "characters": self.all_speakers(),
            "scenes": [
                {
                    "id": s.id,
                    "description": s.description,
                    "lines": [asdict(l) for l in s.lines],
                }
                for s in self.scenes
            ],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Script":
        return cls(
            title=str(d.get("title", "")).strip(),
            language=str(d.get("language", "ja")).strip() or "ja",
            characters=[str(c).strip() for c in d.get("characters", []) if str(c).strip()],
            scenes=[Scene.from_dict(x) for x in d.get("scenes", [])],
        )


@dataclass
class Character:
    """キャラ -> ElevenLabs voice の割当。characters.json の1エントリ。"""

    name: str
    voice_id: str = ""
    voice_name: str = ""
    description: str = ""
    gender: str = ""
    age: str = ""
    stability: str = "natural"
    seed: int | None = None

    def is_assigned(self) -> bool:
        return bool(self.voice_id.strip())

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "voice_id": self.voice_id,
            "voice_name": self.voice_name,
            "description": self.description,
            "gender": self.gender,
            "age": self.age,
            "stability": self.stability,
        }
        if self.seed is not None:
            d["seed"] = self.seed
        return d

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> "Character":
        seed = d.get("seed")
        return cls(
            name=name,
            voice_id=str(d.get("voice_id", "")).strip(),
            voice_name=str(d.get("voice_name", "")).strip(),
            description=str(d.get("description", "")).strip(),
            gender=str(d.get("gender", "")).strip(),
            age=str(d.get("age", "")).strip(),
            stability=str(d.get("stability", "natural")).strip() or "natural",
            seed=int(seed) if isinstance(seed, (int, str)) and str(seed).strip() else None,
        )
