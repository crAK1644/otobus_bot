from datetime import UTC, datetime

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from iett_bot.bot import build_router
from iett_bot.config import Settings
from iett_bot.iett import IettClient

pytestmark = pytest.mark.integration


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109 -- aiogram interface
        self.requests.append(method)
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.requests),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
            )
        return True

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""


async def test_onboarding_settings_status_and_delete(store, fixture_json):
    person = User(id=42, is_bot=False, first_name="Test", language_code="en")
    chat = Chat(id=42, type="private")
    session = RecordingSession()
    bot = Bot("123456:TEST_TOKEN_NEVER_SENT_TO_TELEGRAM", session=session)
    dispatcher = Dispatcher()

    def respond(request):
        return httpx.Response(200, json=fixture_json("search_59rh.json"))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://iett.istanbul"
    ) as client:
        dispatcher.include_router(build_router(store, IettClient(client), Settings(_env_file=None)))
        update_id = 0

        async def message(text):
            nonlocal update_id
            update_id += 1
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=update_id,
                    message=Message(
                        message_id=update_id,
                        date=datetime.now(UTC),
                        chat=chat,
                        from_user=person,
                        text=text,
                    ),
                ),
            )

        async def callback(data):
            nonlocal update_id
            update_id += 1
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=update_id,
                    callback_query=CallbackQuery(
                        id=str(update_id),
                        from_user=person,
                        chat_instance="test",
                        data=data,
                        message=Message(
                            message_id=1, date=datetime.now(UTC), chat=chat, text="Button"
                        ),
                    ),
                ),
            )

        await message("/start")
        assert any("Welcome!" in (getattr(req, "text", "") or "") for req in session.requests)
        await message("/lines 59RH")
        assert (
            session.requests[-1].reply_markup.inline_keyboard[0][0].callback_data == "follow:59RH"
        )
        await callback("follow:59RH")
        assert len(await store.subscriptions(42)) == 1
        await message("/settings 59RH 1,2,3,4,5 07:00-10:00,15:00-19:00")
        assert "Hours saved" in session.requests[-1].text
        await callback("hours:59RH")
        await message("all")
        assert "Hours saved" in session.requests[-1].text
        await callback("lang:tr")
        assert (await store.user(42))["language"] == "tr"
        await message("/pause")
        assert (await store.user(42))["paused"]
        await message("/resume")
        assert not (await store.user(42))["paused"]
        await message("/status")
        assert "59RH" in session.requests[-1].text
        assert "İlk başarılı kontrol bekleniyor" in session.requests[-1].text
        await message("/delete_me")
        assert await store.user(42) is not None  # Confirmation is required for deletion.
        await callback("delete:yes")
        assert await store.user(42) is None
        assert "Kişisel verileriniz silindi" in session.requests[-1].text
    await dispatcher.storage.close()
    await bot.session.close()


async def test_group_messages_do_not_create_subscribers(store, fixture_json):
    session = RecordingSession()
    bot = Bot("123456:TEST_TOKEN_NEVER_SENT_TO_TELEGRAM", session=session)
    dispatcher = Dispatcher()
    async with httpx.AsyncClient(base_url="https://iett.istanbul") as client:
        dispatcher.include_router(build_router(store, IettClient(client), Settings(_env_file=None)))
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=1,
                message=Message(
                    message_id=1,
                    date=datetime.now(UTC),
                    chat=Chat(id=-123, type="group"),
                    from_user=User(id=42, is_bot=False, first_name="Test"),
                    text="/start",
                ),
            ),
        )
    assert await store.user(42) is None
    assert not session.requests
    await dispatcher.storage.close()
    await bot.session.close()
