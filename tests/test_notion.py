import pytest

from src.notion import NotionError, extract_page_id


@pytest.mark.parametrize("raw,expected", [
    ("1234567890abcdef1234567890abcdef", "12345678-90ab-cdef-1234-567890abcdef"),
    ("12345678-90ab-cdef-1234-567890abcdef", "12345678-90ab-cdef-1234-567890abcdef"),
    ("https://www.notion.so/My-Page-1234567890abcdef1234567890abcdef?pvs=4",
     "12345678-90ab-cdef-1234-567890abcdef"),
    ("https://notion.so/ws/Title-deadbeefdeadbeefdeadbeefdeadbeef#block",
     "deadbeef-dead-beef-dead-beefdeadbeef"),
])
def test_extract_page_id(raw, expected):
    assert extract_page_id(raw) == expected


def test_extract_page_id_strips_title_hex_letters():
    # タイトル末尾の 'e'(16進)がIDに食い込まないこと
    url = "https://www.notion.so/Page-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert extract_page_id(url) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_extract_page_id_invalid():
    with pytest.raises(NotionError):
        extract_page_id("no-hex-here")


def test_notion_error_is_catchable_as_exception():
    """NotionError は通常の Exception。Webアプリの except Exception で捕捉でき、
    SystemExit のように握りつぶされない(進捗バーだけ消える問題の再発防止)。"""
    assert issubclass(NotionError, Exception)
    try:
        extract_page_id("invalid")
    except Exception as e:  # noqa: BLE001
        assert isinstance(e, NotionError)
    else:
        pytest.fail("NotionError が送出されませんでした")
