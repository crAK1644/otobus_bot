from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

from iett_bot.delivery import DeliveryWorker
from iett_bot.domain import utcnow


def setup_job():
    store = Mock()
    job = {"id": 1, "chat_id": 42, "messages": ["Test notice"], "next_part": 0, "attempts": 0}
    store.claim_delivery = AsyncMock(return_value=job)
    store.user = AsyncMock(return_value={"paused": False, "blocked": False})
    store.delivery_allowed = AsyncMock(return_value=True)
    for name in ("retry_delivery", "delivery_part_sent", "block_user", "discard_delivery"):
        setattr(store, name, AsyncMock())
    bot = Mock(send_message=AsyncMock())
    limiter = Mock(wait=AsyncMock())
    return store, bot, limiter


async def test_rate_limit_reschedules_same_part():
    store, bot, limiter = setup_job()
    before = utcnow()
    bot.send_message.side_effect = TelegramRetryAfter(
        method=SendMessage(chat_id=42, text="test"), message="Rate limited", retry_after=30
    )
    assert await DeliveryWorker(store, bot, limiter).once()
    args = store.retry_delivery.await_args.args
    assert args[0] == 1
    assert (args[1] - before).total_seconds() >= 30
    limiter.penalize.assert_called_once_with(30)
    store.delivery_part_sent.assert_not_awaited()


@pytest.mark.parametrize("error", [TelegramForbiddenError, TelegramNetworkError])
async def test_blocked_and_temporary_failure(error):
    store, bot, limiter = setup_job()
    bot.send_message.side_effect = error(
        method=SendMessage(chat_id=42, text="test"), message="failure"
    )
    await DeliveryWorker(store, bot, limiter).once()
    if error is TelegramForbiddenError:
        store.block_user.assert_awaited_once_with(42)
    else:
        store.retry_delivery.assert_awaited_once()
    store.delivery_part_sent.assert_not_awaited()


async def test_deleted_subscription_cancels_in_flight_claim():
    store, bot, limiter = setup_job()
    store.delivery_allowed.return_value = False
    await DeliveryWorker(store, bot, limiter).once()
    bot.send_message.assert_not_awaited()
