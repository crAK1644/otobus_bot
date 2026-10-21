import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import timedelta

import httpx

from iett_bot.config import Settings
from iett_bot.db import Store
from iett_bot.domain import utcnow
from iett_bot.iett import IettClient, SourceError

log = logging.getLogger(__name__)


@dataclass
class Heartbeats:
    ticks: dict = field(default_factory=dict)

    def tick(self, worker: str) -> None:
        self.ticks[worker] = utcnow()

    def age(self) -> float:
        if not self.ticks:
            return float("inf")
        return max((utcnow() - tick).total_seconds() for tick in self.ticks.values())


def is_ready(metrics: dict, beats: Heartbeats, settings: Settings) -> bool:
    """Workers alive, source data fresh, and no due delivery stuck for minutes."""
    return (
        beats.age() < max(60, settings.http_timeout_seconds * 5)
        and metrics["source_age_seconds"] <= settings.stale_after_seconds
        and metrics.get("overdue_deliveries", 0) == 0
    )


async def send_ping(url: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            (await client.get(url)).raise_for_status()
    except Exception as exc:  # a bad URL must never stop the scheduler
        # The URL is a secret: log only the error class.
        log.warning("Dead-man ping failed (%s)", type(exc).__name__)


class Monitor:
    def __init__(self, store: Store, source: IettClient, settings: Settings, beats: Heartbeats):
        self.store, self.source, self.settings, self.beats = store, source, settings, beats
        self._locks: dict[str, asyncio.Lock] = {}
        self._checks = asyncio.Semaphore(settings.poll_concurrency)

    async def poll(self, row: dict) -> None:
        async with self._locks.setdefault(row["code"], asyncio.Lock()):
            current = await self.store.line_state(row["code"])
            if current is None:
                return
            # A command may have checked this line since the poller claimed it.
            if "last_attempt_at" in row and row["last_attempt_at"] != current["last_attempt_at"]:
                return
            async with self._checks:
                await self._poll(current)

    async def refresh(self, code: str) -> bool:
        """Check on demand, sharing recent results and respecting source backoff."""
        async with self._locks.setdefault(code, asyncio.Lock()):
            row = await self.store.line_state(code)
            if row is None:
                return False
            now = utcnow()
            if row["failure_count"]:
                if row["next_poll_at"] > now:
                    return False
            elif row["last_success_at"] and now - row["last_success_at"] < timedelta(seconds=10):
                return True
            async with self._checks:
                await self.store.start_check(
                    code, utcnow(), timedelta(seconds=self.settings.http_timeout_seconds + 30)
                )
                return await self._poll(row)

    async def _poll(self, row: dict) -> bool:
        interval = self.settings.poll_interval_seconds
        try:
            snapshot = await self.source.fetch(row["code"])
        except SourceError as exc:
            delay = min(900, interval * 2 ** min(row["failure_count"], 5))
            delay = max(delay * random.uniform(0.9, 1.1), exc.retry_after or 0)
            now = utcnow()
            await self.store.record_failure(row["code"], now, now + timedelta(seconds=delay))
            log.warning("Source check failed for line %s", row["code"])
            return False
        else:
            delay = interval * random.uniform(0.9, 1.1)
            await self.store.record_snapshot(
                snapshot,
                snapshot.checked_at + timedelta(seconds=delay),
                timedelta(seconds=self.settings.stale_after_seconds),
            )
            return True

    async def ping(self, metrics: dict) -> None:
        """Signal a dead-man switch only while healthy; silence is the alert."""
        url = self.settings.healthcheck_ping_url.get_secret_value()
        if url and is_ready(metrics, self.beats, self.settings):
            await send_ping(url)

    async def run_poller(self) -> None:
        while True:
            self.beats.tick("poller")
            due = await self.store.due_lines(
                utcnow(),
                self.settings.poll_concurrency,
                timedelta(seconds=self.settings.http_timeout_seconds + 30),
            )
            if due:
                await asyncio.gather(*(self.poll(row) for row in due))
            else:
                await asyncio.sleep(1)

    async def run_scheduler(self) -> None:
        cleanup_at = utcnow()
        metrics_at = utcnow()
        while True:
            now = utcnow()
            self.beats.tick("scheduler")
            await self.store.open_incidents(
                now, timedelta(seconds=self.settings.outage_after_seconds)
            )
            await self.store.enqueue_due(now, timedelta(seconds=self.settings.stale_after_seconds))
            if now >= cleanup_at:
                await self.store.cleanup(now - timedelta(days=self.settings.history_retention_days))
                cleanup_at = now + timedelta(hours=6)
            if now >= metrics_at:
                metrics = await self.store.metrics(now)
                log.info("Monitor metrics %s", metrics)
                if metrics["source_age_seconds"] > self.settings.stale_after_seconds:
                    log.warning("Announcement source freshness target exceeded")
                await self.ping(metrics)
                metrics_at = now + timedelta(minutes=1)
            await asyncio.sleep(1)
