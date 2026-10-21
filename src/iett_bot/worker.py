import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import timedelta

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


class Monitor:
    def __init__(self, store: Store, source: IettClient, settings: Settings, beats: Heartbeats):
        self.store, self.source, self.settings, self.beats = store, source, settings, beats

    async def poll(self, row: dict) -> None:
        interval = self.settings.poll_interval_seconds
        try:
            snapshot = await self.source.fetch(row["code"])
        except SourceError as exc:
            delay = min(900, interval * 2 ** min(row["failure_count"], 5))
            delay = max(delay * random.uniform(0.9, 1.1), exc.retry_after or 0)
            now = utcnow()
            await self.store.record_failure(row["code"], now, now + timedelta(seconds=delay))
            log.warning("Source check failed for line %s", row["code"])
        else:
            delay = interval * random.uniform(0.9, 1.1)
            await self.store.record_snapshot(
                snapshot,
                snapshot.checked_at + timedelta(seconds=delay),
                timedelta(seconds=self.settings.stale_after_seconds),
            )

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
                metrics_at = now + timedelta(minutes=1)
            await asyncio.sleep(1)
