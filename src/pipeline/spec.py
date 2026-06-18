"""パイプライン定義: ステージの順番・担当エージェント・人間ゲートの有無。

将来エージェントを差し替え/追加する場合はここを編集する(状態の order もこれに従う)。
gate=True のステージは、完了後に人間の承認(approve)を待ってから次へ進む。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageSpec:
    name: str
    agent: str       # src/agents の担当キー
    gate: bool       # 完了後に人間承認ゲートを置くか
    title: str


# シナリオ → (承認) → 作画 → (承認) → 解析 → (承認) → 割当 → (承認) → 合成
DEFAULT_PIPELINE: tuple[StageSpec, ...] = (
    StageSpec("scenario", "scenario", True, "シナリオ作成"),
    StageSpec("art", "art", True, "作画"),
    StageSpec("analyze", "voice.analyze", True, "解析(話者・感情)"),
    StageSpec("cast", "voice.cast", True, "ボイス割当"),
    StageSpec("synth", "voice.synth", False, "音声合成"),
)
