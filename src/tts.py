"""eleven_v3 による音声合成: 個別clip(TTS) と シーン掛け合い(Text-to-Dialogue)。"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable

from elevenlabs.client import ElevenLabs

from .config import CLIPS_DIR, SCENES_DIR, CharacterBook, Settings
from .models import Character, Scene, Script

MODEL_ID = "eleven_v3"
# v3 の stability は離散値。文字列指定を API 値(float)に対応付ける。
STABILITY_MAP = {"creative": 0.0, "natural": 0.5, "robust": 1.0}
# Text-to-Dialogue 1リクエストあたりの目安文字数(超えたら分割)。
DIALOGUE_CHAR_BUDGET = 2500
MAX_RETRIES = 3


def _stability_value(name: str) -> float:
    return STABILITY_MAP.get((name or "natural").strip().lower(), 0.5)


def _collect_bytes(stream: Iterable[bytes] | bytes) -> bytes:
    if isinstance(stream, (bytes, bytearray)):
        return bytes(stream)
    return b"".join(chunk for chunk in stream if chunk)


def _with_retry(fn, label: str):
    """指数バックオフ付きでAPI呼び出しを再試行。"""
    delay = 2.0
    last: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - SDK例外を一括で扱う
            last = e
            if attempt == MAX_RETRIES:
                break
            print(f"    ! {label} 失敗(試行 {attempt}/{MAX_RETRIES}): {e} … {delay:.0f}s後に再試行")
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"{label} に失敗しました: {last}")


def _ext_for(output_format: str) -> str:
    if output_format.startswith("mp3"):
        return ".mp3"
    if output_format.startswith("pcm") or output_format.startswith("wav"):
        return ".wav"
    if output_format.startswith("ulaw"):
        return ".ulaw"
    return ".mp3"


def synth_clips(
    settings: Settings,
    script: Script,
    book: CharacterBook,
    scene_id: str | None = None,
    force: bool = False,
) -> list[Path]:
    """各セリフを個別ファイルに合成して書き出す。"""
    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    out_format = book.output_format
    ext = _ext_for(out_format)
    written: list[Path] = []

    scenes = [s for s in script.scenes if scene_id is None or s.id == scene_id]
    if not scenes:
        raise SystemExit(f"[synth] 対象シーンが見つかりません: {scene_id}")

    for scene in scenes:
        for idx, line in enumerate(scene.lines):
            char = _require_voice(book, line.speaker)
            safe_speaker = "".join(c for c in line.speaker if c.isalnum() or c in "-_") or "x"
            out = CLIPS_DIR / f"{scene.id}_{idx:03d}_{safe_speaker}{ext}"
            if out.exists() and not force:
                written.append(out)
                continue
            text = line.resolved_tts_text()
            if not text:
                continue
            print(f"[synth] {scene.id} #{idx:03d} {line.speaker} ({line.emotion}) -> {out.name}")
            audio = _with_retry(
                lambda: _collect_bytes(
                    client.text_to_speech.convert(
                        voice_id=char.voice_id,
                        model_id=MODEL_ID,
                        text=text,
                        output_format=out_format,
                        voice_settings={"stability": _stability_value(char.stability)},
                        **({"seed": char.seed} if char.seed is not None else {}),
                    )
                ),
                label=f"TTS {out.name}",
            )
            out.write_bytes(audio)
            written.append(out)

    print(f"[synth] 個別clip {len(written)} 件 -> {CLIPS_DIR}")
    return written


def synth_dialogue(
    settings: Settings,
    script: Script,
    book: CharacterBook,
    scene_id: str | None = None,
    force: bool = False,
) -> list[Path]:
    """シーン単位で Text-to-Dialogue を呼び、掛け合い音声を1ファイルに合成。"""
    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    out_format = book.output_format
    ext = _ext_for(out_format)
    written: list[Path] = []

    scenes = [s for s in script.scenes if scene_id is None or s.id == scene_id]
    for scene in scenes:
        out = SCENES_DIR / f"{scene.id}_dialogue{ext}"
        if out.exists() and not force:
            written.append(out)
            continue
        chunks = _chunk_dialogue(scene, book)
        if not chunks:
            continue
        print(f"[dialogue] {scene.id}: {len(scene.lines)} セリフ / {len(chunks)} リクエスト -> {out.name}")
        parts: list[bytes] = []
        for ci, inputs in enumerate(chunks):
            audio = _with_retry(
                lambda inputs=inputs: _collect_bytes(
                    client.text_to_dialogue.convert(
                        inputs=inputs,
                        model_id=MODEL_ID,
                        output_format=out_format,
                    )
                ),
                label=f"Dialogue {out.name} part{ci}",
            )
            parts.append(audio)
        out.write_bytes(b"".join(parts))
        written.append(out)

    print(f"[dialogue] シーン音声 {len(written)} 件 -> {SCENES_DIR}")
    return written


def _chunk_dialogue(scene: Scene, book: CharacterBook) -> list[list[dict[str, Any]]]:
    """シーンの発話を voice_id 付き inputs に変換し、文字数予算で分割する。"""
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for line in scene.lines:
        char = _require_voice(book, line.speaker)
        text = line.resolved_tts_text()
        if not text:
            continue
        if size + len(text) > DIALOGUE_CHAR_BUDGET and current:
            chunks.append(current)
            current, size = [], 0
        current.append({"text": text, "voice_id": char.voice_id})
        size += len(text)
    if current:
        chunks.append(current)
    return chunks


def _require_voice(book: CharacterBook, speaker: str) -> Character:
    char = book.get(speaker)
    if not char or not char.is_assigned():
        raise SystemExit(
            f"[synth] 話者「{speaker}」に voice_id が未割当です。"
            f"先に `cast --apply` を実行するか characters.json に記入してください。"
        )
    return char
