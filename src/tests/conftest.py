from collections.abc import AsyncIterator

import pytest_asyncio
from tortoise import Tortoise


@pytest_asyncio.fixture(autouse=True)
async def tortoise_db() -> AsyncIterator[None]:
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {
                "models": {
                    "models": ["db.models"],
                    "default_connection": "default",
                }
            },
        }
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()
