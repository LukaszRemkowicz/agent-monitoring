from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from conf import settings

if TYPE_CHECKING:
    from conf import Settings


def build_database_url(settings: Settings) -> str:
    username = quote(settings.DATABASE_USER, safe="")
    password = quote(settings.DATABASE_PASSWORD, safe="")
    database = quote(settings.DATABASE_NAME, safe="")
    return (
        f"postgres://{username}:{password}@{settings.DATABASE_HOST}:"
        f"{settings.DATABASE_PORT}/{database}"
    )


def build_tortoise_config(_settings: Settings = settings) -> dict[str, Any]:
    return {
        "connections": {"default": build_database_url(_settings)},
        "apps": {
            "models": {
                "models": ["db.models", "aerich.models"],
                "default_connection": "default",
                "migrations": "migrations/models",
            }
        },
    }


TORTOISE_ORM = build_tortoise_config()
