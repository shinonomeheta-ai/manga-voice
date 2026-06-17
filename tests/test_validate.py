from src.config import CharacterBook
from src.models import Character, Line, Scene, Script
from src.validate import validate


def _script(*lines):
    return Script(scenes=[Scene(id="s1", lines=list(lines))])


def _book(*names):
    book = CharacterBook()
    for n in names:
        book.characters[n] = Character(name=n, voice_id=f"v_{n}", stability="natural")
    return book


def test_validate_ok():
    s = _script(Line(speaker="A", text="hi", emotion="happy", audio_tags=["[happy]"]))
    assert validate(s, _book("A")) is True


def test_validate_unassigned_speaker_is_error():
    s = _script(Line(speaker="A", text="hi"))
    assert validate(s, CharacterBook()) is False


def test_validate_unknown_audio_tag_is_error():
    s = _script(Line(speaker="A", text="hi", audio_tags=["[teleport]"]))
    assert validate(s, _book("A")) is False


def test_validate_empty_text_is_error():
    s = _script(Line(speaker="A", text="   "))
    assert validate(s, _book("A")) is False


def test_validate_over_char_limit_is_error():
    s = _script(Line(speaker="A", text="あ" * 5001))
    assert validate(s, _book("A")) is False


def test_validate_unknown_stability_is_warning_only():
    s = _script(Line(speaker="A", text="hi"))
    book = _book("A")
    book.characters["A"].stability = "weird"
    # 警告のみ。errors は無いので True。
    assert validate(s, book) is True
