import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.services.text_punctuation_buffer import PunctuationStreamBuffer


def test_buffer_flushes_on_comma_and_period() -> None:
    buf = PunctuationStreamBuffer()
    assert buf.feed("你好") == []
    out = buf.feed("，世界。")
    assert out == ["你好，", "世界。"]
    assert buf.drain() is None


def test_buffer_drains_remainder_without_trailing_punct() -> None:
    buf = PunctuationStreamBuffer()
    assert buf.feed("仅一段") == []
    assert buf.drain() == "仅一段"


def test_buffer_force_split_when_too_long() -> None:
    buf = PunctuationStreamBuffer(max_buffer_without_boundary=10)
    assert buf.feed("abcdefghijklmnop") == ["abcdefghij"]
    assert buf.drain() == "klmnop"
