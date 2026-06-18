"""実行(run)の状態管理: runs/<id>/state.json。

各ステージの状態と人間承認ゲートを永続化し、途中停止→再開を可能にする。
ステージ状態:
  pending             : 未実行
  awaiting_input      : エージェントが人間の素材/入力待ち(例: 作画manualで画像未配置)
  awaiting_approval   : エージェントは完了、人間の承認ゲート待ち
  completed           : 完了(ゲートがあれば承認済み)
  error               : 失敗
  skipped             : スキップ
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .spec import DEFAULT_PIPELINE

PENDING = "pending"
AWAITING_INPUT = "awaiting_input"
AWAITING_APPROVAL = "awaiting_approval"
COMPLETED = "completed"
ERROR = "error"
SKIPPED = "skipped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class StageState:
    name: str
    status: str = PENDING
    gate_required: bool = False
    gate_approved: bool = False
    outputs: list[str] = field(default_factory=list)
    message: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StageState":
        return cls(
            name=d["name"], status=d.get("status", PENDING),
            gate_required=bool(d.get("gate_required", False)),
            gate_approved=bool(d.get("gate_approved", False)),
            outputs=list(d.get("outputs", [])), message=d.get("message", ""),
            updated_at=d.get("updated_at", ""),
        )


@dataclass
class RunState:
    run_id: str
    created_at: str
    order: list[str]
    stages: dict[str, StageState]
    options: dict = field(default_factory=dict)
    path: Path | None = None

    # --- 参照 ---
    def stage(self, name: str) -> StageState:
        return self.stages[name]

    def next_actionable(self) -> StageState | None:
        """次に実行/承認すべきステージ。無ければ None(全完了)。"""
        for name in self.order:
            st = self.stages[name]
            if st.status in (COMPLETED, SKIPPED):
                continue
            return st
        return None

    # --- 更新 ---
    def set_status(self, name: str, status: str, message: str = "") -> None:
        st = self.stages[name]
        st.status = status
        st.message = message
        st.updated_at = _now()

    def approve(self, name: str) -> StageState:
        st = self.stages[name]
        st.gate_approved = True
        if st.status == AWAITING_APPROVAL:
            st.status = COMPLETED
        st.updated_at = _now()
        return st

    def reject(self, name: str, reason: str = "") -> StageState:
        st = self.stages[name]
        st.gate_approved = False
        st.status = PENDING  # 再実行できるよう差し戻す
        st.message = reason or "人間により差し戻し"
        st.updated_at = _now()
        return st

    def redo_from(self, name: str) -> list[str]:
        """指定ステージと、それより下流の全ステージを未実行に戻す。

        シナリオだけ直して下流を作り直したいときに使う。成果物パスの記録もクリアする。
        戻したステージ名の一覧を返す。
        """
        if name not in self.order:
            raise SystemExit(f"未知のステージ: {name}")
        idx = self.order.index(name)
        reset: list[str] = []
        for n in self.order[idx:]:
            st = self.stages[n]
            st.status = PENDING
            st.gate_approved = False
            st.outputs = []
            st.message = ""
            st.updated_at = _now()
            reset.append(n)
        return reset

    # --- 永続化 ---
    def save(self) -> None:
        assert self.path is not None
        data = {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "order": self.order,
            "options": self.options,
            "stages": [self.stages[n].to_dict() for n in self.order],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "RunState":
        d = json.loads(path.read_text(encoding="utf-8"))
        stages = {s["name"]: StageState.from_dict(s) for s in d["stages"]}
        return cls(run_id=d["run_id"], created_at=d["created_at"], order=list(d["order"]),
                   stages=stages, options=d.get("options", {}), path=path)

    @classmethod
    def create(cls, run_dir: Path, run_id: str, options: dict | None = None,
               pipeline=DEFAULT_PIPELINE) -> "RunState":
        order = [s.name for s in pipeline]
        stages = {
            s.name: StageState(name=s.name, gate_required=s.gate)
            for s in pipeline
        }
        state = cls(run_id=run_id, created_at=_now(), order=order, stages=stages,
                    options=options or {}, path=run_dir / "state.json")
        state.save()
        return state
