import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from test_bot import RecordingSession

from iett_bot.domain import utcnow
from iett_bot.redirect import build_redirect_router


@pytest.mark.parametrize("language,phrase", [("en", "has moved"), ("tr", "taşındı")])
async def test_former_bot_messages_and_old_buttons_point_to_replacement(language, phrase):
    session = RecordingSession()
    bot = Bot("123456:TEST_TOKEN_NEVER_SENT_TO_TELEGRAM", session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_redirect_router("iett_notification_bot"))
    person = User(id=42, is_bot=False, first_name="Test", language_code=language)
    chat = Chat(id=42, type="private")
    try:
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=1,
                message=Message(
                    message_id=1, date=utcnow(), chat=chat, from_user=person, text="/lines 59RH"
                ),
            ),
        )
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=2,
                callback_query=CallbackQuery(
                    id="old-button",
                    from_user=person,
                    chat_instance="test",
                    data="remove:59RH",
                    message=Message(message_id=1, date=utcnow(), chat=chat, text="Old button"),
                ),
            ),
        )
        replies = [request for request in session.requests if isinstance(request, SendMessage)]
        assert len(replies) == 2
        for reply in replies:
            assert phrase in reply.text
            assert "https://t.me/iett_notification_bot" in reply.text
            assert reply.reply_markup.inline_keyboard[0][0].url == (
                "https://t.me/iett_notification_bot"
            )
    finally:
        await dispatcher.storage.close()
        await bot.session.close()


async def test_former_bot_does_not_redirect_group_messages():
    session = RecordingSession()
    bot = Bot("123456:TEST_TOKEN_NEVER_SENT_TO_TELEGRAM", session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_redirect_router("iett_notification_bot"))
    try:
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=1,
                message=Message(
                    message_id=1,
                    date=utcnow(),
                    chat=Chat(id=-42, type="group"),
                    from_user=User(id=42, is_bot=False, first_name="Test"),
                    text="/start",
                ),
            ),
        )
        assert not session.requests
    finally:
        await dispatcher.storage.close()
        await bot.session.close()
