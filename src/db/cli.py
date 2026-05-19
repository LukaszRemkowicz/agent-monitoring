"""Database migration command aliases."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

MIGRATIONS_DIR = Path("migrations/models")

INIT_MIGRATIONS_REQUIRED_MESSAGES = (
    "You need to run `aerich init-db` first",
    "You may need to run `aerich init-db` first",
)


def _run_aerich(
    args: Sequence[str],
    *,
    capture_output: bool = False,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["aerich", *args],
        capture_output=capture_output,
        check=check,
        text=True,
    )


def _replay_output(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)


def _replay_error(error: subprocess.CalledProcessError) -> None:
    if error.stdout:
        sys.stdout.write(str(error.stdout))
    if error.stderr:
        sys.stderr.write(str(error.stderr))


def _next_migration_number(existing_files: set[Path]) -> int:
    numbers = []
    for path in existing_files:
        prefix = path.name.split("_", 1)[0]
        if len(prefix) == 3 and prefix.isdigit():
            numbers.append(int(prefix))
    return max(numbers, default=0) + 1


def _number_generated_migration(
    migration_name: str,
    before_files: set[Path],
) -> None:
    after_files = set(MIGRATIONS_DIR.glob("*.py"))
    generated_files = sorted(after_files - before_files)
    if not generated_files:
        return

    next_number = _next_migration_number(before_files)
    for generated_file in generated_files:
        target = MIGRATIONS_DIR / f"{next_number:03d}_{migration_name}.py"
        generated_file.rename(target)
        next_number += 1


def _run_makemigrations(args: Sequence[str]) -> int:
    migration_args = list(args)
    if len(migration_args) != 1 or migration_args[0].startswith("-"):
        sys.stderr.write("Usage: makemigrations <migration_name>\n")
        return 2

    migration_name = migration_args[0]
    before_files = set(MIGRATIONS_DIR.glob("*.py"))

    try:
        result = _run_aerich(
            ["migrate", "--offline", "--name", migration_name],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        output = f"{error.stdout or ''}{error.stderr or ''}"
        if any(message in output for message in INIT_MIGRATIONS_REQUIRED_MESSAGES):
            result = _run_aerich(["init-migrations"])
            _number_generated_migration(migration_name, before_files)
            return result.returncode

        _replay_error(error)
        return error.returncode

    _number_generated_migration(migration_name, before_files)
    _replay_output(result)
    return result.returncode


def makemigrations() -> None:
    raise SystemExit(_run_makemigrations(sys.argv[1:]))


def migrate() -> None:
    raise SystemExit(_run_aerich(["upgrade", *sys.argv[1:]]).returncode)


def test() -> None:
    raise SystemExit(subprocess.run(["pytest", *sys.argv[1:]]).returncode)
