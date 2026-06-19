from src.assemble import BASE_GAP_MS, MAX_GAP_MS, MIN_GAP_MS, gap_ms
from src.models import Line


def L(speaker="A", text="x", emotion="neutral"):
    return Line(speaker=speaker, text=text, emotion=emotion)


def test_first_line_no_gap():
    assert gap_ms(None, L()) == 0


def test_quick_reaction_tightens():
    # ツッコミ(別話者・即反応)は基準より短い
    g = gap_ms(L("A", "なあ聞いてくれ"), L("B", "うるさい", emotion="angry"))
    assert g < BASE_GAP_MS


def test_slow_prev_opens_gap():
    # 前が余韻系(sad) → 間が開く
    g = gap_ms(L("A", "……そうか。", emotion="sad"), L("B", "うん", emotion="calm"))
    assert g > BASE_GAP_MS


def test_question_then_quick_answer_is_shortest():
    q = gap_ms(L("A", "今から行く？"), L("B", "行く！", emotion="excited"))
    plain = gap_ms(L("A", "今から行く"), L("B", "行く", emotion="neutral"))
    assert q < plain


def test_same_speaker_breath_longer_than_turn_change():
    same = gap_ms(L("A", "それでね", emotion="neutral"), L("A", "つまり", emotion="neutral"))
    turn = gap_ms(L("A", "それでね", emotion="neutral"), L("B", "なに", emotion="neutral"))
    assert same > turn


def test_gap_clamped():
    g = gap_ms(L("A", "……", emotion="sad"), L("A", "ねえ", emotion="calm"))
    assert MIN_GAP_MS <= g <= MAX_GAP_MS
