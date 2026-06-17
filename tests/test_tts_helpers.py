from src.config import CharacterBook
from src.models import Character, Line, Scene, Script
from src.tts import (
    DIALOGUE_CHAR_BUDGET,
    _chunk_dialogue,
    _ext_for,
    _stability_value,
)


def test_stability_value_mapping():
    assert _stability_value("creative") == 0.0
    assert _stability_value("natural") == 0.5
    assert _stability_value("robust") == 1.0
    assert _stability_value("unknown") == 0.5  # フォールバック


def test_ext_for():
    assert _ext_for("mp3_44100_128") == ".mp3"
    assert _ext_for("pcm_44100") == ".wav"
    assert _ext_for("ulaw_8000") == ".ulaw"
    assert _ext_for("weird") == ".mp3"


def _book_with(*names):
    book = CharacterBook()
    for n in names:
        book.characters[n] = Character(name=n, voice_id=f"v_{n}")
    return book


def test_chunk_dialogue_splits_on_budget():
    big = "あ" * (DIALOGUE_CHAR_BUDGET - 100)
    scene = Scene(id="s1", lines=[
        Line(speaker="A", text=big),
        Line(speaker="B", text="あ" * 300),  # ここで予算超過 -> 分割
    ])
    chunks = _chunk_dialogue(scene, _book_with("A", "B"))
    assert len(chunks) == 2
    assert chunks[0][0]["voice_id"] == "v_A"


def test_chunk_dialogue_tolerant_marks_unassigned():
    scene = Scene(id="s1", lines=[Line(speaker="X", text="hi")])
    chunks = _chunk_dialogue(scene, CharacterBook(), tolerant=True)
    assert chunks[0][0]["voice_id"] == "UNASSIGNED"


def test_chunk_dialogue_strict_raises_on_unassigned():
    import pytest
    scene = Scene(id="s1", lines=[Line(speaker="X", text="hi")])
    with pytest.raises(SystemExit):
        _chunk_dialogue(scene, CharacterBook(), tolerant=False)
