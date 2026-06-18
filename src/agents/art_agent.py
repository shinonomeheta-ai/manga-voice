"""作画担当エージェント。

プロバイダ方式:
- manual (既定): 人間が run_dir/art/pages/ にページ画像を置く。画像があれば art.json を
  生成、無ければ needs_input で配置を促す。現状の人手作画ワークフローに対応。
- auto (stub): 画像生成プロバイダによる自動作画。プロバイダ未確定のため未実装。
  将来ここに画像生成APIアダプタを差し込む(インターフェースは run() と art.json 出力で固定)。

ctx.options["art_provider"] で切替(既定 "manual")。
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import AgentResult, RunContext

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class ArtAgent:
    name = "art"

    def run(self, ctx: RunContext) -> AgentResult:
        provider = str(ctx.options.get("art_provider", "manual")).lower()
        art_dir = ctx.stage_dir("art")
        pages_dir = art_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        if provider == "manual":
            return self._manual(ctx, art_dir, pages_dir)
        if provider == "auto":
            return AgentResult.error(
                "art_provider=auto は未実装です(画像生成プロバイダ未設定)。"
                "manual で人手作画を配置するか、画像生成アダプタを実装してください。")
        return AgentResult.error(f"未知の art_provider: {provider}")

    @staticmethod
    def _manual(ctx: RunContext, art_dir: Path, pages_dir: Path) -> AgentResult:
        images = sorted(p for p in pages_dir.iterdir()
                        if p.is_file() and p.suffix.lower() in IMAGE_EXT)
        if not images:
            return AgentResult.needs_input(
                f"ページ画像がありません。{ctx.rel(pages_dir)} に読み順で画像を置いてから再実行してください。")
        scenario_ref = "scenario/scenario.json"
        manifest = {
            "version": "1.0", "provider": "manual",
            "scenario_ref": scenario_ref if (ctx.run_dir / scenario_ref).exists() else "",
            "pages": [
                {"index": i, "image": ctx.rel(p), "scene_id": "", "caption": p.stem}
                for i, p in enumerate(images)
            ],
        }
        out = art_dir / "art.json"
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return AgentResult.ok([ctx.rel(out)], message=f"{len(images)}ページ")
