"""シナリオ作成エージェント: 人間が書いた前提(premise)から台本を生成する。

入力: run_dir/scenario/premise.txt (人間が書く作品の前提・お題)
出力: run_dir/scenario/scenario.json (schemas/scenario.schema.json 準拠)

premise が未記入なら needs_input でテンプレを置いて停止し、人間の記入を待つ。
Anthropic は遅延import(キーが要るのは実行時のみ)。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import SCENARIO_RULES_PATH, require_anthropic
from .base import AgentResult, RunContext

# テンプレ未編集の目印。これを含む rules ファイルは「未設定」とみなし注入しない。
RULES_SENTINEL = "ここに制作ルールを書いて"

PREMISE_TEMPLATE = """# 作品の前提（このファイルを書いてから pipeline run を再実行）

タイトル案:
ジャンル/トーン:
主な登場人物（名前・性格・関係）:
あらすじ / 起きること:
尺の目安（シーン数など）:
"""

SCENARIO_TOOL = {
    "name": "record_scenario",
    "description": "生成したシナリオを構造化データとして記録する。",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "logline": {"type": "string"},
            "language": {"type": "string"},
            "characters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "gender": {"type": "string"},
                        "age": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "setting": {"type": "string"},
                        "summary": {"type": "string"},
                        "lines": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "speaker": {"type": "string"},
                                    "text": {"type": "string"},
                                    "direction": {"type": "string"},
                                },
                                "required": ["speaker", "text"],
                            },
                        },
                    },
                    "required": ["id", "lines"],
                },
            },
        },
        "required": ["title", "characters", "scenes"],
    },
}

SYSTEM_BASE = """あなたはプロのシナリオライターです。与えられた前提から、音声化と作画を
前提とした台本を作ってください。各シーンを発話(speaker/text)の並びで表現し、地の文は
speaker を「ナレーター」にします。過剰なト書きは direction に短くまとめ、text には
読み上げる本文のみを入れます。必ず record_scenario ツールで構造化して返すこと。"""


def load_rules(ctx: RunContext) -> str:
    """恒久のシナリオ制作ルールを読む。options['scenario_rules_path'] 優先、既定は config/。"""
    raw = ctx.options.get("scenario_rules_path")
    path = Path(raw) if raw else SCENARIO_RULES_PATH
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text or RULES_SENTINEL in text:
        return ""  # 未編集テンプレは無視
    return text


def build_system(rules: str) -> str:
    """ベース指示に制作ルールを最優先ルールとして連結する。"""
    if rules:
        return (SYSTEM_BASE
                + "\n\n# 制作ルール（最優先で厳守すること）\n" + rules)
    return SYSTEM_BASE


class ScenarioAgent:
    name = "scenario"

    def run(self, ctx: RunContext) -> AgentResult:
        scen_dir = ctx.stage_dir("scenario")
        premise_path = scen_dir / "premise.txt"

        # premise を CLI/option から渡された場合は書き出す
        premise_opt = str(ctx.options.get("premise", "")).strip()
        if premise_opt and not premise_path.exists():
            premise_path.write_text(premise_opt, encoding="utf-8")

        if not premise_path.exists() or not premise_path.read_text(encoding="utf-8").strip():
            premise_path.write_text(PREMISE_TEMPLATE, encoding="utf-8")
            return AgentResult.needs_input(
                f"前提が未記入です。{ctx.rel(premise_path)} を書いてから再実行してください。")

        require_anthropic(ctx.settings)
        from anthropic import Anthropic  # 遅延import

        premise = premise_path.read_text(encoding="utf-8")
        rules = load_rules(ctx)
        client = Anthropic(api_key=ctx.settings.anthropic_api_key)
        model = getattr(ctx.settings, "model", "claude-opus-4-8")
        rules_note = "制作ルール適用" if rules else "制作ルール未設定"
        print(f"[scenario] {model} で台本を生成中…（{rules_note}）")
        resp = client.messages.create(
            model=model, max_tokens=8000, system=build_system(rules),
            tools=[SCENARIO_TOOL], tool_choice={"type": "tool", "name": "record_scenario"},
            messages=[{"role": "user", "content": f"# 前提\n{premise}"}],
        )
        data = _tool_input(resp, "record_scenario")
        data.setdefault("version", "1.0")
        data.setdefault("language", "ja")
        out = scen_dir / "scenario.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        n_scenes = len(data.get("scenes", []))
        names = [c.get("name") for c in data.get("characters", [])]
        return AgentResult.ok([ctx.rel(out)], message=f"{n_scenes}シーン / 登場人物 {names}")


def _tool_input(resp, tool_name: str) -> dict:
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return dict(block.input)
    raise RuntimeError(f"Claude が {tool_name} ツールを返しませんでした。")
