import json
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from iett_bot.db import Store


@pytest.fixture
def fixture_json():
    def load(name):
        return json.loads((Path(__file__).parent / "fixtures" / name).read_text())

    return load


@pytest_asyncio.fixture
async def store():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    schema = "test_" + uuid4().hex
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    db = None
    try:
        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=5, server_settings={"search_path": schema}
        )
        db = Store(pool)
        await db.migrate()
        yield db
    finally:
        if db:
            await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()
