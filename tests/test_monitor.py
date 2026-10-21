import asyncio
from datetime import timedelta

import httpx
import pytest

from iett_bot.config import Settings
from iett_bot.domain import Line, utcnow
from iett_bot.iett import IettClient
from iett_bot.worker import Heartbeats, Monitor

pytestmark = pytest.mark.integration


async def test_concurrent_refresh_and_claimed_poll_share_one_capture(store):
    await store.ensure_user(42)
    await store.subscribe(42, Line("59RH", "TEST"), 20)
    claimed = await store.due_lines(utcnow(), 1, timedelta(seconds=40))
    requests = []

    async def respond(request):
        requests.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json=["TEST cancellation"])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://iett.istanbul"
    ) as client:
        monitor = Monitor(store, IettClient(client), Settings(_env_file=None), Heartbeats())
        assert all(await asyncio.gather(*(monitor.refresh("59RH") for _ in range(10))))
        await monitor.poll(claimed[0])
    assert len(requests) == 1
    assert await store.pool.fetchval("SELECT count(*) FROM snapshots") == 1
    assert await store.pool.fetchval("SELECT count(*) FROM notices") == 1
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 1


async def test_manual_checks_share_the_background_concurrency_limit(store):
    await store.ensure_user(42)
    codes = ["59RH", "43R", "500T", "19F"]
    for code in codes:
        await store.subscribe(42, Line(code, "TEST"), 20)
    claimed = await store.due_lines(utcnow(), 1, timedelta(seconds=40))
    polled = claimed[0]["code"]
    active = peak = 0
    two_checks_started = asyncio.Event()

    async def respond(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            two_checks_started.set()
        await two_checks_started.wait()
        await asyncio.sleep(0)
        active -= 1
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://iett.istanbul"
    ) as client:
        monitor = Monitor(store, IettClient(client), Settings(_env_file=None), Heartbeats())
        async with asyncio.timeout(5):
            await asyncio.gather(
                monitor.poll(claimed[0]),
                *(monitor.refresh(code) for code in codes if code != polled),
            )
    assert peak == 2
    assert await store.pool.fetchval("SELECT count(*) FROM snapshots") == 4


async def test_new_subscriber_does_not_bypass_source_retry_after(store):
    line = Line("59RH", "TEST")
    await store.ensure_user(42)
    await store.subscribe(42, line, 20)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(503, headers={"Retry-After": "120"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://iett.istanbul"
    ) as client:
        monitor = Monitor(store, IettClient(client), Settings(_env_file=None), Heartbeats())
        assert not await monitor.refresh("59RH")
        retry_at = (await store.line_state("59RH"))["next_poll_at"]
        await store.ensure_user(43)
        await store.subscribe(43, line, 20)
        assert not await monitor.refresh("59RH")
        assert (await store.line_state("59RH"))["next_poll_at"] == retry_at
    assert len(requests) == 1
