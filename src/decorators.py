from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import wraps
from socket import gaierror
from typing import Any, ParamSpec, TypeVar, overload

import click
from tortoise.exceptions import DBConnectionError, IntegrityError

from conf import settings
from db.lifecycle import database_lifespan
from exceptions import McpClientError
from logging_config import get_logger

P = ParamSpec("P")
T = TypeVar("T")
AsyncCallable = Callable[P, Coroutine[Any, Any, T]]
logger = get_logger(__name__)


def as_async() -> Callable[[AsyncCallable[P, T]], Callable[P, T]]:
    def decorator(func: AsyncCallable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return asyncio.run(func(*args, **kwargs))

        return wrapper

    return decorator


@overload
def db[**P, T](func: AsyncCallable[P, T]) -> AsyncCallable[P, T]: ...


@overload
def db() -> Callable[[AsyncCallable[P, T]], AsyncCallable[P, T]]: ...


def db[**P, T](
    func: AsyncCallable[P, T] | None = None,
) -> AsyncCallable[P, T] | Callable[[AsyncCallable[P, T]], AsyncCallable[P, T]]:
    def decorator(wrapped: AsyncCallable[P, T]) -> AsyncCallable[P, T]:
        @wraps(wrapped)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                async with database_lifespan():
                    return await wrapped(*args, **kwargs)
            except IntegrityError as exc:
                logger.error(
                    "database integrity error",
                    extra={
                        "event": "database_integrity_error",
                        "database_host": settings.DATABASE_HOST,
                        "database_port": settings.DATABASE_PORT,
                        "database_name": settings.DATABASE_NAME,
                        "error": str(exc),
                    },
                )
                raise click.ClickException(
                    f"Database integrity error: {exc}. "
                    f"Database={settings.DATABASE_HOST}:{settings.DATABASE_PORT}/"
                    f"{settings.DATABASE_NAME}."
                ) from None
            except (DBConnectionError, gaierror, ConnectionRefusedError) as exc:
                logger.error(
                    "database connection failed",
                    extra={
                        "event": "database_connection_failed",
                        "database_host": settings.DATABASE_HOST,
                        "database_port": settings.DATABASE_PORT,
                        "database_name": settings.DATABASE_NAME,
                        "error": str(exc),
                    },
                )
                raise click.ClickException(
                    "Database connection failed "
                    f"({settings.DATABASE_HOST}:{settings.DATABASE_PORT}/"
                    f"{settings.DATABASE_NAME}). "
                    "Check DATABASE_HOST, DATABASE_PORT, DATABASE_NAME, "
                    "and whether Postgres is running. For host-side commands, "
                    "DATABASE_HOST usually should be 127.0.0.1 or localhost."
                ) from None
            except McpClientError as exc:
                logger.error(
                    "MCP call failed",
                    extra={
                        "event": "mcp_call_failed",
                        "mcp_url": exc.mcp_url,
                        "tool_name": exc.tool_name,
                        "error": str(exc),
                    },
                )
                hint: str = f" {exc.hint}" if exc.hint else ""
                raise click.ClickException(
                    "MCP call failed "
                    f"(tool={exc.tool_name or 'unknown'}, url={exc.mcp_url or 'unknown'}). "
                    f"Reason: {exc}.{hint}"
                ) from None

        return wrapper

    if func is None:
        return decorator
    return decorator(func)
