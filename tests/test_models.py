from src.models import Character, Line, Script


def test_resolved_tts_text_prefers_explicit():
    line = Line(speaker="A", text="hi", audio_tags=["[excited]"], tts_text="[excited] hi!")
    assert line.resolved_tts_text() == "[excited] hi!"


def test_resolved_tts_text_builds_from_tags():
    line = Line(speaker="A", text="やったー", audio_tags=["[excited]", "[laughs]"])
    assert line.resolved_tts_text() == "[excited] [laughs] やったー"


def test_resolved_tts_text_no_tags():
    assert Line(speaker="A", text="plain").resolved_tts_text() == "plain"


def test_line_from_dict_defaults_speaker():
    line = Line.from_dict({"text": "x"})
    assert line.speaker == "ナレーター"
    assert line.emotion == "neutral"


def test_all_speakers_order_and_dedup():
    s = Script.from_dict({
        "characters": ["太郎"],
        "scenes": [{"id": "s1", "lines": [
            {"speaker": "花子", "text": "a"},
            {"speaker": "太郎", "text": "b"},
            {"speaker": "花子", "text": "c"},
        ]}],
    })
    assert s.all_speakers() == ["太郎", "花子"]


def test_script_roundtrip():
    data = {"title": "t", "language": "ja", "scenes": [
        {"id": "s1", "description": "d", "lines": [
            {"speaker": "A", "text": "hi", "emotion": "happy",
             "audio_tags": ["[happy]"], "tts_text": "[happy] hi"}]}]}
    s = Script.from_dict(data)
    again = Script.from_dict(s.to_dict())
    assert again.scenes[0].lines[0].tts_text == "[happy] hi"
    assert again.all_speakers() == ["A"]


def test_character_assigned_and_seed_parsing():
    c = Character.from_dict("A", {"voice_id": "v1", "seed": "42"})
    assert c.is_assigned() and c.seed == 42
    assert not Character.from_dict("B", {"voice_id": ""}).is_assigned()
