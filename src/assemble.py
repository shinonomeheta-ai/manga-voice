"""個別clip を pydub(ffmpeg) で連結し、シーン全体の音声を作る。

ffmpeg が未インストールでも CLI 全体は壊れないよう、import を遅延させ
失敗時はメッセージを出してスキップする。
"""
from __future__ import annotations

from pathlib import Path

from .config import CLIPS_DIR, SCENES_DIR
from .models import Script

GAP_MS = 350  # セリフ間に挟む無音(ミリ秒)


def assemble_scenes(script: Script, scene_id: str | None = None) -> list[Path]:
    """clips/ の個別clipをシーンごとに連結して scenes/<id>_joined.* を作る。"""
    try:
        from pydub import AudioSegment  # 遅延import(ffmpeg依存)
    except Exception as e:  # noqa: BLE001
        print(f"[assemble] pydub/ffmpeg が利用できないためスキップします: {e}")
        return []

    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    scenes = [s for s in script.scenes if scene_id is None or s.id == scene_id]

    for scene in scenes:
        clips = sorted(CLIPS_DIR.glob(f"{scene.id}_*"))
        clips = [c for c in clips if c.suffix.lower() in (".mp3", ".wav")]
        if not clips:
            continue
        combined = AudioSegment.empty()
        silence = AudioSegment.silent(duration=GAP_MS)
        for i, clip in enumerate(clips):
            try:
                seg = AudioSegment.from_file(clip)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {clip.name} の読み込み失敗: {e}")
                continue
            if i:
                combined += silence
            combined += seg
        if len(combined) == 0:
            continue
        ext = clips[0].suffix.lower()
        out = SCENES_DIR / f"{scene.id}_joined{ext}"
        fmt = "mp3" if ext == ".mp3" else "wav"
        combined.export(out, format=fmt)
        written.append(out)
        print(f"[assemble] {scene.id}: {len(clips)} clip 連結 -> {out.name}")

    print(f"[assemble] 連結シーン音声 {len(written)} 件 -> {SCENES_DIR}")
    return written
