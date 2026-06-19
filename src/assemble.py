"""個別clip を pydub(ffmpeg) で連結し、シーン全体の音声を作る。

掛け合いを自然にするため、セリフ間の無音は固定ではなく、前後の発話の
感情・話者交代・疑問→即答 に応じて**動的**に決める（ツッコミは詰め、ためは開ける）。

ffmpeg が未インストールでも CLI 全体は壊れないよう、import を遅延させ
失敗時はメッセージを出してスキップする。
"""
from __future__ import annotations

from pathlib import Path

from .config import CLIPS_DIR, SCENES_DIR
from .models import Line, Script

BASE_GAP_MS = 300
MIN_GAP_MS = 80
MAX_GAP_MS = 700

# 即反応＝間を詰める感情 / ためを作る＝間を開ける感情
QUICK_EMOTIONS = {"excited", "surprised", "angry", "playful", "happy"}
SLOW_EMOTIONS = {"sad", "serious", "tender", "calm", "fearful"}


def gap_ms(prev: Line | None, cur: Line) -> int:
    """前のセリフ prev を受けて、cur の直前に挟む無音(ms)を決める。"""
    if prev is None:
        return 0
    g = BASE_GAP_MS
    if cur.emotion in QUICK_EMOTIONS:          # 相手に即反応 → 詰める
        g -= 130
    if prev.emotion in SLOW_EMOTIONS:          # 前が余韻系 → 開ける
        g += 200
    if prev.speaker == cur.speaker:            # 同一話者の続き → 息継ぎで開ける
        g += 80
    else:                                      # 話者交代 → 会話のテンポで少し詰める
        g -= 40
    if prev.text.rstrip().endswith(("?", "？")) and cur.emotion in QUICK_EMOTIONS:
        g -= 80                                # 疑問→即答 はさらに詰める
    return max(MIN_GAP_MS, min(MAX_GAP_MS, g))


def _clip_for(scene_id: str, idx: int) -> Path | None:
    matches = sorted(CLIPS_DIR.glob(f"{scene_id}_{idx:03d}_*"))
    matches = [c for c in matches if c.suffix.lower() in (".mp3", ".wav")]
    return matches[0] if matches else None


def assemble_scenes(script: Script, scene_id: str | None = None) -> list[Path]:
    """clips/ の個別clipをシーンごとに、動的な間で連結して scenes/<id>_joined.* を作る。"""
    try:
        from pydub import AudioSegment  # 遅延import(ffmpeg依存)
    except Exception as e:  # noqa: BLE001
        print(f"[assemble] pydub/ffmpeg が利用できないためスキップします: {e}")
        return []

    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    scenes = [s for s in script.scenes if scene_id is None or s.id == scene_id]

    for scene in scenes:
        combined = AudioSegment.empty()
        prev: Line | None = None
        n = 0
        for idx, line in enumerate(scene.lines):
            clip = _clip_for(scene.id, idx)
            if clip is None:
                continue
            try:
                seg = AudioSegment.from_file(clip)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {clip.name} の読み込み失敗: {e}")
                continue
            if prev is not None:
                combined += AudioSegment.silent(duration=gap_ms(prev, line))
            combined += seg
            prev = line
            n += 1
        if n == 0 or len(combined) == 0:
            continue
        ext = (_clip_for(scene.id, 0) or Path("x.mp3")).suffix.lower()
        ext = ext if ext in (".mp3", ".wav") else ".mp3"
        out = SCENES_DIR / f"{scene.id}_joined{ext}"
        combined.export(out, format="mp3" if ext == ".mp3" else "wav")
        written.append(out)
        print(f"[assemble] {scene.id}: {n} clip を動的ギャップで連結 -> {out.name}")

    print(f"[assemble] 連結シーン音声 {len(written)} 件 -> {SCENES_DIR}")
    return written
