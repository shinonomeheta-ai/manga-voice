import json

from src.agents.art_agent import ArtAgent
from src.agents.base import AgentResult, RunContext
from src.agents.scenario_agent import ScenarioAgent
from src.agents.voice_agent import VoiceSynthAgent, _scenario_to_text
from src.pipeline import orchestrator as orch
from src.pipeline import state as S
from src.pipeline.spec import StageSpec
from src.pipeline.state import RunState


def _pipeline():
    return (StageSpec("a", "a", True, "A"), StageSpec("b", "b", False, "B"))


class OkAgent:
    name = "ok"

    def run(self, ctx):
        return AgentResult.ok(["out.json"], "done")


class NeedsAgent:
    name = "needs"

    def __init__(self, flag):
        self.flag = flag

    def run(self, ctx):
        if self.flag.exists():
            return AgentResult.ok(["out.json"])
        return AgentResult.needs_input("素材待ち")


class ErrAgent:
    name = "err"

    def run(self, ctx):
        return AgentResult.error("boom")


def _new_state(tmp_path):
    return RunState.create(tmp_path, "r1", pipeline=_pipeline())


# --- state ---

def test_state_roundtrip(tmp_path):
    st = _new_state(tmp_path)
    st.set_status("a", S.AWAITING_APPROVAL, "x")
    st.save()
    again = RunState.load(tmp_path / "state.json")
    assert again.stages["a"].status == S.AWAITING_APPROVAL
    assert again.order == ["a", "b"]
    assert again.stages["a"].gate_required is True


# --- orchestrator: gate ---

def test_gate_stops_then_approve_continues(tmp_path):
    st = _new_state(tmp_path)
    reg = {"a": OkAgent, "b": OkAgent}
    orch.advance(st, reg)
    assert st.stages["a"].status == S.AWAITING_APPROVAL
    assert st.stages["b"].status == S.PENDING  # ゲートで止まり b は未実行

    st.approve("a"); st.save()
    orch.advance(st, reg)
    assert st.stages["a"].status == S.COMPLETED
    assert st.stages["b"].status == S.COMPLETED


def test_auto_approve_runs_all(tmp_path):
    st = _new_state(tmp_path)
    orch.advance(st, {"a": OkAgent, "b": OkAgent}, auto_approve=True)
    assert st.next_actionable() is None
    assert st.stages["a"].gate_approved is True


def test_needs_input_then_resumes(tmp_path):
    st = _new_state(tmp_path)
    flag = tmp_path / "flag"
    reg = {"a": lambda: NeedsAgent(flag), "b": OkAgent}
    orch.advance(st, reg)
    assert st.stages["a"].status == S.AWAITING_INPUT
    flag.write_text("ok", encoding="utf-8")
    orch.advance(st, reg, auto_approve=True)
    assert st.next_actionable() is None


def test_error_stops(tmp_path):
    st = _new_state(tmp_path)
    orch.advance(st, {"a": ErrAgent, "b": OkAgent})
    assert st.stages["a"].status == S.ERROR
    assert st.stages["b"].status == S.PENDING


def test_reject_resets_to_pending(tmp_path):
    st = _new_state(tmp_path)
    orch.advance(st, {"a": OkAgent, "b": OkAgent})
    assert st.stages["a"].status == S.AWAITING_APPROVAL
    st.reject("a", "やり直し")
    assert st.stages["a"].status == S.PENDING
    assert st.stages["a"].gate_approved is False


def test_redo_from_resets_stage_and_downstream(tmp_path):
    st = _new_state(tmp_path)
    orch.advance(st, {"a": OkAgent, "b": OkAgent}, auto_approve=True)
    assert st.next_actionable() is None  # 全完了
    reset = st.redo_from("a")
    assert reset == ["a", "b"]           # a と下流 b
    assert st.stages["a"].status == S.PENDING
    assert st.stages["b"].status == S.PENDING
    assert st.stages["a"].gate_approved is False


def test_redo_from_keeps_upstream(tmp_path):
    st = _new_state(tmp_path)
    orch.advance(st, {"a": OkAgent, "b": OkAgent}, auto_approve=True)
    st.redo_from("b")                    # b だけ(下流) / a は保持
    assert st.stages["a"].status == S.COMPLETED
    assert st.stages["b"].status == S.PENDING


# --- art manual provider ---

def test_art_manual_needs_then_ok(tmp_path):
    ctx = RunContext(run_dir=tmp_path)
    assert ArtAgent().run(ctx).status == "needs_input"
    (tmp_path / "art" / "pages" / "01.png").write_bytes(b"\x89PNG\r\n")
    res = ArtAgent().run(ctx)
    assert res.status == "ok"
    assert (tmp_path / "art" / "art.json").exists()


