from __future__ import annotations

from asyncpg import PostgresError  # type: ignore[import-untyped]
from tortoise.exceptions import IntegrityError

from retry_policy import (
    LOG_ANALYSIS_PRIMARY_KEY_RETRY,
    CommandExitCode,
    match_database_retry_rule,
)


def _integrity_error(*, sqlstate: str, constraint_name: str) -> IntegrityError:
    postgres_error = PostgresError.new(
        {
            "C": sqlstate,
            "M": "database constraint violation",
            "n": constraint_name,
        }
    )
    error = IntegrityError(postgres_error)
    error.__cause__ = postgres_error
    return error


def test_retry_with_force_exit_code_is_stable() -> None:
    assert CommandExitCode.SUCCESS == 0
    assert CommandExitCode.ERROR == 1
    assert CommandExitCode.RETRY_WITH_FORCE == 75


def test_log_analysis_primary_key_retry_rule_describes_scheduler_action() -> None:
    rule = LOG_ANALYSIS_PRIMARY_KEY_RETRY

    assert rule.name == "log_analysis_primary_key_conflict"
    assert rule.sqlstate == "23505"
    assert rule.constraint_name == "log_analyses_pkey"
    assert rule.exit_code is CommandExitCode.RETRY_WITH_FORCE
    assert "rerun" in rule.description
    assert "--force" in rule.description


def test_database_retry_rule_matches_wrapped_postgres_error() -> None:
    error = _integrity_error(sqlstate="23505", constraint_name="log_analyses_pkey")

    assert match_database_retry_rule(error) is LOG_ANALYSIS_PRIMARY_KEY_RETRY


def test_database_retry_rule_rejects_other_constraint() -> None:
    error = _integrity_error(sqlstate="23505", constraint_name="email_deliveries_pkey")

    assert match_database_retry_rule(error) is None
