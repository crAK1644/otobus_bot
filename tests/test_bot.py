from datetime import UTC, datetime, timedelta

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from iett_bot.bot import build_router, start_payload
from iett_bot.config import Settings
from iett_bot.domain import utcnow
from iett_bot.iett import IettClient

pytestmark = pytest.mark.integration


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.get_me_fails = False

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109 -- aiogram interface
        self.requests.append(method)
        if isinstance(method, GetMe):
            if self.get_me_fails:
                raise TelegramNetworkError(method=method, message="simulated outage")
            return User(id=999, is_bot=True, first_name="IETT", username="iett_notification_bot")
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
        if request.url.path.endswith("GetLineStatus"):
            return httpx.Response(200, json=[])
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
        assert "No notices" in session.requests[-1].text
        await message("/settings 59RH 1,2,3,4,5 07:00-10:00,15:00-19:00")
        assert "Hours saved" in session.requests[-1].text
        await callback("hours:59RH")
        await message("all")
        assert "Hours saved" in session.requests[-1].text
        await callback("lang:tr")
        assert (await store.user(42))["language"] == "tr"
        await message("/privacy")
        assert "Gizlilik" in session.requests[-1].text  # language was switched to Turkish above
        assert "github.com/crAK1644/otobus_bot/issues" in session.requests[-1].text
        await message("/pause")
        assert (await store.user(42))["paused"]
        await message("/resume")
        assert not (await store.user(42))["paused"]
        await message("/status")
        assert "59RH" in session.requests[-1].text
        assert "Son başarılı kontrol" in session.requests[-1].text
        assert "duyuru yok" in session.requests[-1].text
        await message("/delete_me")
        assert await store.user(42) is not None  # Confirmation is required for deletion.
        await callback("delete:yes")
        assert await store.user(42) is None
        assert "Kişisel verileriniz silindi" in session.requests[-1].text
    await dispatcher.storage.close()
    await bot.session.close()


class Conversation:
    def __init__(self, dispatcher, bot, session):
        self.dispatcher, self.bot, self.session = dispatcher, bot, session
        self.person = User(id=42, is_bot=False, first_name="Test", language_code="en")
        self.chat = Chat(id=42, type="private")
        self.notices = {"59RH": [], "43R": ["TEST: 43R güzergâh değişikliği."]}
        self.failed = set()
        self.update_id = 0

    def respond(self, request):
        if request.url.path.endswith("GetSearchItems"):
            code = request.url.params["key"]
            return httpx.Response(
                200,
                json={"list": [{"Code": code, "Name": code, "Path": f"/RouteDetail?hkod={code}"}]},
            )
        code = request.url.params["rcode"]
        return (
            httpx.Response(503)
            if code in self.failed
            else httpx.Response(200, json=self.notices[code])
        )

    async def send(self, text=None, callback=None):
        self.update_id += 1
        message = Message(
            message_id=self.update_id,
            date=utcnow(),
            chat=self.chat,
            from_user=self.person,
            text=text or "Button",
        )
        if callback:
            update = Update(
                update_id=self.update_id,
                callback_query=CallbackQuery(
                    id=str(self.update_id),
                    from_user=self.person,
                    chat_instance="test",
                    data=callback,
                    message=message,
                ),
            )
        else:
            update = Update(update_id=self.update_id, message=message)
        await self.dispatcher.feed_update(self.bot, update)
        return self.session.requests[-1].text


@pytest.fixture
async def conversation(store):
    session = RecordingSession()
    bot = Bot("123456:TEST_TOKEN_NEVER_SENT_TO_TELEGRAM", session=session)
    dispatcher = Dispatcher()
    conversation = Conversation(dispatcher, bot, session)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(conversation.respond), base_url="https://iett.istanbul"
    ) as client:
        dispatcher.include_router(build_router(store, IettClient(client), Settings(_env_file=None)))
        yield conversation
    await dispatcher.storage.close()
    await bot.session.close()


