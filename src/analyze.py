"""漫画画像・シナリオテキストを Claude で解析し Script を生成する。"""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from .config import Settings
from .models import Script
from .prompts import SCRIPT_TOOL, analysis_system_prompt

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
TEXT_EXT = {".txt", ".md"}
# Claude が受け付ける画像 media type
SUPPORTED_IMAGE_MEDIA = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def collect_inputs(inputs_dir: Path) -> tuple[list[Path], list[Path]]:
    """inputs ディレクトリから画像とテキストをファイル名順に収集する。"""
    images: list[Path] = []
    texts: list[Path] = []
    for p in sorted(inputs_dir.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext in IMAGE_EXT:
            images.append(p)
        elif ext in TEXT_EXT:
            texts.append(p)
    return images, texts


def _image_block(path: Path) -> dict[str, Any]:
    media_type, _ = mimetypes.guess_type(str(path))
    if media_type not in SUPPORTED_IMAGE_MEDIA:
        media_type = "image/png"
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def _build_content(images: list[Path], texts: list[Path]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for path in texts:
        body = path.read_text(encoding="utf-8", errors="replace")
        content.append({"type": "text", "text": f"# シナリオ/台本ファイル: {path.name}\n{body}"})
    for path in images:
        content.append({"type": "text", "text": f"# 漫画画像: {path.name}"})
        content.append(_image_block(path))
    if not content:
        raise SystemExit(
            "inputs/ に解析対象がありません。漫画画像(.png/.jpg)か台本(.txt/.md)を置いてください。"
        )
    content.append(
        {
            "type": "text",
            "text": "上記の素材すべてを通して解析し、record_script ツールで台本を返してください。",
        }
    )
    return content


def analyze(
    settings: Settings,
    inputs_dir: Path,
    language: str = "ja",
    max_tokens: int = 8000,
) -> Script:
    """inputs を解析して Script を返す。"""
    images, texts = collect_inputs(inputs_dir)
    client = Anthropic(api_key=settings.anthropic_api_key)
    content = _build_content(images, texts)

    print(f"[analyze] 画像 {len(images)} 枚 / テキスト {len(texts)} 件 を {settings.model} で解析中…")
    resp = client.messages.create(
        model=settings.model,
        max_tokens=max_tokens,
        system=analysis_system_prompt(language),
        tools=[SCRIPT_TOOL],
        tool_choice={"type": "tool", "name": "record_script"},
        messages=[{"role": "user", "content": content}],
    )

    tool_input = _extract_tool_input(resp)
    script = Script.from_dict(tool_input)
    if not script.language:
        script.language = language
    n_lines = sum(len(s.lines) for s in script.scenes)
    print(f"[analyze] 完了: {len(script.scenes)} シーン / {n_lines} セリフ / 話者 {script.all_speakers()}")
    return script


def _extract_tool_input(resp: Any) -> dict[str, Any]:
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_script":
            return dict(block.input)
    raise RuntimeError(
        "Claude が record_script ツールを返しませんでした。応答: "
        + json.dumps([getattr(b, "type", "?") for b in resp.content])
    )
