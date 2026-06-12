from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import click
import typer

PROD_STATE_DIR = "/var/lib/agent-monitoring/prod"
PROD_COMPOSE_FILE = "docker-compose.prod.yml"
PROD_COMPOSE_SERVICE = "app"
LOCAL_COMPOSE_FILE = "docker-compose.yaml"
LOCAL_COMPOSE_SERVICE = "monitoring-app"

app = typer.Typer(
    name="monitoring-run",
    help="Run monitoring jobs through Docker Compose from the current checkout.",
    no_args_is_help=True,
)


@dataclass(frozen=True)
class ComposeRuntime:
    compose_file: str
    service: str
    env: dict[str, str]


@app.command(
    "log-analysis",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def log_analysis(
    ctx: typer.Context,
    migrate: bool = typer.Option(
        True,
        "--migrate/--no-migrate",
        help="Run committed migrations before the one-shot job.",
    ),
) -> None:
    """Run the log-analysis app command through Docker Compose."""

    raise typer.Exit(run_compose_command(["log_analysis", *ctx.args], migrate_first=migrate))


@app.command(
    "sitemap-analysis",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def sitemap_analysis(
    ctx: typer.Context,
    migrate: bool = typer.Option(
        True,
        "--migrate/--no-migrate",
        help="Run committed migrations before the one-shot job.",
    ),
) -> None:
    """Run the sitemap-analysis app command through Docker Compose."""

    raise typer.Exit(run_compose_command(["sitemap-analysis", *ctx.args], migrate_first=migrate))


@app.command(
    "check-mcp",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def check_mcp(ctx: typer.Context) -> None:
    """Run the check-mcp app command through Docker Compose."""

    raise typer.Exit(run_compose_command(["check-mcp", *ctx.args], migrate_first=False))


def main() -> None:
    app()


def run_compose_command(command: Sequence[str], *, migrate_first: bool) -> int:
    runtime: ComposeRuntime = resolve_compose_runtime()
    compose_command: list[str] = [
        "docker",
        "compose",
        "-f",
        runtime.compose_file,
        "run",
        "--rm",
        runtime.service,
    ]
    typer.echo(
        "Running Docker Compose command "
        f"(compose_file={runtime.compose_file}, service={runtime.service})."
    )
    if migrate_first:
        migration_result = subprocess.run(
            [*compose_command, "migrate"],
            env=runtime.env,
            check=False,
        )
        if migration_result.returncode != 0:
            return int(migration_result.returncode)
    result = subprocess.run(
        [*compose_command, *command],
        env=runtime.env,
        check=False,
    )
    return int(result.returncode)


def resolve_compose_runtime() -> ComposeRuntime:
    env: dict[str, str] = os.environ.copy()
    env["LOG_FORMAT"] = env.get("MONITORING_RUN_LOG_FORMAT", "pretty")
    env["LOG_COLOR"] = env.get("MONITORING_RUN_LOG_COLOR", "always")
    configured_compose_file = env.get("MONITORING_COMPOSE_FILE", "").strip()
    configured_service = env.get("MONITORING_COMPOSE_SERVICE", "").strip()
    prod_tag = env.get("TAG", "").strip()
    prod_tag_file = Path(env.get("PROD_STATE_DIR", PROD_STATE_DIR)) / "current_tag"

    use_prod: bool = bool(
        configured_compose_file == PROD_COMPOSE_FILE
        or configured_service == PROD_COMPOSE_SERVICE
        or prod_tag
        or prod_tag_file.exists()
    )
    if use_prod:
        if not prod_tag:
            prod_tag = read_prod_tag(prod_tag_file)
        env["TAG"] = prod_tag
        return ComposeRuntime(
            compose_file=configured_compose_file or PROD_COMPOSE_FILE,
            service=configured_service or PROD_COMPOSE_SERVICE,
            env=env,
        )
    return ComposeRuntime(
        compose_file=configured_compose_file or LOCAL_COMPOSE_FILE,
        service=configured_service or LOCAL_COMPOSE_SERVICE,
        env=env,
    )


def read_prod_tag(tag_file: Path) -> str:
    try:
        tag: str = tag_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise click.ClickException(
            f"TAG is not set and deployed tag file was not found: {tag_file}"
        ) from exc
    if not tag:
        raise click.ClickException(f"Deployed tag file is empty: {tag_file}")
    return tag
