import re
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


def test_privacy_notice_discloses_the_essentials_in_both_languages():
    for language in ("en", "tr"):
        text = TEXT[language]["privacy"]
        for required in ("crAK1644", "Telegram", "Oracle", "/delete_me", "90", "KVKK"):
            assert required in text, (language, required)
        assert "github.com/crAK1644/otobus_bot/issues" in text
        assert len(text) < 4000


async def test_command_menu_is_published_per_language():
    from unittest.mock import AsyncMock

    from iett_bot.__main__ import publish_commands

    bot = AsyncMock()
    await publish_commands(bot)
    calls = {
        call.kwargs["language_code"]: call.args[0] for call in bot.set_my_commands.await_args_list
    }
    assert calls.keys() == {"en", "tr"}
    assert [c.command for c in calls["en"]] == [c.command for c in calls["tr"]]
    assert (calls["en"][0].description, calls["tr"][0].description) == ("Start", "Başla")


def test_command_menu_fits_telegram_limits():
    from iett_bot.__main__ import COMMANDS

    for command, *descriptions in COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", command)
        assert all(3 <= len(text) <= 256 for text in descriptions)
    assert len({row[0] for row in COMMANDS}) == len(COMMANDS)
