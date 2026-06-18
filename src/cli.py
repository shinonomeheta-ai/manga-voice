"""manga-voice CLI: analyze / cast / synth / run。

漫画・シナリオ -> 解析 -> キャスティング -> 合成 -> 連結 を統合する。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import assemble as assemble_mod
from . import assets as assets_mod
from . import notion as notion_mod
from . import tts as tts_mod
from . import validate as validate_mod
from . import voices as voices_mod
from .analyze import analyze as analyze_inputs
from .config import (
    ASSETS_DIR,
    INPUTS_DIR,
    SCRIPT_PATH,
    CharacterBook,
    Settings,
    ensure_dirs,
    load_settings,
    require_anthropic,
    require_elevenlabs,
    require_notion_token,
)
from .models import Script


def _save_script(script: Script) -> None:
    SCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCRIPT_PATH.write_text(
        json.dumps(script.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[script] 保存: {SCRIPT_PATH}")


def _load_script() -> Script:
    if not SCRIPT_PATH.exists():
        raise SystemExit(
            f"script.json が見つかりません: {SCRIPT_PATH}\n先に `analyze` を実行してください。"
        )
    return Script.from_dict(json.loads(SCRIPT_PATH.read_text(encoding="utf-8")))


def cmd_fetch_notion(args: argparse.Namespace, settings: Settings) -> None:
    inputs_dir = Path(args.inputs) if args.inputs else INPUTS_DIR
    token = require_notion_token(settings)
    notion_mod.fetch_page_to_inputs(token, args.page, inputs_dir)


def _load_bible(args: argparse.Namespace, book: CharacterBook):
    assets_dir = Path(args.assets) if getattr(args, "assets", None) else ASSETS_DIR
    bible = assets_mod.load_character_bible(assets_dir, book)
    if bible:
        named = ", ".join(a.name for a in bible)
        print(f"[bible] キャラ設定資料 {len(bible)} 体を使用: {named}")
    return bible


def cmd_analyze(args: argparse.Namespace, settings: Settings) -> Script:
    inputs_dir = Path(args.inputs) if args.inputs else INPUTS_DIR
    if getattr(args, "notion_page", None):
        notion_mod.fetch_page_to_inputs(require_notion_token(settings), args.notion_page, inputs_dir)
    require_anthropic(settings)
    book = CharacterBook.load()
    bible = _load_bible(args, book)
    script = analyze_inputs(settings, inputs_dir, language=book.language, character_bible=bible)
    _save_script(script)
    return script


def cmd_cast(args: argparse.Namespace, settings: Settings) -> CharacterBook:
    require_anthropic(settings)
    require_elevenlabs(settings)
    script = _load_script()
    book = CharacterBook.load()
    return voices_mod.cast(settings, script, book, apply=args.apply)


def _resolve_run(run_id: str | None):
    from .config import RUNS_DIR
    from .pipeline.state import RunState
    if run_id:
        p = RUNS_DIR / run_id / "state.json"
        if not p.exists():
            raise SystemExit(f"run が見つかりません: {run_id}")
        return RunState.load(p)
    candidates = sorted(RUNS_DIR.glob("*/state.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit("run がありません。`pipeline new` で作成してください。")
    return RunState.load(candidates[0])


def cmd_pipeline_new(args: argparse.Namespace, settings: Settings) -> None:
    import datetime
    from .config import RUNS_DIR
    from .pipeline import orchestrator as orch
    from .pipeline.state import RunState
    run_id = args.id or datetime.datetime.now().strftime("run-%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / run_id
    if (run_dir / "state.json").exists():
        raise SystemExit(f"既に存在する run です: {run_id}")
    options = {"art_provider": args.art_provider, "dialogue": bool(args.dialogue)}
    if args.premise:
        options["premise"] = args.premise
    if args.rules:
        options["scenario_rules_path"] = args.rules
    state = RunState.create(run_dir, run_id, options=options)
    print(f"[pipeline] 作成: {run_id}  ({run_dir})")
    print(orch.status_table(state))
    print("次: `pipeline run` で進められます（各ステージの承認/素材待ちで停止します）。")


def cmd_pipeline_run(args: argparse.Namespace, settings: Settings) -> None:
    from .pipeline import orchestrator as orch
    from .pipeline.registry import default_registry
    state = _resolve_run(args.run)
    orch.advance(state, default_registry(), settings=settings, dry_run=args.dry_run,
                 until=args.until, auto_approve=args.auto_approve)
    print(orch.status_table(state))


def cmd_pipeline_status(args: argparse.Namespace, settings: Settings) -> None:
    from .pipeline import orchestrator as orch
    print(orch.status_table(_resolve_run(args.run)))


def cmd_pipeline_approve(args: argparse.Namespace, settings: Settings) -> None:
    state = _resolve_run(args.run)
    st = state.approve(args.stage)
    state.save()
    print(f"[pipeline] 承認: {args.stage} -> {st.status}")
    print("次: `pipeline run` で続行します。")


def cmd_pipeline_reject(args: argparse.Namespace, settings: Settings) -> None:
    state = _resolve_run(args.run)
    state.reject(args.stage, args.reason or "")
    state.save()
    print(f"[pipeline] 差戻し: {args.stage}（再実行できます）")


def cmd_pipeline_list(args: argparse.Namespace, settings: Settings) -> None:
    from .config import RUNS_DIR
    from .pipeline.state import RunState
    runs = sorted(RUNS_DIR.glob("*/state.json"))
    if not runs:
        print("(run なし)")
        return
    for p in runs:
        s = RunState.load(p)
        nxt = s.next_actionable()
        print(f"- {s.run_id}: 次 = {nxt.name if nxt else '完了'}")


def cmd_validate(args: argparse.Namespace, settings: Settings) -> None:
    script = _load_script()
    book = CharacterBook.load()
    ok = validate_mod.validate(script, book, scene_id=args.scene)
    if not ok:
        raise SystemExit(1)


def cmd_synth(args: argparse.Namespace, settings: Settings) -> None:
    if not args.dry_run:
        require_elevenlabs(settings)
    script = _load_script()
    book = CharacterBook.load()
    tts_mod.synth_clips(settings, script, book, scene_id=args.scene,
                        force=args.force, dry_run=args.dry_run)
    if args.dialogue:
        tts_mod.synth_dialogue(settings, script, book, scene_id=args.scene,
                               force=args.force, dry_run=args.dry_run)
    if not args.no_assemble and not args.dry_run:
        assemble_mod.assemble_scenes(script, scene_id=args.scene)


def cmd_run(args: argparse.Namespace, settings: Settings) -> None:
    # 0) Notion から取り込み(任意)
    inputs_dir = Path(args.inputs) if args.inputs else INPUTS_DIR
    if getattr(args, "notion_page", None):
        notion_mod.fetch_page_to_inputs(require_notion_token(settings), args.notion_page, inputs_dir)
    # 解析は常に Anthropic が要る。合成・キャスティングは dry-run なら ElevenLabs 不要。
    require_anthropic(settings)
    if not args.dry_run:
        require_elevenlabs(settings)
    # 1) analyze
    book = CharacterBook.load()
    bible = _load_bible(args, book)
    script = analyze_inputs(settings, inputs_dir, language=book.language, character_bible=bible)
    _save_script(script)
    # 2) cast (run では自動で書き戻す。dry-run は ElevenLabs を呼ばず既存割当のみ使用)
    if not args.dry_run:
        book = voices_mod.cast(settings, script, book, apply=True)
    # 3) synth + 4) assemble
    tts_mod.synth_clips(settings, script, book, scene_id=args.scene,
                        force=args.force, dry_run=args.dry_run)
    if args.dialogue:
        tts_mod.synth_dialogue(settings, script, book, scene_id=args.scene,
                               force=args.force, dry_run=args.dry_run)
    if not args.no_assemble and not args.dry_run:
        assemble_mod.assemble_scenes(script, scene_id=args.scene)
    print("[run] 完了。output/ を確認してください。")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="manga-voice",
        description="漫画・シナリオから ElevenLabs v3 でキャラに沿った感情ボイスを自動生成する",
    )
    p.add_argument("--model", help="解析に使う Claude モデル (既定 claude-opus-4-8)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("fetch-notion", help="Notionページの画像/テキストを inputs/ に取り込む")
    sp.add_argument("--page", required=True, help="NotionページのURLまたはID")
    sp.add_argument("--inputs", help="出力先ディレクトリ (既定 inputs/)")
    sp.set_defaults(func=cmd_fetch_notion)

    sp = sub.add_parser("analyze", help="inputs を解析して script.json を生成")
    sp.add_argument("--inputs", help="入力ディレクトリ (既定 inputs/)")
    sp.add_argument("--assets", help="キャラ設定資料ディレクトリ (既定 assets/characters/)")
    sp.add_argument("--notion-page", help="解析前にNotionページを取り込む(URL/ID)")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("cast", help="話者に ElevenLabs ボイスを割当(提案/--applyで保存)")
    sp.add_argument("--apply", action="store_true", help="characters.json に書き戻す")
    sp.set_defaults(func=cmd_cast)

    sp = sub.add_parser("validate", help="合成前の事前チェック(APIキー不要)")
    sp.add_argument("--scene", help="対象シーンID(省略時は全シーン)")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("synth", help="セリフを音声合成")
    sp.add_argument("--scene", help="対象シーンID(省略時は全シーン)")
    sp.add_argument("--force", action="store_true", help="既存音声を再生成")
    sp.add_argument("--dialogue", action="store_true", help="Text-to-Dialogue 掛け合いも生成")
    sp.add_argument("--no-assemble", action="store_true", help="clip連結をスキップ")
    sp.add_argument("--dry-run", action="store_true",
                    help="実APIを呼ばず合成計画をmanifestに書き出す(課金なし)")
    sp.set_defaults(func=cmd_synth)

    sp = sub.add_parser("run", help="解析→割当→合成→連結 を一括実行")
    sp.add_argument("--inputs", help="入力ディレクトリ (既定 inputs/)")
    sp.add_argument("--assets", help="キャラ設定資料ディレクトリ (既定 assets/characters/)")
    sp.add_argument("--notion-page", help="最初にNotionページを取り込む(URL/ID)")
    sp.add_argument("--scene", help="対象シーンID(省略時は全シーン)")
    sp.add_argument("--force", action="store_true", help="既存音声を再生成")
    sp.add_argument("--dialogue", action="store_true", help="Text-to-Dialogue 掛け合いも生成")
    sp.add_argument("--no-assemble", action="store_true", help="clip連結をスキップ")
    sp.add_argument("--dry-run", action="store_true",
                    help="合成は実APIを呼ばず計画のみ(解析は実行)")
    sp.set_defaults(func=cmd_run)

    # --- マルチエージェント・パイプライン ---
    pp = sub.add_parser("pipeline", help="シナリオ→作画→音声 を人間承認ゲート付きで連結")
    psub = pp.add_subparsers(dest="pcommand", required=True)

    sp = psub.add_parser("new", help="新しい run を作成")
    sp.add_argument("--id", help="run ID(省略時は日時)")
    sp.add_argument("--premise", help="シナリオの前提(省略時はテンプレを置いて停止)")
    sp.add_argument("--art-provider", default="manual", choices=["manual", "auto"],
                    help="作画プロバイダ(既定 manual=人手配置)")
    sp.add_argument("--rules", help="シナリオ制作ルールのパス(既定 config/scenario_rules.md)")
    sp.add_argument("--dialogue", action="store_true", help="合成でText-to-Dialogueも生成")
    sp.set_defaults(func=cmd_pipeline_new)

    sp = psub.add_parser("run", help="パイプラインを進める(ゲート/素材待ちで停止)")
    sp.add_argument("--run", help="run ID(省略時は最新)")
    sp.add_argument("--until", help="このステージまでで停止")
    sp.add_argument("--dry-run", action="store_true", help="合成は課金なしの計画のみ")
    sp.add_argument("--auto-approve", action="store_true",
                    help="人間ゲートを自動承認して一気通貫(テスト/全自動用)")
    sp.set_defaults(func=cmd_pipeline_run)

    sp = psub.add_parser("status", help="run の進捗を表示")
    sp.add_argument("--run", help="run ID(省略時は最新)")
    sp.set_defaults(func=cmd_pipeline_status)

    sp = psub.add_parser("approve", help="ステージの人間ゲートを承認")
    sp.add_argument("stage", help="承認するステージ名(scenario/art/analyze/cast)")
    sp.add_argument("--run", help="run ID(省略時は最新)")
    sp.set_defaults(func=cmd_pipeline_approve)

    sp = psub.add_parser("reject", help="ステージを差し戻して再実行可能にする")
    sp.add_argument("stage", help="差し戻すステージ名")
    sp.add_argument("--reason", help="差戻し理由")
    sp.add_argument("--run", help="run ID(省略時は最新)")
    sp.set_defaults(func=cmd_pipeline_reject)

    sp = psub.add_parser("list", help="run 一覧")
    sp.set_defaults(func=cmd_pipeline_list)

    return p


def _force_utf8_stdout() -> None:
    """Windows既定コンソール(cp932)で日本語/記号がエンコード不能になるのを防ぐ。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    ensure_dirs()
    settings = load_settings(model=args.model)
    args.func(args, settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
