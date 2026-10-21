from unittest.mock import AsyncMock, Mock

from aiohttp.test_utils import TestClient, TestServer

from iett_bot.config import Settings
from iett_bot.health import make_health_app
from iett_bot.worker import Heartbeats


async def test_health_readiness_metrics_and_database_failure():
    store = Mock(
        metrics=AsyncMock(
            return_value={
                "source_age_seconds": 30.0,
                "pending": 2.0,
                "active_outages": 0.0,
            }
        )
    )
    beats = Heartbeats()
    beats.tick("poller")
    beats.tick("scheduler")
    async with TestClient(
        TestServer(make_health_app(store, beats, Settings(_env_file=None)))
    ) as client:
        assert (await client.get("/healthz")).status == 200
        assert (await client.get("/readyz")).status == 200
        metrics = await (await client.get("/metrics")).text()
        assert "iett_pending 2.0" in metrics
        assert "chat_id" not in metrics
        store.metrics.return_value["source_age_seconds"] = 601
        assert (await client.get("/readyz")).status == 503
        assert (await client.get("/healthz")).status == 200
        store.metrics.side_effect = ConnectionError("simulated database outage")
        assert (await client.get("/readyz")).status == 503
        assert (await client.get("/metrics")).status == 503
