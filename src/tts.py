"""eleven_v3 による音声合成: 個別clip(TTS) と シーン掛け合い(Text-to-Dialogue)。

--dry-run では実APIを呼ばず、合成計画を manifest(JSON)に書き出す。これにより
ElevenLabs に課金せずに「誰が・どの voice_id で・何文字・どんなタグで」喋るかを
事前確認できる。ElevenLabs SDK は dry-run 時に import しないよう遅延読み込みする。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

from .config import CLIPS_DIR, OUTPUT_DIR, SCENES_DIR, CharacterBook, Settings
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


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "-_") or "x"


def _select_scenes(script: Script, scene_id: str | None) -> list[Scene]:
    scenes = [s for s in script.scenes if scene_id is None or s.id == scene_id]
    if not scenes:
        raise SystemExit(f"対象シーンが見つかりません: {scene_id}")
    return scenes


def synth_clips(
    settings: Settings,
    script: Script,
    book: CharacterBook,
    scene_id: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> list[Path]:
    """各セリフを個別ファイルに合成して書き出す(dry_runなら計画のみ)。"""
    out_format = book.output_format
    ext = _ext_for(out_format)
    scenes = _select_scenes(script, scene_id)

    if dry_run:
        return _dryrun_clips(scenes, book, ext, out_format)

    from elevenlabs.client import ElevenLabs  # 遅延import(キー必須の本番のみ)

    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for scene in scenes:
        for idx, line in enumerate(scene.lines):
            char = _require_voice(book, line.speaker)
            out = CLIPS_DIR / f"{scene.id}_{idx:03d}_{_safe(line.speaker)}{ext}"
            if out.exists() and not force:
                written.append(out)
                continue
            text = line.resolved_tts_text()
            if not text:
                continue
            print(f"[synth] {scene.id} #{idx:03d} {line.speaker} ({line.emotion}) -> {out.name}")
            audio = _with_retry(
                lambda char=char, text=text: _collect_bytes(
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
    dry_run: bool = False,
) -> list[Path]:
    """シーン単位で Text-to-Dialogue を呼び掛け合い音声を合成(dry_runなら計画のみ)。"""
    out_format = book.output_format
    ext = _ext_for(out_format)
    scenes = _select_scenes(script, scene_id)

    if dry_run:
        return _dryrun_dialogue(scenes, book, ext)

    from elevenlabs.client import ElevenLabs  # 遅延import

    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

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


# --- dry-run の計画作成 -------------------------------------------------

def _dryrun_clips(scenes: list[Scene], book: CharacterBook, ext: str, out_format: str) -> list[Path]:
    entries: list[dict[str, Any]] = []
    unassigned: set[str] = set()
    total_chars = 0
    for scene in scenes:
        for idx, line in enumerate(scene.lines):
            char = book.get(line.speaker)
            assigned = bool(char and char.is_assigned())
            if not assigned:
                unassigned.add(line.speaker)
            text = line.resolved_tts_text()
            total_chars += len(text)
            entries.append({
                "scene": scene.id,
                "index": idx,
                "speaker": line.speaker,
                "voice_id": (char.voice_id if char else "") or "UNASSIGNED",
                "emotion": line.emotion,
                "audio_tags": line.audio_tags,
                "stability": (char.stability if char else book.default_stability),
                "chars": len(text),
                "output": f"{scene.id}_{idx:03d}_{_safe(line.speaker)}{ext}",
            })
    manifest = {
        "mode": "dry-run",
        "model_id": MODEL_ID,
        "output_format": out_format,
        "total_clips": len(entries),
        "total_chars": total_chars,
        "unassigned_speakers": sorted(unassigned),
        "clips": entries,
    }
    path = _write_manifest("dryrun_clips.json", manifest)
    print(f"[dry-run] 個別clip 計画: {len(entries)} 件 / 合計 {total_chars} 文字 -> {path.name}")
    if unassigned:
        print(f"[dry-run] ⚠ voice_id 未割当の話者: {', '.join(sorted(unassigned))} "
              f"(本番前に `cast --apply` か characters.json で割当を)")
    return [path]


def _dryrun_dialogue(scenes: list[Scene], book: CharacterBook, ext: str) -> list[Path]:
    scenes_plan: list[dict[str, Any]] = []
    for scene in scenes:
        chunks = _chunk_dialogue(scene, book, tolerant=True)
        scenes_plan.append({
            "scene": scene.id,
            "output": f"{scene.id}_dialogue{ext}",
            "requests": len(chunks),
            "lines": sum(len(c) for c in chunks),
            "chars": sum(len(i["text"]) for c in chunks for i in c),
        })
    manifest = {"mode": "dry-run", "model_id": MODEL_ID,
                "char_budget": DIALOGUE_CHAR_BUDGET, "scenes": scenes_plan}
    path = _write_manifest("dryrun_dialogue.json", manifest)
    total_req = sum(s["requests"] for s in scenes_plan)
    print(f"[dry-run] dialogue 計画: {len(scenes_plan)} シーン / {total_req} リクエスト -> {path.name}")
    return [path]


def _write_manifest(name: str, data: dict[str, Any]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --- 共有ヘルパ ---------------------------------------------------------

def _chunk_dialogue(
    scene: Scene, book: CharacterBook, tolerant: bool = False
) -> list[list[dict[str, Any]]]:
    """シーンの発話を voice_id 付き inputs に変換し、文字数予算で分割する。

    tolerant=True(dry-run)では未割当でも UNASSIGNED として継続する。
    """
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for line in scene.lines:
        if tolerant:
            ch = book.get(line.speaker)
            voice_id = (ch.voice_id if ch else "") or "UNASSIGNED"
        else:
            voice_id = _require_voice(book, line.speaker).voice_id
        text = line.resolved_tts_text()
        if not text:
            continue
        if size + len(text) > DIALOGUE_CHAR_BUDGET and current:
            chunks.append(current)
            current, size = [], 0
        current.append({"text": text, "voice_id": voice_id})
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