async def test_follow_multiple_lines_and_read_current_notices_while_paused(store, conversation):
    empty = await conversation.send(callback="follow:59RH")
    assert "No notices" in empty and "59RH" in empty
    present = await conversation.send(callback="follow:43R")
    assert "latest successful check: 1" in present
    assert len(await store.subscriptions(42)) == 2
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 1
    await conversation.send("/pause")
    all_lines = await conversation.send("/status")
    assert "59RH" in all_lines and "43R" in all_lines
    assert conversation.notices["43R"][0] in all_lines
    assert "No notices" in all_lines and "First detected" in all_lines
    one_line = await conversation.send("/status 59RH")
    assert "59RH" in one_line and "43R" not in one_line
    button = await conversation.send(callback="check:43R")
    assert conversation.notices["43R"][0] in button
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 1
    assert "Add this line" in await conversation.send("/status 500T")


async def test_status_captures_new_notices_and_failed_refresh_keeps_last_result(
    store, conversation
):
    await conversation.send(callback="follow:59RH")
    conversation.notices["59RH"] = ["TEST: 59RH seferi iptal edilmiştir."]
    await store.pool.execute(
        "UPDATE lines SET last_success_at=$1 WHERE code='59RH'", utcnow() - timedelta(seconds=11)
    )
    text = await conversation.send("/status")
    assert conversation.notices["59RH"][0] in text
    assert await store.pool.fetchval("SELECT count(*) FROM snapshots") == 2
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 1
    await conversation.send("/status")
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 1
    conversation.failed.add("59RH")
    await store.pool.execute(
        "UPDATE lines SET last_success_at=$1 WHERE code='59RH'", utcnow() - timedelta(seconds=11)
    )
    text = await conversation.send("/status")
    assert "couldn’t refresh" in text and "unknown" in text
    assert conversation.notices["59RH"][0] in text
    await conversation.send("/status")  # Backoff is respected even on repeated requests.
    assert await store.pool.fetchval("SELECT failure_count FROM lines") == 1
    assert await store.pool.fetchval("SELECT count(*) FROM snapshots") == 2


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


async def test_privacy_notice_never_creates_a_user_record_and_honors_stored_language(
    store, conversation
):
    english = await conversation.send("/privacy")  # Telegram language is English
    assert "Controller" in english and "KVKK" in english
    assert await store.user(42) is None  # reading the notice stores nothing
    await store.ensure_user(42, "tr")
    assert "Veri sorumlusu" in await conversation.send("/privacy")  # saved choice wins


def sent_texts(conversation):
    return [getattr(req, "text", "") or "" for req in conversation.session.requests]


async def test_start_link_follows_the_line_checks_it_and_offers_a_share_link(store, conversation):
    await conversation.send("/start 59RH")
    assert [sub["line_code"] for sub in await store.subscriptions(42)] == ["59RH"]
    texts = sent_texts(conversation)
    assert any("Welcome!" in text for text in texts)
    assert any("https://t.me/iett_notification_bot?start=59RH" in text for text in texts)
    assert "No notices" in texts[-1]  # the immediate check ran


async def test_start_link_with_turkish_letters_round_trips(store, conversation):
    conversation.notices["1ŞÜ"] = []
    await conversation.send("/start " + start_payload("1ŞÜ"))
    assert [sub["line_code"] for sub in await store.subscriptions(42)] == ["1ŞÜ"]
    assert f"?start={start_payload('1ŞÜ')}" in "".join(sent_texts(conversation))


async def test_malformed_start_link_gets_the_normal_welcome(store, conversation):
    for payload in ("t_!!!", "not a line", "x" * 70):
        await conversation.send("/start " + payload)
    assert await store.subscriptions(42) == []
    assert all("Following" not in text for text in sent_texts(conversation))
    assert sum("Welcome!" in text for text in sent_texts(conversation)) == 3


async def test_start_link_still_checks_the_line_when_the_bot_username_is_unavailable(
    store, conversation
):
    conversation.session.get_me_fails = True
    await conversation.send("/start 59RH")
    assert [sub["line_code"] for sub in await store.subscriptions(42)] == ["59RH"]
    texts = sent_texts(conversation)
    assert any(text.startswith("Following 59RH") and "t.me" not in text for text in texts)
    assert "No notices" in texts[-1]  # the immediate check still ran
