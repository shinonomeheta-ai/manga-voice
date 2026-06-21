"""Notion 取り込み層: ページのブロックを走査して画像DL+テキスト抽出 → inputs/。

Notion でページに貼った漫画画像・台本テキストを、既存の analyze がそのまま読める
ファイル(画像 + テキスト)として inputs/ に書き出す。

- Notion の画像URL(file.url)は約1時間で失効するため、取得した直後にダウンロードする。
- 追加依存を増やさないため標準ライブラリ(urllib)のみを使う。
- 認証: 内部インテグレーションのトークン。対象ページをそのインテグレーションに
  「共有(Connections)」しておく必要がある。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

NOTION_VERSION = "2026-03-11"
API_BASE = "https://api.notion.com/v1"

# プレーンテキストを持つブロック種別 -> 接頭辞(整形用)
TEXT_BLOCKS: dict[str, str] = {
    "paragraph": "",
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "- ",
    "to_do": "- ",
    "quote": "> ",
    "callout": "",
    "toggle": "",
}
IMAGE_EXT_BY_CT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def extract_page_id(url_or_id: str) -> str:
    """NotionのURL/IDから32桁hexを取り出しダッシュ付きUUIDに整形する。

    タイトル付きURL("...My-Page-<32hex>")でタイトル末尾の16進文字がIDと連結するのを
    避けるため、クエリ(?)とフラグメント(#)を除いた上で「末尾32桁」を採用する。
    """
    s = url_or_id.strip().split("?")[0].split("#")[0]
    hex_chars = re.findall(r"[0-9a-fA-F]", s)
    if len(hex_chars) < 32:
        raise SystemExit(f"Notion ページID/URL を解釈できません: {url_or_id}")
    h = "".join(hex_chars[-32:]).lower()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


class NotionClient:
    def __init__(self, token: str):
        self.token = token

    def _request(self, method: str, path: str) -> dict[str, Any]:
        req = urllib.request.Request(f"{API_BASE}{path}", method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Notion-Version", NOTION_VERSION)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise SystemExit(
                f"Notion API エラー {e.code} ({path}): {body}\n"
                f"トークンが正しいか、対象ページをインテグレーションに共有済みか確認してください。"
            )

    def page_title(self, page_id: str) -> str:
        data = self._request("GET", f"/pages/{page_id}")
        props = data.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                return _rich_text(prop.get("title", [])) or "notion-page"
        return "notion-page"

    def iter_blocks(self, block_id: str) -> Iterator[dict[str, Any]]:
        """ブロックを順序通りに、子も再帰的に列挙する(ページネーション対応)。"""
        cursor: str | None = None
        while True:
            q = f"?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
            data = self._request("GET", f"/blocks/{block_id}/children{q}")
            for block in data.get("results", []):
                yield block
                if block.get("has_children"):
                    yield from self.iter_blocks(block["id"])
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")


def _rich_text(items: list[dict[str, Any]]) -> str:
    return "".join(i.get("plain_text", "") for i in items).strip()


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as resp:
        ct = resp.headers.get("Content-Type", "").split(";")[0].strip()
        data = resp.read()
    # Content-Type から拡張子を確定(URLのクエリで判別できないことがあるため)
    ext = IMAGE_EXT_BY_CT.get(ct)
    if ext and dest.suffix.lower() not in IMAGE_EXT_BY_CT.values():
        dest = dest.with_suffix(ext)
    dest.write_bytes(data)


def _download_bytes(url: str) -> tuple[bytes, str]:
    """URLから画像を取得し (bytes, 拡張子) を返す。"""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as resp:
        ct = resp.headers.get("Content-Type", "").split(";")[0].strip()
        data = resp.read()
    return data, IMAGE_EXT_BY_CT.get(ct, ".png")


def _image_url(block: dict[str, Any]) -> str | None:
    img = block.get("image") or {}
    if img.get("type") == "external":
        return (img.get("external") or {}).get("url")
    if img.get("type") == "file":
        return (img.get("file") or {}).get("url")
    return None


def fetch_page_image_items(token: str, page_ref: str) -> list[tuple[str, bytes]]:
    """Notionページ内の画像を順に取得し [(拡張子, バイト列)] を返す(ファイル保存しない)。

    Webアプリの画像取り込み(Claude Vision文字起こし)にそのまま渡せる形式。
    画像URLは約1時間で失効するため、取得時にその場でダウンロードする。
    """
    client = NotionClient(token)
    page_id = extract_page_id(page_ref)
    items: list[tuple[str, bytes]] = []
    for block in client.iter_blocks(page_id):
        if block.get("type") != "image":
            continue
        url = _image_url(block)
        if not url:
            continue
        try:
            data, ext = _download_bytes(url)
            items.append((ext, data))
        except Exception as e:  # noqa: BLE001
            print(f"[notion] 画像DL失敗(失効URLの可能性): {e}")
    return items


def fetch_page_to_inputs(token: str, page_ref: str, inputs_dir: Path) -> list[Path]:
    """Notionページを inputs/ に展開する。保存したファイルのパス一覧を返す。

    テキストは1つの .txt にまとめ、画像出現位置には【画像: ファイル名】マーカーを
    残すことで、漫画のコマ順を解析(analyze)側が把握できるようにする。
    """
    inputs_dir.mkdir(parents=True, exist_ok=True)
    client = NotionClient(token)
    page_id = extract_page_id(page_ref)
    title = client.page_title(page_id)
    slug = re.sub(r"[^0-9A-Za-z぀-ヿ一-鿿-]+", "_", title)[:40] or "notion"
    prefix = f"notion_{slug}_{page_id[:8]}"

    saved: list[Path] = []
    text_lines: list[str] = [f"# Notion: {title} ({page_id})", ""]
    img_count = 0

    print(f"[notion] ページ取得: {title} ({page_id})")
    for block in client.iter_blocks(page_id):
        btype = block.get("type", "")
        if btype == "image":
            url = _image_url(block)
            if not url:
                continue
            img_count += 1
            dest = inputs_dir / f"{prefix}_img{img_count:03d}.png"
            try:
                _download(url, dest)
            except Exception as e:  # noqa: BLE001
                print(f"  ! 画像DL失敗(失効URLの可能性): {e}")
                continue
            # _download が拡張子を補正している場合に追従
            actual = next(inputs_dir.glob(f"{prefix}_img{img_count:03d}.*"), dest)
            saved.append(actual)
            text_lines.append(f"【画像: {actual.name}】")
            print(f"  + 画像 {actual.name}")
        elif btype in TEXT_BLOCKS:
            content = block.get(btype, {})
            txt = _rich_text(content.get("rich_text", []))
            if txt:
                text_lines.append(TEXT_BLOCKS[btype] + txt)
        elif btype == "divider":
            text_lines.append("")

    text_path = inputs_dir / f"{prefix}.txt"
    text_path.write_text("\n".join(text_lines) + "\n", encoding="utf-8")
    saved.append(text_path)
    print(f"[notion] テキスト -> {text_path.name} / 画像 {img_count} 枚 を inputs/ に保存")
    return saved
