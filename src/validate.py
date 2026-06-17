"""合成前のオフライン事前チェック(APIキー不要)。

script.json と characters.json を突き合わせ、本番合成で失敗/不自然になりやすい点を
errors(致命) と warnings(注意) に分けて報告する。
"""
from __future__ import annotations

from .config import CharacterBook
from .models import Script
from .prompts import EMOTION_VALUES, V3_AUDIO_TAGS
from .tts import STABILITY_MAP

# eleven_v3 の1リクエスト上限(概算)。これを超える行は分割が必要。
TTS_CHAR_LIMIT = 5000
WHITELIST = set(V3_AUDIO_TAGS)


def validate(script: Script, book: CharacterBook, scene_id: str | None = None) -> bool:
    """検査して結果を表示。errors が無ければ True を返す。"""
    errors: list[str] = []
    warnings: list[str] = []

    scenes = [s for s in script.scenes if scene_id is None or s.id == scene_id]
    if not scenes:
        errors.append(f"対象シーンがありません: {scene_id or '(全シーン)'}")

    n_lines = 0
    for scene in scenes:
        if not scene.lines:
            warnings.append(f"{scene.id}: セリフが0件です")
        for idx, line in enumerate(scene.lines):
            n_lines += 1
            loc = f"{scene.id}#{idx:03d}({line.speaker})"
            if not line.text.strip():
                errors.append(f"{loc}: text が空です")
            # audio_tags ホワイトリスト検査
            for tag in line.audio_tags:
                if tag not in WHITELIST:
                    errors.append(f"{loc}: 未知の audio_tag {tag!r}(v3に無効・読み上げの恐れ)")
            # tts_text にタグらしき未知トークンが残っていないか
            if line.emotion and line.emotion not in EMOTION_VALUES:
                warnings.append(f"{loc}: 未知の emotion {line.emotion!r}")
            length = len(line.resolved_tts_text())
            if length > TTS_CHAR_LIMIT:
                errors.append(f"{loc}: {length}文字は上限{TTS_CHAR_LIMIT}超(要分割)")

    # キャスティング検査
    for speaker in script.all_speakers():
        char = book.get(speaker)
        if not char or not char.is_assigned():
            errors.append(f"話者「{speaker}」に voice_id 未割当(`cast --apply` 推奨)")
        elif char.stability.strip().lower() not in STABILITY_MAP:
            warnings.append(
                f"話者「{speaker}」の stability {char.stability!r} は想定外"
                f"(creative/natural/robust)。natural 扱いになります"
            )

    _report(n_lines, len(scenes), errors, warnings)
    return not errors


def _report(n_lines: int, n_scenes: int, errors: list[str], warnings: list[str]) -> None:
    print(f"[validate] {n_scenes} シーン / {n_lines} セリフ を検査")
    for w in warnings:
        print(f"  ⚠ {w}")
    for e in errors:
        print(f"  ✗ {e}")
    if not errors and not warnings:
        print("  ✓ 問題なし。合成可能です。")
    elif not errors:
        print(f"  ✓ 致命的な問題なし(警告 {len(warnings)} 件)。合成可能です。")
    else:
        print(f"  ✗ エラー {len(errors)} 件。修正後に再検査してください。")
