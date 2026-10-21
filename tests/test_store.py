import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import asyncpg
import pytest

from iett_bot.db import Store, SubscriptionLimit
from iett_bot.delivery import DeliveryWorker
from iett_bot.domain import Line, Snapshot
from iett_bot.schedule import Schedule

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 21, 4, 45, tzinfo=UTC)
LINE = Line("59RH", "RUMELİ HİSARÜSTÜ - HACIOSMAN METRO")
NOTICE = "TEST VERİSİ: HACIOSMAN METRO kalkışlı 08:30 seferi iptal edilmiştir. (07:45)"


async def subscribe(store, chat_id=42, schedule=None):
    await store.ensure_user(chat_id, "en")
    await store.subscribe(chat_id, LINE, 20)
    if schedule:
        await store.set_schedule(chat_id, LINE.code, Schedule.parse(schedule))


async def snapshot(store, notices=(NOTICE,), at=NOW):
    await store.record_snapshot(
        Snapshot(LINE.code, at, tuple(notices), tuple(notices)), at + timedelta(seconds=60)
    )


async def test_capture_removal_and_delivery_survive_restart(store, monkeypatch):
    await subscribe(store)
    await snapshot(store)
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 1
    await snapshot(store, (), NOW + timedelta(minutes=1))
    await snapshot(store, (), NOW + timedelta(minutes=2))
    notice = await store.pool.fetchrow("SELECT * FROM notices")
    assert notice["original_text"] == NOTICE
    assert notice["removed_at"] == NOW + timedelta(minutes=2)
    assert notice["last_seen_at"] == NOW
    schema = await store.pool.fetchval("SELECT current_schema()")
    restarted = Store(
        await asyncpg.create_pool(
            os.environ["TEST_DATABASE_URL"],
            min_size=1,
            max_size=2,
            server_settings={"search_path": schema},
        )
    )
    try:
        await restarted.migrate()  # idempotent across real connection pools
        bot = Mock(send_message=AsyncMock())
        limiter = Mock(wait=AsyncMock())
        monkeypatch.setattr("iett_bot.delivery.utcnow", lambda: NOW + timedelta(minutes=3))
        assert await DeliveryWorker(restarted, bot, limiter).once()
        assert NOTICE in bot.send_message.await_args.args[1]
        assert "First detected" in bot.send_message.await_args.args[1]
        assert LINE.url in bot.send_message.await_args.args[1]
        assert not await DeliveryWorker(restarted, bot, limiter).once()
        assert await store.pool.fetchval("SELECT status FROM outbox") == "sent"
    finally:
        await restarted.close()


async def test_capture_and_queue_are_atomic(store, monkeypatch):
    await subscribe(store)

    def crash(*args):
        raise RuntimeError("Simulated crash before queue creation")

    monkeypatch.setattr("iett_bot.db.notice_messages", crash)
    with pytest.raises(RuntimeError):
        await snapshot(store)
    assert await store.pool.fetchval("SELECT count(*) FROM notices") == 0
    assert await store.pool.fetchval("SELECT count(*) FROM snapshots") == 0
    assert await store.pool.fetchval("SELECT last_success_at FROM lines") is None


async def test_whitespace_order_edits_and_reappearance(store):
    await subscribe(store)
    await snapshot(store, ("A  B", "C"))
    await snapshot(store, ("C", "A\nB"), NOW + timedelta(minutes=1))
    assert await store.pool.fetchval("SELECT count(*) FROM notices") == 2
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 2
    await snapshot(store, (), NOW + timedelta(minutes=2))
    await store.record_failure("59RH", NOW + timedelta(minutes=3), NOW + timedelta(minutes=4))
    assert await store.pool.fetchval("SELECT max(missing_count) FROM notices") == 1
    await snapshot(store, ("A B", "C"), NOW + timedelta(minutes=4))
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 2
    await snapshot(store, ("A B edited", "C"), NOW + timedelta(minutes=5))
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 3
    await snapshot(store, (), NOW + timedelta(minutes=6))
    await snapshot(store, (), NOW + timedelta(minutes=7))
    await snapshot(store, ("A B",), NOW + timedelta(minutes=8))
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 4


