"""キャスティング: ElevenLabs のボイスからキャラに合う声を割り当てる。

戦略:
- まずアカウント内のボイス(プリメイド含む)を候補にする。これらは voice_id で
  そのまま TTS に使えるため確実。
- 候補をキャラ説明と突き合わせ、Claude に最適な1つを選ばせる。
- --apply で characters.json に書き戻す。
"""
from __future__ import annotations

from typing import Any

from anthropic import Anthropic
from elevenlabs.client import ElevenLabs

from .config import CharacterBook, Settings
from .models import Character, Script
from .prompts import VOICE_SELECT_TOOL, voice_selection_prompt


def _voice_field(v: Any, *names: str) -> str:
    """SDK のボイスオブジェクト/辞書から最初に見つかったフィールドを文字列で返す。"""
    for n in names:
        val = getattr(v, n, None)
        if val is None and isinstance(v, dict):
            val = v.get(n)
        if val:
            return str(val)
    return ""


def _labels(v: Any) -> dict[str, Any]:
    labels = getattr(v, "labels", None)
    if labels is None and isinstance(v, dict):
        labels = v.get("labels")
    return labels or {}


def fetch_account_voices(client: ElevenLabs) -> list[Any]:
    """アカウントで利用可能なボイス一覧(プリメイド含む)を取得。"""
    resp = client.voices.get_all()
    voices = getattr(resp, "voices", None)
    if voices is None and isinstance(resp, dict):
        voices = resp.get("voices")
    return list(voices or [])


def _candidate_block(voices: list[Any]) -> str:
    lines: list[str] = []
    for v in voices:
        vid = _voice_field(v, "voice_id", "voiceId")
        name = _voice_field(v, "name")
        desc = _voice_field(v, "description")
        labels = _labels(v)
        label_str = ", ".join(f"{k}={val}" for k, val in labels.items()) if labels else ""
        parts = [p for p in (name, desc, label_str) if p]
        lines.append(f"- {vid} / " + " / ".join(parts))
    return "\n".join(lines)


def _character_desc(char: Character) -> str:
    bits = [f"名前: {char.name}"]
    if char.description:
        bits.append(f"特徴: {char.description}")
    if char.gender:
        bits.append(f"性別: {char.gender}")
    if char.age:
        bits.append(f"年齢: {char.age}")
    return "\n".join(bits)


def _select_voice_for(
    anthropic: Anthropic, model: str, char: Character, candidate_block: str,
    voices_by_id: dict[str, Any],
) -> tuple[str, str, str]:
    """Claude に候補から1つ選ばせ (voice_id, voice_name, reason) を返す。"""
    resp = anthropic.messages.create(
        model=model,
        max_tokens=500,
        tools=[VOICE_SELECT_TOOL],
        tool_choice={"type": "tool", "name": "select_voice"},
        messages=[{
            "role": "user",
            "content": voice_selection_prompt(_character_desc(char), candidate_block),
        }],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "select_voice":
            vid = str(block.input.get("voice_id", "")).strip()
            reason = str(block.input.get("reason", "")).strip()
            name = _voice_field(voices_by_id.get(vid), "name") if vid in voices_by_id else ""
            return vid, name, reason
    return "", "", ""


def cast(
    settings: Settings,
    script: Script,
    book: CharacterBook,
    apply: bool = False,
) -> CharacterBook:
    """script の各話者に voice を割り当て、未割当を Claude で補完する。"""
    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    anthropic = Anthropic(api_key=settings.anthropic_api_key)

    speakers = script.all_speakers()
    unassigned = [s for s in speakers if not (book.get(s) and book.get(s).is_assigned())]

    if not unassigned:
        print(f"[cast] 全 {len(speakers)} 話者に割当済みです。")
        return book

    voices = fetch_account_voices(client)
    if not voices:
        raise SystemExit(
            "[cast] アカウントに利用可能なボイスがありません。ElevenLabs で"
            "ボイスを追加するか、characters.json に voice_id を直接記入してください。"
        )
    voices_by_id = {_voice_field(v, "voice_id", "voiceId"): v for v in voices}
    candidate_block = _candidate_block(voices)
    print(f"[cast] 候補ボイス {len(voices)} 件。未割当 {len(unassigned)} 話者を選定します。")

    for name in unassigned:
        char = book.ensure(name)
        vid, vname, reason = _select_voice_for(
            anthropic, settings.model, char, candidate_block, voices_by_id
        )
        if not vid:
            print(f"  - {name}: 選定に失敗(スキップ)")
            continue
        mark = "適用" if apply else "提案"
        print(f"  - {name}: {vname or vid} ({vid}) [{mark}] {('… ' + reason) if reason else ''}")
        if apply:
            char.voice_id = vid
            char.voice_name = vname

    if apply:
        book.save()
        print(f"[cast] characters.json に保存しました: {book.path}")
    else:
        print("[cast] dry-run。書き戻すには --apply を付けてください。")
    return book
