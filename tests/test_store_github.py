"""GitHub保存層(store_github)のオフラインテスト。実際のAPIは叩かない。"""
import base64
import json

import pytest

from src.store_github import GitHubError, GitHubStore, names_from_listing, slug


def test_slug_safe_and_keeps_japanese():
    assert slug(" ななし ちゃん!! ") == "ななし_ちゃん"
    assert slug("") == "project"
    assert slug("a/b\\c") == "a_b_c"


def test_names_from_listing_filters_json():
    entries = [
        {"type": "file", "name": "b.json"},
        {"type": "file", "name": "a.json"},
        {"type": "file", "name": "note.txt"},
        {"type": "dir", "name": "sub"},
    ]
    assert names_from_listing(entries) == ["a", "b"]


def test_requires_token_and_repo():
    with pytest.raises(GitHubError):
        GitHubStore("", "owner/repo")
    with pytest.raises(GitHubError):
        GitHubStore("tok", "")


def test_save_then_load_roundtrip(monkeypatch):
    store = GitHubStore("tok", "owner/repo")
    state: dict[str, bytes] = {}
    calls: list[str] = []

    def fake_request(method, path, body=None):
        p = path.split("?")[0]
        calls.append(f"{method} {p}")
        # ブランチ存在チェックはOK扱い
        if method == "GET" and "/branches/" in p:
            return 200, {"name": "data"}
        # ディレクトリ一覧
        if method == "GET" and p.endswith("/contents/projects"):
            return 200, [{"type": "file", "name": "第1話.json"}]
        # ファイル取得: 保存済みなら返す、無ければ404
        if method == "GET" and "/contents/projects/" in p:
            if p in state:
                return 200, {"content": base64.b64encode(state[p]).decode(),
                             "sha": "sha1"}
            return 404, None
        if method == "PUT" and "/contents/projects/" in p:
            state[p] = base64.b64decode(body["content"])
            return 200, {"content": {}}
        raise AssertionError(f"想定外: {method} {path}")

    monkeypatch.setattr(store, "_request", fake_request)

    proj = {"name": "第1話", "blocks": [{"speaker": "太郎", "text": "やあ"}]}
    store.save_project("第1話", proj)
    assert store.load_project("第1話") == proj
    assert store.list_projects() == ["第1話"]
    # PUT が呼ばれている(=保存処理が走った)
    assert any(c.startswith("PUT ") for c in calls)
