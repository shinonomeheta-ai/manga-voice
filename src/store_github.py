"""GitHub をプロジェクト保存先(簡易DB)として使う層。

Webアプリの「クラウド保存」用。プロジェクト(台本＋設定＋キャスト)のJSONを、
同一リポの別ブランチ(既定 data)の projects/<slug>.json として読み書きする。
コード(main)と混ざらず、main/master 限定の CI も走らない。

- 依存を増やさないため標準ライブラリ(urllib)のみ。
- 認証: リポジトリの Contents 読み書き権限を持つ PAT(ファイングレインド推奨)。
- 失敗は通常例外 GitHubError(呼び出し側の except Exception で捕捉可能)。
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"


class GitHubError(RuntimeError):
    """GitHub保存の失敗。通常例外なので Webアプリの except Exception で捕捉できる。"""


def slug(name: str) -> str:
    """プロジェクト名をファイル名に使える安全な文字列へ(日本語は残す)。"""
    s = re.sub(r"[^0-9A-Za-z぀-ヿ一-鿿_-]+", "_", (name or "").strip())
    return s[:60].strip("_") or "project"


def names_from_listing(entries: list[dict[str, Any]]) -> list[str]:
    """contents API のディレクトリ応答から .json ファイルの名前(拡張子なし)を抽出。"""
    out: list[str] = []
    for e in entries:
        if e.get("type") == "file" and str(e.get("name", "")).endswith(".json"):
            out.append(e["name"][:-5])
    return sorted(out)


class GitHubStore:
    def __init__(self, token: str, repo: str, branch: str = "data",
                 directory: str = "projects"):
        if not token or not repo:
            raise GitHubError("GITHUB_TOKEN と GITHUB_REPO を設定してください。")
        self.token = token
        self.repo = repo.strip().strip("/")
        self.branch = branch
        self.directory = directory.strip("/")

    # --- 低レベル ---
    def _request(self, method: str, path: str,
                 body: dict[str, Any] | None = None) -> tuple[int, Any]:
        url = f"{API_BASE}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", API_VERSION)
        req.add_header("User-Agent", "manga-voice-app")  # GitHub は UA 必須
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, None
            detail = e.read().decode("utf-8", "replace")
            raise GitHubError(
                f"GitHub API エラー {e.code} ({method} {path}): {detail}\n"
                f"PAT の権限(Contents 読み書き)とリポジトリ名/ブランチを確認してください。"
            )

    def _path(self, name: str) -> str:
        # 日本語ファイル名はURLに生で入れられないためパーセントエンコードする。
        p = urllib.parse.quote(f"{self.directory}/{slug(name)}.json")
        return f"/repos/{self.repo}/contents/{p}"

    # --- 公開API ---
    def ensure_branch(self) -> None:
        """保存先ブランチが無ければ main(無ければ master)から作成する。"""
        st, _ = self._request("GET", f"/repos/{self.repo}/branches/{self.branch}")
        if st == 200:
            return
        for base in ("main", "master"):
            st, ref = self._request("GET", f"/repos/{self.repo}/git/ref/heads/{base}")
            if st == 200 and ref:
                sha = ref["object"]["sha"]
                self._request("POST", f"/repos/{self.repo}/git/refs",
                              {"ref": f"refs/heads/{self.branch}", "sha": sha})
                return
        raise GitHubError("作成元ブランチ(main/master)が見つかりません。")

    def list_projects(self) -> list[str]:
        st, data = self._request(
            "GET", f"/repos/{self.repo}/contents/{self.directory}?ref={self.branch}")
        if st == 404 or not data:
            return []
        return names_from_listing(data if isinstance(data, list) else [])

    def save_project(self, name: str, project: dict[str, Any]) -> None:
        self.ensure_branch()
        path = self._path(name)
        st, existing = self._request("GET", f"{path}?ref={self.branch}")
        content = base64.b64encode(
            json.dumps(project, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("ascii")
        body: dict[str, Any] = {
            "message": f"save project: {slug(name)}",
            "content": content,
            "branch": self.branch,
        }
        if st == 200 and existing:
            body["sha"] = existing["sha"]  # 既存は上書き(sha必須)
        self._request("PUT", path, body)

    def load_project(self, name: str) -> dict[str, Any]:
        st, data = self._request("GET", f"{self._path(name)}?ref={self.branch}")
        if st == 404 or not data:
            raise GitHubError(f"プロジェクトが見つかりません: {name}")
        raw = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(raw)

    def delete_project(self, name: str) -> None:
        path = self._path(name)
        st, existing = self._request("GET", f"{path}?ref={self.branch}")
        if st == 404 or not existing:
            return
        self._request("DELETE", path, {
            "message": f"delete project: {slug(name)}",
            "sha": existing["sha"],
            "branch": self.branch,
        })
