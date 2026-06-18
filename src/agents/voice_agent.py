"""音声エージェント: 既存の解析/キャスティング/合成を run_dir 基準でラップする。

3ステージに対応:
- voice.analyze : 作画(art)+シナリオ から script.json を生成
- voice.cast    : 話者に ElevenLabs ボイスを割当
- voice.synth   : eleven_v3 で音声合成(ctx.dry_run なら課金なしの計画のみ)

成果物は run_dir/voice/ に置く。API は遅延importの既存関数に委譲する。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .. import assets as assets_mod
from .. import tts as tts_mod
from .. import voices as voices_mod
from ..analyze import IMAGE_EXT, analyze as analyze_inputs
from ..config import CharacterBook, require_anthropic, require_elevenlabs
from ..models import Script
from ..works import work_of
from .base import AgentResult, RunContext


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _scenario_to_text(scenario: dict) -> str:
    """scenario.json を解析に渡す台本テキストへ整形する。"""
    lines = [f"# {scenario.get('title', '')}".rstrip()]
    if scenario.get("logline"):
        lines.append(f"ログライン: {scenario['logline']}")
    for sc in scenario.get("scenes", []):
        lines.append(f"\n【{sc.get('id', '')}】 {sc.get('setting', '')}".rstrip())
        for ln in sc.get("lines", []):
            direction = f"（{ln['direction']}）" if ln.get("direction") else ""
            lines.append(f"{ln.get('speaker', '')}：{direction}{ln.get('text', '')}")
    return "\n".join(lines) + "\n"


class VoiceAnalyzeAgent:
    name = "voice.analyze"

    def run(self, ctx: RunContext) -> AgentResult:
        require_anthropic(ctx.settings)
        voice_dir = ctx.stage_dir("voice")
        inputs_dir = voice_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)

        # 1) シナリオ本文(あれば)を台本テキスト化
        scenario_path = ctx.run_dir / "scenario" / "scenario.json"
        if scenario_path.exists():
            (inputs_dir / "scenario.txt").write_text(
                _scenario_to_text(_read_json(scenario_path)), encoding="utf-8")

        # 2) 作画ページ画像を集める(art.json があれば順序を尊重)
        n_img = self._gather_art_images(ctx.run_dir, inputs_dir)

        if n_img == 0 and not (inputs_dir / "scenario.txt").exists():
            return AgentResult.needs_input(
                "解析対象がありません。先に scenario / art ステージを完了させてください。")

        work = work_of(ctx.options)
        book = CharacterBook.load(work.characters_path)
        bible = assets_mod.load_character_bible(work.assets_dir, book)
        script = analyze_inputs(ctx.settings, inputs_dir, language=book.language,
                                character_bible=bible)
        out = voice_dir / "script.json"
        _write_json(out, script.to_dict())
        return AgentResult.ok([ctx.rel(out)],
                              message=f"{len(script.scenes)}シーン / 話者 {script.all_speakers()}")

    @staticmethod
    def _gather_art_images(run_dir: Path, inputs_dir: Path) -> int:
        art_dir = run_dir / "art"
        manifest = art_dir / "art.json"
        images: list[Path] = []
        if manifest.exists():
            for page in _read_json(manifest).get("pages", []):
                p = run_dir / page["image"]
                if p.exists():
                    images.append(p)
        else:
            for p in sorted(art_dir.rglob("*")):
                if p.is_file() and p.suffix.lower() in IMAGE_EXT:
                    images.append(p)
        for i, src in enumerate(images):
            shutil.copy2(src, inputs_dir / f"page_{i:03d}{src.suffix.lower()}")
        return len(images)


class VoiceCastAgent:
    name = "voice.cast"

    def run(self, ctx: RunContext) -> AgentResult:
        require_anthropic(ctx.settings)
        require_elevenlabs(ctx.settings)
        voice_dir = ctx.stage_dir("voice")
        script = Script.from_dict(_read_json(voice_dir / "script.json"))
        book = CharacterBook.load(work_of(ctx.options).characters_path)
        book = voices_mod.cast(ctx.settings, script, book, apply=True)
        cast_out = voice_dir / "cast.json"
        _write_json(cast_out, {"characters": {n: c.to_dict() for n, c in book.characters.items()}})
        unassigned = [s for s in script.all_speakers()
                      if not (book.get(s) and book.get(s).is_assigned())]
        if unassigned:
            return AgentResult.error(f"未割当の話者が残っています: {', '.join(unassigned)}")
        return AgentResult.ok([ctx.rel(cast_out)], message="全話者に割当完了")


class VoiceSynthAgent:
    name = "voice.synth"

    def run(self, ctx: RunContext) -> AgentResult:
        if not ctx.dry_run:
            require_elevenlabs(ctx.settings)
        voice_dir = ctx.stage_dir("voice")
        script = Script.from_dict(_read_json(voice_dir / "script.json"))
        book = CharacterBook.load(work_of(ctx.options).characters_path)
        dialogue = bool(ctx.options.get("dialogue", False))

        tts_mod.synth_clips(ctx.settings, script, book, dry_run=ctx.dry_run)
        if dialogue:
            tts_mod.synth_dialogue(ctx.settings, script, book, dry_run=ctx.dry_run)

        manifest = self._manifest(script, book, dialogue, ctx.dry_run)
        out = voice_dir / "voice_output.json"
        _write_json(out, manifest)
        note = "（dry-run: 計画のみ・課金なし）" if ctx.dry_run else ""
        return AgentResult.ok([ctx.rel(out)],
                              message=f"clip {len(manifest['clips'])}件{note}")

    @staticmethod
    def _manifest(script: Script, book: CharacterBook, dialogue: bool, dry_run: bool) -> dict:
        ext = tts_mod._ext_for(book.output_format)
        clips = []
        for scene in script.scenes:
            for idx, line in enumerate(scene.lines):
                ch = book.get(line.speaker)
                clips.append({
                    "scene": scene.id, "index": idx, "speaker": line.speaker,
                    "voice_id": (ch.voice_id if ch else "") or "UNASSIGNED",
                    "file": f"clips/{scene.id}_{idx:03d}_{tts_mod._safe(line.speaker)}{ext}",
                })
        scenes = ([{"scene": s.id, "file": f"scenes/{s.id}_dialogue{ext}"} for s in script.scenes]
                  if dialogue else [])
        return {"version": "1.0", "model_id": tts_mod.MODEL_ID, "dry_run": dry_run,
                "clips": clips, "scenes": scenes}