def test_art_auto_is_stub_error(tmp_path):
    ctx = RunContext(run_dir=tmp_path, options={"art_provider": "auto"})
    assert ArtAgent().run(ctx).status == "error"


# --- scenario agent: premise gate (no API) ---

def test_scenario_needs_premise(tmp_path):
    res = ScenarioAgent().run(RunContext(run_dir=tmp_path))
    assert res.status == "needs_input"
    assert (tmp_path / "scenario" / "premise.txt").exists()


NEME_MD = """# 第01話「テスト」
## ログライン
（テスト回）
## ページ別ネーム指示
### P1
- コマ1:【画】夜の街 ／【ナレ】「夜が来た」
- コマ2:【画】男の横顔 ／【セリフ・太郎】「行こう」
"""


def test_scenario_ingest_offline(tmp_path):
    """--neme 取り込みは API 無しで scenario.json + art_brief.json を出す。"""
    neme = tmp_path / "neme.md"
    neme.write_text(NEME_MD, encoding="utf-8")
    ctx = RunContext(run_dir=tmp_path, options={"scenario_neme": str(neme)})
    res = ScenarioAgent().run(ctx)
    assert res.status == "ok"
    scenario = json.loads((tmp_path / "scenario" / "scenario.json").read_text(encoding="utf-8"))
    assert scenario["scenes"][0]["lines"][1]["speaker"] == "太郎"
    assert (tmp_path / "scenario" / "art_brief.json").exists()


def test_scenario_ingest_missing_file(tmp_path):
    ctx = RunContext(run_dir=tmp_path, options={"scenario_neme": str(tmp_path / "none.md")})
    assert ScenarioAgent().run(ctx).status == "error"


def test_art_renders_brief_from_ingest(tmp_path):
    # 取り込み済みの art_brief.json を置くと art ステージが brief.md を出す
    (tmp_path / "scenario").mkdir()
    (tmp_path / "scenario" / "art_brief.json").write_text(json.dumps(
        {"pages": [{"page": 1, "panels": [{"panel": 1, "art": "夜の街"}]}]}), encoding="utf-8")
    res = ArtAgent().run(RunContext(run_dir=tmp_path))
    assert res.status == "needs_input"  # 画像はまだ無い
    brief = (tmp_path / "art" / "brief.md").read_text(encoding="utf-8")
    assert "夜の街" in brief and "P1" in brief


def test_scenario_rules_injected_into_system(tmp_path):
    from src.agents.scenario_agent import SYSTEM_BASE, build_system, load_rules
    rules_file = tmp_path / "rules.md"
    rules_file.write_text("- トーン: シリアス\n- 一人称: 僕", encoding="utf-8")
    ctx = RunContext(run_dir=tmp_path, options={"scenario_rules_path": str(rules_file)})
    rules = load_rules(ctx)
    sys_prompt = build_system(rules)
    assert "シリアス" in sys_prompt and "制作ルール" in sys_prompt
    assert sys_prompt.startswith(SYSTEM_BASE)


def test_scenario_rules_template_sentinel_ignored(tmp_path):
    from src.agents.scenario_agent import build_system, load_rules
    rules_file = tmp_path / "rules.md"
    rules_file.write_text("ここに制作ルールを書いてください", encoding="utf-8")
    ctx = RunContext(run_dir=tmp_path, options={"scenario_rules_path": str(rules_file)})
    assert load_rules(ctx) == ""              # 未編集テンプレは無視
    assert build_system(load_rules(ctx)) == build_system("")  # ルール無し扱い


def test_scenario_rules_missing_file(tmp_path):
    from src.agents.scenario_agent import load_rules
    ctx = RunContext(run_dir=tmp_path, options={"scenario_rules_path": str(tmp_path / "nope.md")})
    assert load_rules(ctx) == ""


# --- voice agent helper (pure) ---

def test_scenario_to_text():
    s = {"title": "T", "scenes": [{"id": "s1", "setting": "教室",
         "lines": [{"speaker": "A", "text": "hi", "direction": "笑顔"}]}]}
    t = _scenario_to_text(s)
    assert "教室" in t and "A：（笑顔）hi" in t


def test_voice_synth_dry_run_offline(tmp_path):
    """音声合成ステージは dry-run なら ElevenLabs キー無しで計画を出せる。"""
    voice = tmp_path / "voice"
    voice.mkdir(parents=True)
    (voice / "script.json").write_text(json.dumps(
        {"scenes": [{"id": "s1", "lines": [
            {"speaker": "博士", "text": "やあ", "tts_text": "やあ"}]}]}),
        encoding="utf-8")
    res = VoiceSynthAgent().run(RunContext(run_dir=tmp_path, dry_run=True))
    assert res.status == "ok"
    manifest = json.loads((voice / "voice_output.json").read_text(encoding="utf-8"))
    assert manifest["dry_run"] is True and len(manifest["clips"]) == 1