async def test_schedules_pauses_and_collecting_while_muted(store):
    await subscribe(store, 42, "3 09:00-10:00")
    await subscribe(store, 43)
    await store.set_paused(43, True)
    await snapshot(store)
    assert await store.pool.fetchval("SELECT count(*) FROM notices") == 1
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 0
    await store.set_paused(43, False)
    await store.enqueue_due(NOW, timedelta(minutes=3))
    assert await store.pool.fetchval("SELECT chat_id FROM outbox") == 43
    # The notice disappears before subscriber 42's notification window opens.
    await snapshot(store, (), NOW + timedelta(hours=2))
    await snapshot(store, (), NOW + timedelta(hours=2, minutes=1))
    assert await store.pool.fetchval("SELECT count(*) FROM outbox WHERE chat_id=42") == 0
    assert await store.pool.fetchval("SELECT count(*) FROM outbox WHERE chat_id=43") == 1


async def test_schedule_rechecked_at_send_time(store):
    await subscribe(store)
    await snapshot(store)
    await store.set_schedule(42, LINE.code, Schedule.parse("3 09:00-10:00"))
    assert await store.claim_delivery(NOW) is None
    job = await store.claim_delivery(NOW + timedelta(hours=2))
    assert job is not None
    await store.set_paused(42, True)
    assert not await store.delivery_allowed(job["id"], NOW + timedelta(hours=2))


async def test_outage_and_recovery_once_per_notified_user(store):
    await subscribe(store, 42)
    await subscribe(store, 43, "1 all")  # Excludes Wednesday.
    await snapshot(store, ())
    await store.record_failure(LINE.code, NOW + timedelta(minutes=1), NOW + timedelta(minutes=2))
    await store.open_incidents(NOW + timedelta(minutes=9), timedelta(minutes=10))
    assert await store.pool.fetchval("SELECT count(*) FROM incidents") == 0
    at = NOW + timedelta(minutes=10)
    await store.open_incidents(at, timedelta(minutes=10))
    await store.enqueue_due(at, timedelta(minutes=3))
    job = await store.claim_delivery(at)
    assert job["kind"] == "outage" and job["chat_id"] == 42
    await store.delivery_part_sent(job["id"], at)
    await snapshot(store, (), NOW + timedelta(minutes=11))
    await store.enqueue_due(NOW + timedelta(minutes=12), timedelta(minutes=3))
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 2
    recovery = await store.claim_delivery(NOW + timedelta(minutes=12))
    assert recovery["kind"] == "recovery"
    await store.delivery_part_sent(recovery["id"], NOW + timedelta(minutes=12))
    await snapshot(store, (), NOW + timedelta(minutes=13))
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 2


async def test_outage_recovered_before_hours_does_not_send_old_warnings(store):
    await subscribe(store, 42, "3 09:00-10:00")
    await snapshot(store, ())
    await store.record_failure(LINE.code, NOW, NOW + timedelta(minutes=1))
    await store.open_incidents(NOW + timedelta(minutes=10), timedelta(minutes=10))
    await store.enqueue_due(NOW + timedelta(minutes=10), timedelta(minutes=3))
    await snapshot(store, (), NOW + timedelta(minutes=11))
    await store.enqueue_due(NOW + timedelta(hours=2), timedelta(minutes=3))
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 0


async def test_stale_snapshot_not_newly_queued_and_deleted_data_cascades(store):
    await subscribe(store)
    await store.set_paused(42, True)
    await snapshot(store)
    await store.set_paused(42, False)
    await store.enqueue_due(NOW + timedelta(minutes=4), timedelta(minutes=3))
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 0
    await snapshot(store, at=NOW + timedelta(minutes=5))
    await store.delete_user(42)
    assert await store.user(42) is None
    assert await store.pool.fetchval("SELECT count(*) FROM subscriptions") == 0
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 0
    assert await store.pool.fetchval("SELECT count(*) FROM notices") == 1


