"""オーケストレーター: ステージを順に実行し、人間ゲートで停止/再開する。

エージェントは registry(ステージ名 -> Agent)で注入する。これにより本番の
エージェント群でも、テスト用のダミーエージェントでも同じ制御を回せる。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..agents.base import (
    RESULT_ERROR,
    RESULT_NEEDS_INPUT,
    RESULT_OK,
    Agent,
    RunContext,
)
from . import state as S
from .state import RunState

# registry: ステージ名 -> Agent を返す factory(遅延生成でAPI依存importを避ける)
AgentRegistry = dict[str, Callable[[], Agent]]


def advance(
    run_state: RunState,
    registry: AgentRegistry,
    settings: Any = None,
    dry_run: bool = False,
    until: str | None = None,
    auto_approve: bool = False,
) -> RunState:
    """実行可能なステージを順に進める。ゲート/入力待ち/エラー/until で停止。"""
    assert run_state.path is not None
    run_dir = run_state.path.parent

    while True:
        st = run_state.next_actionable()
        if st is None:
            print("[pipeline] 全ステージ完了。")
            return run_state

        if st.status == S.AWAITING_APPROVAL:
            print(f"[pipeline] ✋ 「{st.name}」は人間の承認待ちです。"
                  f"確認後 `pipeline approve {st.name}` を実行してください。")
            return run_state
        if st.status == S.ERROR:
            print(f"[pipeline] ✗ 「{st.name}」はエラー状態です: {st.message}\n"
                  f"原因を直して `pipeline run` で再実行してください。")
            return run_state

        # awaiting_input / pending は実行を試みる
        factory = registry.get(st.name)
        if factory is None:
            run_state.set_status(st.name, S.ERROR, f"エージェント未登録: {st.name}")
            run_state.save()
            print(f"[pipeline] ✗ エージェント未登録: {st.name}")
            return run_state

        ctx = RunContext(run_dir=run_dir, settings=settings, dry_run=dry_run,
                         options=run_state.options)
        print(f"[pipeline] ▶ 「{st.name}」を実行…")
        run_state.set_status(st.name, "running")
        try:
            result = factory().run(ctx)
        except SystemExit as e:  # 設定不足など想定内の停止
            run_state.set_status(st.name, S.ERROR, str(e))
            run_state.save()
            print(f"[pipeline] ✗ 「{st.name}」: {e}")
            return run_state
        except Exception as e:  # noqa: BLE001
            run_state.set_status(st.name, S.ERROR, repr(e))
            run_state.save()
            print(f"[pipeline] ✗ 「{st.name}」で例外: {e!r}")
            return run_state

        if result.status == RESULT_NEEDS_INPUT:
            st.outputs = result.outputs
            run_state.set_status(st.name, S.AWAITING_INPUT, result.message)
            run_state.save()
            print(f"[pipeline] ⏸ 「{st.name}」は素材待ち: {result.message}")
            return run_state
        if result.status == RESULT_ERROR:
            run_state.set_status(st.name, S.ERROR, result.message)
            run_state.save()
            print(f"[pipeline] ✗ 「{st.name}」失敗: {result.message}")
            return run_state

        # 正常完了
        st.outputs = result.outputs
        if st.gate_required and not auto_approve:
            run_state.set_status(st.name, S.AWAITING_APPROVAL, result.message)
            run_state.save()
            print(f"[pipeline] ✅ 「{st.name}」完了 → 人間の承認待ち。"
                  f"成果物: {', '.join(result.outputs) or '(なし)'}")
            print(f"[pipeline]   承認: `pipeline approve {st.name}` / 差戻し: `pipeline reject {st.name}`")
            return run_state

        # ゲート無し or auto_approve
        if st.gate_required:
            st.gate_approved = True
        run_state.set_status(st.name, S.COMPLETED, result.message)
        run_state.save()
        print(f"[pipeline] ✓ 「{st.name}」完了。成果物: {', '.join(result.outputs) or '(なし)'}")

        if until and st.name == until:
            print(f"[pipeline] until={until} に到達したので停止します。")
            return run_state


def status_table(run_state: RunState) -> str:
    icon = {
        S.PENDING: "・", S.AWAITING_INPUT: "⏸", S.AWAITING_APPROVAL: "✋",
        S.COMPLETED: "✓", S.ERROR: "✗", S.SKIPPED: "—", "running": "▶",
    }
    lines = [f"run: {run_state.run_id}"]
    for name in run_state.order:
        st = run_state.stages[name]
        gate = ""
        if st.gate_required:
            gate = "[承認済]" if st.gate_approved else "[要承認]"
        lines.append(f"  {icon.get(st.status, '?')} {name:18s} {st.status:18s} {gate} "
                     f"{('- ' + st.message) if st.message else ''}".rstrip())
    return "\n".join(lines)
