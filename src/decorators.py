from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import wraps
from socket import gaierror
from typing import Any, ParamSpec, TypeVar, overload

import click
from llm_core.exceptions import ProviderConfigurationError, ProviderExecutionError
from tortoise.exceptions import DBConnectionError, IntegrityError

from conf import settings
from db.lifecycle import database_lifespan
from exceptions import (
    LogAnalysisAgentError,
    McpClientError,
    PrivateMonitoringContextError,
    format_exception_chain,
)
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
            except PrivateMonitoringContextError as exc:
                logger.error(
                    "private monitoring context failed",
                    extra={
                        "event": "private_monitoring_context_failed",
                        "context_path": exc.context_path,
                        "error": str(exc),
                    },
                )
                raise click.ClickException(
                    "Private monitoring context is not configured.\n"
                    f"Reason: {exc}.\n"
                    "Create the file at private/vps_monitoring_context.md, "
                    "mount it into Docker Compose, or set MONITORING_PRIVATE_CONTEXT_PATH "
                    "to the correct path."
                ) from None
            except LogAnalysisAgentError as exc:
                error_detail: str = format_exception_chain(exc)
                logger.error(
                    "log-analysis agent failed",
                    extra={
                        "event": "log_analysis_agent_failed",
                        "provider": settings.MONITORING_LLM_PROVIDER,
                        "error": error_detail,
                    },
                )
                raise click.ClickException(
                    "Log-analysis workflow failed.\n" f"Reason: {error_detail}."
                ) from None
            except ProviderConfigurationError as exc:
                logger.error(
                    "LLM provider configuration failed",
                    extra={
                        "event": "llm_provider_configuration_failed",
                        "provider": settings.MONITORING_LLM_PROVIDER,
                        "error": str(exc),
                    },
                )
                raise click.ClickException(
                    "LLM provider configuration failed "
                    f"(provider={settings.MONITORING_LLM_PROVIDER}). "
                    f"Reason: {exc}. "
                    "Check OPENAI_API_KEY in .env, Doppler, or the shell. "
                    "The variable name must be OPENAI_API_KEY, not OPEN_API_KEY."
                ) from None
            except ProviderExecutionError as exc:
                provider_error_detail: str = format_exception_chain(exc)
                logger.error(
                    "LLM provider request failed",
                    extra={
                        "event": "llm_provider_request_failed",
                        "provider": settings.MONITORING_LLM_PROVIDER,
                        "error": provider_error_detail,
                    },
                )
                raise click.ClickException(
                    "LLM provider request failed "
                    f"(provider={settings.MONITORING_LLM_PROVIDER}).\n"
                    f"Reason: {provider_error_detail}."
                ) from None

        return wrapper

    if func is None:
        return decorator
    return decorator(func)
