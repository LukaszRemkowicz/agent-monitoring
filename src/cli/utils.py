"""CLI support utilities."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import click
import environ  # type: ignore[import-untyped]

DEFAULT_STATE_ROOT = Path("/var/lib/agent-monitoring")
PROD_COMPOSE_FILE = "docker-compose.prod.yml"
PROD_COMPOSE_PROJECT_NAME = "agent-monitoring"
PROD_COMPOSE_SERVICE = "app"
CONTAINER_RUNTIME_MARKERS = (
    Path("/.dockerenv"),
    Path("/run/.containerenv"),
)


def normalize_environment(environment: str) -> str:
    if environment == "production":
        return "prod"
    if environment in {"local", "prod"}:
        return environment
    raise ValueError(f"Unsupported environment: {environment}")


def get_project_dir(project_dir: Path | None = None) -> Path:
    if project_dir is not None:
        return project_dir.resolve()
    return Path.cwd().resolve()


def get_state_dir(
    environment: str,
    *,
    project_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    configured_state_dir = _env_str("STATE_DIR", default="", env=env).strip()
    if configured_state_dir:
        return Path(configured_state_dir)

    normalized_environment = normalize_environment(environment)
    preferred = DEFAULT_STATE_ROOT / normalized_environment
    if _can_use_preferred_state_dir(preferred):
        return preferred

    return get_project_dir(project_dir) / ".agent" / "state" / normalized_environment


def current_tag_path(
    environment: str = "prod",
    *,
    project_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    return get_state_dir(environment, project_dir=project_dir, env=env) / "current_tag"


def should_bridge_to_prod_compose() -> bool:
    return bool(not is_running_in_container() and resolve_prod_tag(required=False))


def is_running_in_container() -> bool:
    return any(path.exists() for path in CONTAINER_RUNTIME_MARKERS)


def run_prod_compose_command(command: Sequence[str]) -> int:
    tag = resolve_prod_tag(required=True)
    return int(subprocess.run(build_prod_compose_command(tag, command), check=False).returncode)


def resolve_prod_tag(
    *,
    required: bool,
    env: Mapping[str, str] | None = None,
) -> str:
    tag = _env_str("TAG", default="", env=env).strip()
    if tag:
        return tag

    tag_file = current_tag_path("prod", env=env)
    try:
        tag = tag_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        if required:
            raise click.ClickException(
                "TAG is not set and deployed tag file was not found: " f"{tag_file}"
            )
        return ""

    if tag:
        return tag
    if required:
        raise click.ClickException(f"Deployed tag file is empty: {tag_file}")
    return ""


def build_prod_compose_command(tag: str, command: Sequence[str]) -> list[str]:
    return [
        "env",
        f"TAG={tag}",
        f"COMPOSE_PROJECT_NAME={PROD_COMPOSE_PROJECT_NAME}",
        "docker",
        "compose",
        "-f",
        PROD_COMPOSE_FILE,
        "run",
        "--rm",
        PROD_COMPOSE_SERVICE,
        *command,
    ]


def _can_use_preferred_state_dir(preferred: Path) -> bool:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return True
    parent = preferred.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


def _env_str(
    name: str,
    *,
    default: str,
    env: Mapping[str, str] | None = None,
) -> str:
    if env is not None:
        return env.get(name, default)
    return str(environ.Env().str(name, default=default))
