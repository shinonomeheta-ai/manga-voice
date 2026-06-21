"""整音エフェクト: 生成した音声を「自然」に聞こえるよう後処理する。

ffmpeg のオーディオフィルタで、声に効く定番チェーン(低域カット→緩い圧縮→
ラウドネス整え→ピーク制限→軽いフェード)をかける。プリセットで強さを切替。

- build_filter(preset) はフィルタ文字列を組むだけの純関数 → オフラインでテスト可。
- apply_fx() は ffmpeg を呼ぶ。ffmpeg が無ければエフェクト無しでコピー(壊さない)。

注意: 後処理は「音の整え(トーン/音量/耳障りさ)」を改善するが、棒読み感など
発話自体の自然さは TTS 側(プロソディ/掛け合い)で作る。FXは仕上げ。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# プリセット = ffmpeg -af に渡すフィルタの並び
PRESETS: dict[str, list[str]] = {
    # 明るくクリアな整音(既定)。高域を少し持ち上げてハッキリ。
    "natural": [
        "highpass=f=80",                                  # 低域のゴロ・ノイズ除去
        "acompressor=threshold=-18dB:ratio=3:attack=5:release=120",  # 緩い圧縮で粒を揃える
        "treble=g=2:f=6000",                              # 高域を少し上げてクリア
        "loudnorm=I=-16:TP=-1.5:LRA=11",                  # ラウドネス正規化(配信目安)
        "alimiter=limit=0.95",                            # クリップ防止
    ],
    # 最小限(正規化＋安全策のみ)
    "clean": [
        "highpass=f=80",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "alimiter=limit=0.95",
    ],
    # 温かみ。低音を厚く・高域を抑えて柔らかく・軽い空気感(natural と明確に差をつける)。
    "warm": [
        "highpass=f=60",
        "bass=g=5:f=180",                                 # 低音に厚み(強め)
        "treble=g=-4:f=6000",                             # 高域を抑えて柔らかく
        "acompressor=threshold=-20dB:ratio=2.5:attack=8:release=180",
        "aecho=0.8:0.88:55:0.2",                          # 軽い空気感(やや増)
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "alimiter=limit=0.95",
    ],
}
DEFAULT_PRESET = "natural"


def build_filter(preset: str = DEFAULT_PRESET) -> str:
    """プリセット名から ffmpeg のフィルタ文字列を組む。未知名は natural に。"""
    chain = PRESETS.get(preset, PRESETS[DEFAULT_PRESET])
    return ",".join(chain)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def apply_fx(src: Path, dst: Path, preset: str = DEFAULT_PRESET) -> bool:
    """src に整音エフェクトをかけて dst に出力。成功したら True。

    ffmpeg が無い/失敗した場合はエフェクト無しで src を dst にコピーし False を返す。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not ffmpeg_available():
        print("[fx] ffmpeg が見つからないためエフェクト無しで出力します。")
        if src.resolve() != dst.resolve():
            shutil.copy(src, dst)
        return False
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(src), "-af", build_filter(preset), str(dst)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"[fx] 整音適用: preset={preset} -> {dst.name}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[fx] ffmpeg 失敗({e}). エフェクト無しで出力します。")
        if src.resolve() != dst.resolve():
            shutil.copy(src, dst)
        return False
