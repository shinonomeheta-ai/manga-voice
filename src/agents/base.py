"""エージェント共通基盤: 実行コンテキストと結果の型。

各エージェント(scenario / art / voice の各ステージ)はこの Agent インターフェースに
従い、run(ctx) -> AgentResult を返す。オーケストレーターはこの結果だけを見て
ステージ状態(state.json)を更新するため、エージェントの中身に依存しない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# エージェント実行の結果区分
RESULT_OK = "ok"              # 正常終了。outputs を生成した
RESULT_NEEDS_INPUT = "needs_input"  # 人間からの素材/入力待ち(例: 作画manualで画像未配置)
RESULT_ERROR = "error"        # 失敗


@dataclass
class AgentResult:
    status: str = RESULT_OK
    outputs: list[str] = field(default_factory=list)  # run_dir 相対の成果物パス
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, outputs: list[str] | None = None, message: str = "", **data: Any) -> "AgentResult":
        return cls(RESULT_OK, outputs or [], message, dict(data))

    @classmethod
    def needs_input(cls, message: str, **data: Any) -> "AgentResult":
        return cls(RESULT_NEEDS_INPUT, [], message, dict(data))

    @classmethod
    def error(cls, message: str, **data: Any) -> "AgentResult":
        return cls(RESULT_ERROR, [], message, dict(data))


@dataclass
class RunContext:
    """エージェントに渡す実行コンテキスト。"""

    run_dir: Path
    settings: Any = None            # config.Settings | None (APIキー。dry-run/オフラインでは None 可)
    dry_run: bool = False
    options: dict[str, Any] = field(default_factory=dict)

    def stage_dir(self, name: str) -> Path:
        d = self.run_dir / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def rel(self, path: Path) -> str:
        """run_dir 相対のパス文字列(成果物記録用)。"""
        try:
            return str(path.relative_to(self.run_dir)).replace("\\", "/")
        except ValueError:
            return str(path)


@runtime_checkable
class Agent(Protocol):
    name: str

    def run(self, ctx: RunContext) -> AgentResult: ...
