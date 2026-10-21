import argparse
import asyncio
import json
import logging
import sys
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiohttp import web

from iett_bot.bot import build_router
from iett_bot.config import Settings
from iett_bot.db import Store
from iett_bot.delivery import DeliveryWorker
from iett_bot.health import make_health_app
from iett_bot.iett import IettClient, http_client
from iett_bot.redirect import build_redirect_router
from iett_bot.worker import Heartbeats, Monitor

log = logging.getLogger(__name__)


async def runtime(settings: Settings) -> None:
    settings.require_runtime()
    store = await Store.connect(settings.database_url.get_secret_value())
    bot = Bot(settings.bot_token.get_secret_value())
    legacy_bot = None
    runner = None
    tasks = []
    try:
        await store.migrate()
        async with store.pool.acquire() as leader:
            if not await leader.fetchval("SELECT pg_try_advisory_lock(59202602)"):
                raise ValueError("Another bot process already holds this database's runtime lock.")
            try:
                async with http_client(settings.http_timeout_seconds) as client:
                    source = IettClient(client)
                    beats = Heartbeats()
                    beats.tick("poller")
                    beats.tick("scheduler")
                    monitor = Monitor(store, source, settings, beats)
                    dispatcher = Dispatcher()
                    dispatcher.include_router(build_router(store, source, settings, monitor))
                    runner = web.AppRunner(make_health_app(store, beats, settings), access_log=None)
                    await runner.setup()
                    await web.TCPSite(runner, settings.health_host, settings.health_port).start()
                    await bot.delete_webhook(drop_pending_updates=False)
                    commands = {
                        "en": [
                            "Start",
                            "Your lines",
                            "Settings",
                            "Current notices",
                            "Pause alerts",
                            "Resume alerts",
                            "Delete personal data",
                            "Cancel",
                            "Help",
                        ],
                        "tr": [
                            "Başla",
                            "Hatlarınız",
                            "Ayarlar",
                            "Güncel duyurular",
                            "Bildirimleri duraklat",
                            "Bildirimleri aç",
                            "Kişisel verileri sil",
                            "Vazgeç",
                            "Yardım",
                        ],
                    }
                    names = [
                        "start",
                        "lines",
                        "settings",
                        "status",
                        "pause",
                        "resume",
                        "delete_me",
                        "cancel",
                        "help",
                    ]
                    for language, descriptions in commands.items():
                        await bot.set_my_commands(
                            [
                                BotCommand(command=name, description=description)
                                for name, description in zip(names, descriptions, strict=True)
                            ],
                            language_code=language,
                        )
                    tasks = [
                        asyncio.create_task(monitor.run_poller(), name="source-poller"),
                        asyncio.create_task(monitor.run_scheduler(), name="scheduler"),
                        asyncio.create_task(DeliveryWorker(store, bot).run(), name="delivery"),
                        asyncio.create_task(
                            dispatcher.start_polling(
                                bot,
                                allowed_updates=dispatcher.resolve_used_update_types(),
                                close_bot_session=False,
                                tasks_concurrency_limit=32,
                            ),
                            name="telegram",
                        ),
                    ]
                    if settings.legacy_bot_token.get_secret_value():
                        legacy_bot = Bot(settings.legacy_bot_token.get_secret_value())
                        identity = await bot.get_me()
                        legacy_dispatcher = Dispatcher()
                        legacy_dispatcher.include_router(build_redirect_router(identity.username))
                        await legacy_bot.delete_webhook(drop_pending_updates=False)
                        tasks.append(
                            asyncio.create_task(
                                legacy_dispatcher.start_polling(
                                    legacy_bot,
                                    allowed_updates=legacy_dispatcher.resolve_used_update_types(),
                                    close_bot_session=False,
                                    handle_signals=False,
                                    tasks_concurrency_limit=8,
                                ),
                                name="former-telegram-bot",
                            )
                        )
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        await task  # Fail the process if a background worker fails.
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                with suppress(Exception):
                    await leader.execute("SELECT pg_advisory_unlock(59202602)")
    finally:
        if runner:
            await runner.cleanup()
        await bot.session.close()
        if legacy_bot:
            await legacy_bot.session.close()
        await store.close()


async def command(args: argparse.Namespace, settings: Settings) -> None:
    if args.command == "source":
        async with http_client(settings.http_timeout_seconds) as client:
            source = IettClient(client)
            line = await source.resolve(args.line)
            if not line:
                raise ValueError("No exact matching İETT line found.")
            snapshot = await source.fetch(line.code)
            print(
                json.dumps(
                    {
                        "line": line.code,
                        "name": line.name,
                        "source": line.url,
                        "checked_at": snapshot.checked_at.isoformat(),
                        "notices": snapshot.notices,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    elif args.command == "migrate":
        if not settings.database_url.get_secret_value():
            raise ValueError("Set DATABASE_URL before running migrations.")
        store = await Store.connect(settings.database_url.get_secret_value())
        try:
            await store.migrate()
        finally:
            await store.close()
    else:
        await runtime(settings)


def main() -> None:
    parser = argparse.ArgumentParser(description="Shared İETT Telegram service alerts")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Run the source monitor, delivery worker, and Telegram bot")
    sub.add_parser("migrate", help="Apply database migrations without starting Telegram")
    source = sub.add_parser("source", help="Read a public line feed without a token or database")
    source.add_argument("line", nargs="?", default="59RH")
    args = parser.parse_args()
    try:
        settings = Settings()
        logging.basicConfig(
            level=settings.log_level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        # HTTP client logging can include Telegram tokens in URLs. Keep it suppressed.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        asyncio.run(command(args, settings))
    except KeyboardInterrupt:
        pass
    except ValueError as exc:
        # Configuration validation may include input values: only print our explicit messages.
        message = (
            str(exc) if type(exc) is ValueError else "Invalid configuration; check .env values."
        )
        print(message, file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        log.error(
            "Application stopped (%s). Check database/source connectivity.", type(exc).__name__
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
