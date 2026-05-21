from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

import pytest_asyncio
from tortoise import Tortoise

from conf import Settings, set_settings, settings


@contextmanager
def override_settings(**updates: object) -> Iterator[Settings]:
    """Temporarily patch selected shared app settings for tests."""

    previous_settings = settings.copy()
    effective_settings = previous_settings.copy(**updates)
    set_settings(effective_settings)
    try:
        yield effective_settings
    finally:
        set_settings(previous_settings)


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
