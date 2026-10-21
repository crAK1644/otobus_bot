from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from iett_bot.config import Settings
from iett_bot.delivery import DeliveryWorker
from iett_bot.domain import utcnow
from iett_bot.iett import IettClient
from iett_bot.worker import Heartbeats, Monitor

pytestmark = pytest.mark.integration


async def test_simulated_59rh_source_to_saved_queue_to_telegram_after_deletion(store, fixture_json):
    announcement = fixture_json("simulated_59rh_cancellation.json")
    responses = [fixture_json("search_59rh.json"), announcement, [], []]

    def respond(request):
        return httpx.Response(200, json=responses.pop(0))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://iett.istanbul"
    ) as client:
        source = IettClient(client)
        line = await source.resolve("59RH")
        for chat_id, language in [(42, "en"), (43, "tr")]:
            await store.ensure_user(chat_id, language)
            await store.subscribe(chat_id, line, 20)
        monitor = Monitor(store, source, Settings(_env_file=None), Heartbeats())
        await monitor.poll({"code": "59RH", "failure_count": 0})
        await monitor.poll({"code": "59RH", "failure_count": 0})
        await monitor.poll({"code": "59RH", "failure_count": 0})
    assert await store.pool.fetchval("SELECT removed_at IS NOT NULL FROM notices")
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 2
    bot = Mock(send_message=AsyncMock())
    worker = DeliveryWorker(store, bot, Mock(wait=AsyncMock()))
    assert await worker.once()
    assert await worker.once()
    assert not await worker.once()
    sent = [call.args[1] for call in bot.send_message.await_args_list]
    assert all(announcement[0] in message and line.url in message for message in sent)
    assert any("First detected" in message for message in sent)
    assert any("İlk görülme" in message for message in sent)
    assert await store.enqueue_due(utcnow(), timedelta(minutes=3)) == 0


async def test_failed_poll_preserves_snapshot_and_notice(store, fixture_json):
    payloads = [fixture_json("search_59rh.json"), ["TEST cancellation"], {"error": "unavailable"}]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payloads.pop(0))),
        base_url="https://iett.istanbul",
    ) as client:
        source = IettClient(client)
        line = await source.resolve("59RH")
        await store.ensure_user(42)
        await store.subscribe(42, line, 20)
        monitor = Monitor(store, source, Settings(_env_file=None), Heartbeats())
        await monitor.poll({"code": "59RH", "failure_count": 0})
        successful_at = await store.pool.fetchval("SELECT last_success_at FROM lines")
        await monitor.poll({"code": "59RH", "failure_count": 0})
    row = await store.pool.fetchrow("SELECT * FROM lines")
    assert row["failure_count"] == 1 and row["last_success_at"] == successful_at
    assert await store.pool.fetchval("SELECT count(*) FROM snapshots") == 1
    assert await store.pool.fetchval("SELECT missing_count FROM notices") == 0
