from typing import Any

import pytest
from pytest_mock import MockerFixture

from conf import Settings
from db.config import build_database_url, build_tortoise_config


def test_build_tortoise_config_contains_aerich_only_for_phase_zero() -> None:
    settings = Settings(
        {
            "DATABASE_HOST": "db",
            "DATABASE_PORT": 5432,
            "DATABASE_NAME": "monitoring",
            "DATABASE_USER": "user",
            "DATABASE_PASSWORD": "pass",
        }
    )

    config = build_tortoise_config(settings)

    assert config["connections"]["default"] == ("postgres://user:pass@db:5432/monitoring")
    assert config["apps"]["models"]["models"] == [
        "db.models",
        "aerich.models",
    ]
    assert config["apps"]["models"]["default_connection"] == "default"
    assert config["apps"]["models"]["migrations"] == "migrations/models"


def test_build_database_url_escapes_credentials() -> None:
    settings = Settings(
        {
            "DATABASE_HOST": "db.example",
            "DATABASE_PORT": 15432,
            "DATABASE_NAME": "monitoring/test",
            "DATABASE_USER": "monitor@example.com",
            "DATABASE_PASSWORD": "secret/pass",
        }
    )

    assert build_database_url(settings) == (
        "postgres://monitor%40example.com:secret%2Fpass@db.example:15432/monitoring%2Ftest"
    )


@pytest.mark.asyncio
async def test_database_lifecycle_initializes_and_closes_tortoise(
    mocker: MockerFixture,
) -> None:
    calls: list[tuple[str, object]] = []

    async def fake_init(**tortoise_kwargs: object) -> None:
        calls.append(("init", tortoise_kwargs["config"]))

    async def fake_close_connections() -> None:
        calls.append(("close", None))

    from db import lifecycle
    from db.lifecycle import close_database, initialize_database

    mocker.patch.object(lifecycle.Tortoise, "init", fake_init)
    mocker.patch.object(
        lifecycle.Tortoise,
        "close_connections",
        fake_close_connections,
    )

    config = {
        "connections": {"default": "postgres://user:pass@db:5432/monitoring"},
        "apps": {"models": {"models": ["db.models"]}},
    }

    await initialize_database(config)
    await close_database()

    assert calls == [("init", config), ("close", None)]


@pytest.mark.asyncio
async def test_database_lifespan_wraps_initialization_and_shutdown(
    mocker: MockerFixture,
) -> None:
    calls: list[str] = []

    async def fake_initialize_database(config: dict[str, Any]) -> None:
        calls.append(f"init:{config['connections']['default']}")

    async def fake_close_database() -> None:
        calls.append("close")

    from db import lifecycle
    from db.lifecycle import database_lifespan

    mocker.patch.object(lifecycle, "initialize_database", fake_initialize_database)
    mocker.patch.object(lifecycle, "close_database", fake_close_database)
    mocker.patch.object(
        lifecycle,
        "TORTOISE_ORM",
        {"connections": {"default": "postgres://user:pass@db:5432/monitoring"}},
    )

    async with database_lifespan():
        calls.append("inside")

    assert calls == [
        "init:postgres://user:pass@db:5432/monitoring",
        "inside",
        "close",
    ]
