from __future__ import annotations

from typing import Any
from urllib.parse import quote

from conf import Settings, settings


def build_database_url(runtime_settings: Settings) -> str:
    username = quote(runtime_settings.DATABASE_USER, safe="")
    password = quote(runtime_settings.DATABASE_PASSWORD, safe="")
    database = quote(runtime_settings.DATABASE_NAME, safe="")
    return (
        f"postgres://{username}:{password}@{runtime_settings.DATABASE_HOST}:"
        f"{runtime_settings.DATABASE_PORT}/{database}"
    )


def build_tortoise_config(runtime_settings: Settings = settings) -> dict[str, Any]:
    return {
        "connections": {"default": build_database_url(runtime_settings)},
        "apps": {
            "models": {
                "models": ["db.models", "aerich.models"],
                "default_connection": "default",
                "migrations": "migrations/models",
            }
        },
    }


TORTOISE_ORM = build_tortoise_config()
