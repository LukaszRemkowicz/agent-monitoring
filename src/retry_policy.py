"""Stable process-exit contracts for scheduled command retries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from asyncpg import PostgresError  # type: ignore[import-untyped]
from tortoise.exceptions import IntegrityError


class CommandExitCode(IntEnum):
    """Process exit codes consumed by external command supervisors."""

    SUCCESS = 0
    ERROR = 1
    RETRY_WITH_FORCE = 75


@dataclass(frozen=True, slots=True)
class DatabaseRetryRule:
    """One database failure that maps to a scheduler retry action."""

    name: str
    sqlstate: str
    constraint_name: str
    exit_code: CommandExitCode
    description: str


LOG_ANALYSIS_PRIMARY_KEY_RETRY = DatabaseRetryRule(
    name="log_analysis_primary_key_conflict",
    sqlstate="23505",
    constraint_name="log_analyses_pkey",
    exit_code=CommandExitCode.RETRY_WITH_FORCE,
    description="Wait according to cron policy, then rerun the same analysis date with --force.",
)

DATABASE_RETRY_RULES: tuple[DatabaseRetryRule, ...] = (LOG_ANALYSIS_PRIMARY_KEY_RETRY,)


def match_database_retry_rule(exc: IntegrityError) -> DatabaseRetryRule | None:
    """Return the allowlisted retry rule matching a wrapped PostgreSQL error."""

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        if isinstance(current, PostgresError):
            for rule in DATABASE_RETRY_RULES:
                if (
                    current.constraint_name == rule.constraint_name
                    and current.sqlstate == rule.sqlstate
                ):
                    return rule

        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if not current.__suppress_context__ and current.__context__ is not None:
            pending.append(current.__context__)
        pending.extend(argument for argument in current.args if isinstance(argument, BaseException))
    return None
