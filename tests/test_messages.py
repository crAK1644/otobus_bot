from datetime import UTC, datetime

from iett_bot.domain import Line
from iett_bot.i18n import TEXT, notice_messages, split_text


def test_translation_keys_match():
    assert TEXT["tr"].keys() == TEXT["en"].keys()


def test_long_original_text_and_emoji_survive_chunking():
    original = "İptal duyurusu <b>özgün metin</b> 🚍\n" * 900
    pieces = split_text(original)
    assert "".join(pieces) == original
    assert all(len(part.encode("utf-16-le")) // 2 <= 3800 for part in pieces)
    messages = notice_messages(
        Line("59RH", "RUMELİ HİSARÜSTÜ - HACIOSMAN METRO"),
        original,
        datetime(2026, 10, 21, 4, 45, tzinfo=UTC),
        "en",
    )
    assert len(messages) > 1
    assert all(len(part.encode("utf-16-le")) // 2 <= 4096 for part in messages)
    assert "First detected: 21.10.2026 07:45 (Istanbul)" in messages[0]
    assert "https://iett.istanbul/RouteDetail?hkod=59RH" in messages[0]
