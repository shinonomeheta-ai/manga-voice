import pytest

from src import works
from src.works import DEFAULT_WORK, Work


def test_resolve_default_uses_global():
    w = works.resolve_work(None)
    assert w.name == DEFAULT_WORK and w.base is None
    assert w.characters_path.name == "characters.json"


def test_resolve_missing_work_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(works, "WORKS_DIR", tmp_path)
    with pytest.raises(SystemExit):
        works.resolve_work("does-not-exist")


def test_init_and_resolve_work(monkeypatch, tmp_path):
    monkeypatch.setattr(works, "WORKS_DIR", tmp_path)
    w = works.init_work("sf")
    assert isinstance(w, Work)
    assert w.characters_path.exists()           # 雛形 characters.json
    assert w.rules_path.exists()                # 雛形 rules
    assert (tmp_path / "sf" / "assets" / "characters").is_dir()
    assert "sf" in works.list_works()
    # 解決でも同じパスを指す
    assert works.resolve_work("sf").characters_path == w.characters_path


def test_work_of_options(monkeypatch, tmp_path):
    monkeypatch.setattr(works, "WORKS_DIR", tmp_path)
    works.init_work("pachinko")
    w = works.work_of({"work": "pachinko"})
    assert w.name == "pachinko"
    assert works.work_of({}).name == DEFAULT_WORK
