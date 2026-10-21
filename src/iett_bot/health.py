from aiohttp import web

from iett_bot.config import Settings
from iett_bot.db import Store
from iett_bot.domain import utcnow
from iett_bot.worker import Heartbeats, is_ready


def make_health_app(store: Store, beats: Heartbeats, settings: Settings) -> web.Application:
    app = web.Application()

    async def health(_: web.Request) -> web.Response:
        alive = beats.age() < max(60, settings.http_timeout_seconds * 5)
        return web.json_response({"alive": alive}, status=200 if alive else 503)

    async def ready(_: web.Request) -> web.Response:
        try:
            metrics = await store.metrics(utcnow())
        except Exception:
            return web.json_response({"ready": False}, status=503)
        available = is_ready(metrics, beats, settings)
        return web.json_response({"ready": available, **metrics}, status=200 if available else 503)

    async def metrics(_: web.Request) -> web.Response:
        try:
            values = await store.metrics(utcnow())
        except Exception:
            return web.Response(status=503, text="Database unavailable\n")
        values["worker_heartbeat_age_seconds"] = beats.age()
        body = "".join(f"iett_{key} {value}\n" for key, value in values.items())
        return web.Response(text=body, content_type="text/plain")

    app.router.add_get("/healthz", health)
    app.router.add_get("/readyz", ready)
    app.router.add_get("/metrics", metrics)
    return app
