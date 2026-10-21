import asyncio
import logging
import time
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import LinkPreviewOptions

from iett_bot.db import Store
from iett_bot.domain import utcnow

log = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, per_second: float = 20):
        self.interval = 1 / per_second
        self.next_global = 0.0
        self.chat_next: dict[int, float] = {}

    async def wait(self, chat_id: int) -> None:
        now = time.monotonic()
        target = max(now, self.next_global, self.chat_next.get(chat_id, 0))
        if target > now:
            await asyncio.sleep(target - now)
        now = time.monotonic()
        self.next_global = now + self.interval
        self.chat_next[chat_id] = now + 1.05
        if len(self.chat_next) > 10000:
            self.chat_next = {key: at for key, at in self.chat_next.items() if at > now}

    def penalize(self, seconds: float) -> None:
        self.next_global = max(self.next_global, time.monotonic() + seconds)


class DeliveryWorker:
    def __init__(self, store: Store, bot: Bot, limiter: RateLimiter | None = None):
        self.store = store
        self.bot = bot
        self.limiter = limiter or RateLimiter()

    async def once(self) -> bool:
        job = await self.store.claim_delivery(utcnow())
        if not job:
            return False
        await self.limiter.wait(job["chat_id"])
        # Re-check consent after a rate-limit wait; deletion may have removed the job.
        if not await self.store.delivery_allowed(job["id"], utcnow()):
            await self.store.retry_delivery(job["id"], utcnow(), "Delivery paused")
            return True
        try:
            await self.bot.send_message(
                job["chat_id"],
                job["messages"][job["next_part"]],
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except TelegramRetryAfter as exc:
            self.limiter.penalize(exc.retry_after)
            await self.store.retry_delivery(
                job["id"], utcnow() + timedelta(seconds=exc.retry_after), "Telegram rate limit"
            )
        except TelegramForbiddenError:
            await self.store.block_user(job["chat_id"])
        except TelegramBadRequest:
            await self.store.discard_delivery(job["id"])
            log.error("Permanent delivery error for queue item %s", job["id"])
        except (TelegramNetworkError, TelegramAPIError, OSError):
            delay = min(300, 2 ** min(job["attempts"] + 1, 9))
            await self.store.retry_delivery(
                job["id"], utcnow() + timedelta(seconds=delay), "Temporary Telegram error"
            )
        else:
            await self.store.delivery_part_sent(job["id"], utcnow())
        return True

    async def run(self) -> None:
        while True:
            if not await self.once():
                await asyncio.sleep(0.5)
