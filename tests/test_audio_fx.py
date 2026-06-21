from src.audio_fx import DEFAULT_PRESET, PRESETS, build_filter, ffmpeg_available


def test_presets_exist():
    assert {"natural", "clean", "warm"} <= set(PRESETS)
    assert DEFAULT_PRESET == "natural"


def test_build_filter_natural_has_core_chain():
    f = build_filter("natural")
    assert "highpass=f=80" in f
    assert "loudnorm" in f and "alimiter" in f
    assert "acompressor" in f


def test_build_filter_clean_is_minimal():
    f = build_filter("clean")
    assert "loudnorm" in f
    assert "acompressor" not in f and "aecho" not in f


def test_build_filter_warm_has_warmth_and_air():
    f = build_filter("warm")
    assert "bass=" in f and "treble=" in f and "aecho" in f


def test_unknown_preset_falls_back_to_natural():
    assert build_filter("nope") == build_filter("natural")


def test_filter_is_comma_joined():
    assert "," in build_filter("natural")


def test_ffmpeg_available_returns_bool():
    assert isinstance(ffmpeg_available(), bool)