async def test_retention_preserves_active_receipts_and_unsent_alerts(store):
    await subscribe(store)
    old = NOW - timedelta(days=120)
    await snapshot(store, (NOTICE, "Persistent notice"), old)
    persistent_id = await store.pool.fetchval(
        "SELECT id FROM outbox WHERE notice_id=(SELECT id FROM notices "
        "WHERE original_text='Persistent notice')"
    )
    await store.pool.execute(
        "UPDATE outbox SET status='sent',sent_at=$2 WHERE id=$1", persistent_id, old
    )
    await snapshot(store, ("Persistent notice",), old + timedelta(minutes=1))
    await snapshot(store, ("Persistent notice",), old + timedelta(minutes=2))
    await store.cleanup(NOW - timedelta(days=90))
    assert await store.pool.fetchval("SELECT count(*) FROM notices") == 2
    await snapshot(store, ("Persistent notice",), NOW)
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 2
    await store.pool.execute("UPDATE outbox SET status='sent',sent_at=$1", NOW)
    await store.cleanup(NOW - timedelta(days=90))
    assert await store.pool.fetchval("SELECT count(*) FROM notices") == 1
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 1


async def test_single_shared_poll_and_atomic_subscription_limit(store):
    await subscribe(store, 42)
    await subscribe(store, 43)
    await store.set_paused(42, True)
    due = await store.due_lines(datetime.now(UTC), 10, timedelta(minutes=1))
    assert len(due) == 1 and due[0]["code"] == "59RH"
    await store.ensure_user(44)
    results = await asyncio.gather(
        store.subscribe(44, LINE, 1),
        store.subscribe(44, Line("19F", "TEST"), 1),
        return_exceptions=True,
    )
    assert sum(isinstance(result, SubscriptionLimit) for result in results) == 1
    assert len(await store.subscriptions(44)) == 1


async def test_expired_claim_is_recovered_and_blocked_user_stops_delivery(store):
    await subscribe(store)
    await snapshot(store)
    job = await store.claim_delivery(NOW)
    assert job
    assert await store.claim_delivery(NOW + timedelta(seconds=30)) is None
    recovered = await store.claim_delivery(NOW + timedelta(minutes=3))
    assert recovered["id"] == job["id"]
    await store.block_user(42)
    assert await store.claim_delivery(NOW + timedelta(minutes=10)) is None
    assert (await store.user(42))["blocked"]


async def test_delivery_retry_parts_and_operational_metrics(store):
    await subscribe(store)
    await snapshot(store, ("🚍 TEST cancellation " * 800,))
    metrics = await store.metrics(NOW + timedelta(seconds=30))
    assert metrics["pending"] == 1
    assert metrics["source_age_seconds"] == 30
    assert metrics["subscribed_lines"] == 1
    first = await store.claim_delivery(NOW)
    assert len(first["messages"]) > 1
    retry_at = NOW + timedelta(seconds=30)
    await store.retry_delivery(first["id"], retry_at, "Test rate limit")
    assert await store.claim_delivery(NOW + timedelta(seconds=29)) is None
    retry = await store.claim_delivery(retry_at)
    assert retry["id"] == first["id"] and retry["next_part"] == 0
    assert retry["attempts"] == 1
    for index in range(len(first["messages"])):
        await store.delivery_part_sent(first["id"], retry_at)
        job = await store.claim_delivery(retry_at)
        if index + 1 < len(first["messages"]):
            assert job["next_part"] == index + 1
        else:
            assert job is None
    assert await store.pool.fetchval("SELECT status FROM outbox") == "sent"


async def test_unsubscribe_invalidates_claim_and_parallel_polling_is_deduplicated(store):
    await subscribe(store)
    await store.subscribe(42, Line("19F", "TEST LINE"), 20)
    other = Snapshot("19F", NOW, ("TEST second line cancellation",), ())
    await asyncio.gather(
        snapshot(store),
        store.record_snapshot(other, NOW + timedelta(seconds=60)),
        store.enqueue_due(NOW, timedelta(minutes=3)),
    )
    assert await store.pool.fetchval("SELECT count(*) FROM outbox") == 2
    job = await store.claim_delivery(NOW)
    await store.unsubscribe(42, job["line_code"])
    assert not await store.delivery_allowed(job["id"], NOW)
