"""ステージ名 -> エージェント の対応(registry)。

遅延 factory にしておくことで、import 時点ではAPI依存を持ち込まない。
将来エージェントを差し替える場合はここを編集する。
"""
from __future__ import annotations

from .orchestrator import AgentRegistry


def default_registry() -> AgentRegistry:
    from ..agents.art_agent import ArtAgent
    from ..agents.scenario_agent import ScenarioAgent
    from ..agents.voice_agent import (
        VoiceAnalyzeAgent,
        VoiceCastAgent,
        VoiceSynthAgent,
    )

    return {
        "scenario": ScenarioAgent,
        "art": ArtAgent,
        "analyze": VoiceAnalyzeAgent,
        "cast": VoiceCastAgent,
        "synth": VoiceSynthAgent,
    }
