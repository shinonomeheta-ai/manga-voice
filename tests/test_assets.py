from src.assets import load_character_bible
from src.config import CharacterBook
from src.models import Character


def test_load_bible_image_and_profile(tmp_path):
    (tmp_path / "太郎.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "太郎.md").write_text("元気な男子高校生", encoding="utf-8")
    (tmp_path / "花子.txt").write_text("しっかり者", encoding="utf-8")  # 画像なし
    bible = load_character_bible(tmp_path)
    by_name = {a.name: a for a in bible}
    assert by_name["太郎"].image_path is not None
    assert "元気" in by_name["太郎"].profile
    assert by_name["花子"].image_path is None
    assert by_name["花子"].profile == "しっかり者"


def test_load_bible_merges_characters_json_description(tmp_path):
    book = CharacterBook(characters={"太郎": Character(name="太郎", description="ノリが良い")})
    bible = load_character_bible(tmp_path, book)  # 空ディレクトリ + bookのdescription
    assert any(a.name == "太郎" and "ノリが良い" in a.profile for a in bible)


def test_load_bible_empty_dir_no_book(tmp_path):
    assert load_character_bible(tmp_path) == []


def test_load_bible_missing_dir(tmp_path):
    assert load_character_bible(tmp_path / "nope") == []
