"""Developer shell with monitoring helpers preloaded."""

from __future__ import annotations

import asyncio
import os
from typing import Any

os.environ.setdefault("DATABASE_HOST", "127.0.0.1")
os.environ.setdefault("DATABASE_PORT", os.environ.get("DATABASE_PORT_HOST", "5438"))

from conf import settings
from db.config import TORTOISE_ORM
from db.lifecycle import close_database, initialize_database
from db.models import LogAnalysis, LogAnalysisLLMCall, RunStatus, SitemapAnalysis
from repositories import LLMCallRepository, LogAnalysisRepository, SitemapAnalysisRepository
from schemas import LogAnalysisIn, LogAnalysisLLMCallIn, SitemapAnalysisIn
from services.log_analyse import LogAnalysisService
from services.sitemap import AnalysisRunner

SHELL_EXIT_AFTER_BOOT_ENV = "MONITORING_SHELL_EXIT_AFTER_BOOT"

SHELL_IMPORT_LINES = [
    "from conf import settings",
    "from db.config import TORTOISE_ORM",
    "from db.models import LogAnalysis, LogAnalysisLLMCall, RunStatus, SitemapAnalysis",
    "from repositories import LLMCallRepository, LogAnalysisRepository, SitemapAnalysisRepository",
    "from schemas import LogAnalysisIn, LogAnalysisLLMCallIn, SitemapAnalysisIn",
    "from services.log_analyse import LogAnalysisService",
    "from services.sitemap import AnalysisRunner",
]


def build_shell_namespace() -> dict[str, Any]:
    """Return names preloaded into the developer shell."""

    return {
        "settings": settings,
        "TORTOISE_ORM": TORTOISE_ORM,
        "LogAnalysis": LogAnalysis,
        "LogAnalysisIn": LogAnalysisIn,
        "LogAnalysisLLMCall": LogAnalysisLLMCall,
        "LogAnalysisLLMCallIn": LogAnalysisLLMCallIn,
        "LogAnalysisRepository": LogAnalysisRepository,
        "LogAnalysisService": LogAnalysisService,
        "LLMCallRepository": LLMCallRepository,
        "RunStatus": RunStatus,
        "SitemapAnalysis": SitemapAnalysis,
        "SitemapAnalysisIn": SitemapAnalysisIn,
        "SitemapAnalysisRepository": SitemapAnalysisRepository,
        "AnalysisRunner": AnalysisRunner,
    }


def _start_ipython(user_ns: dict[str, Any]) -> None:
    """Start IPython with the given user namespace."""

    from IPython import start_ipython

    start_ipython(
        argv=[],
        user_ns=user_ns,
        display_banner=False,
    )


def print_shell_imports() -> None:
    """Print copy-paste import lines for names loaded into the shell."""

    print("Preloaded imports:")
    for import_line in SHELL_IMPORT_LINES:
        print(import_line)


def close_shell_database() -> None:
    """Close shell database connections, ignoring IPython loop ownership noise."""

    try:
        asyncio.run(close_database())
    except RuntimeError as exc:
        if "attached to a different loop" not in str(exc):
            raise


async def _initialize_shell() -> dict[str, Any]:
    """Initialize the database and return the shell namespace."""

    await initialize_database(TORTOISE_ORM)
    return build_shell_namespace()


def run_shell(*, start_repl: bool = True) -> int:
    """Initialize the database, then start the interactive developer shell."""

    user_ns = asyncio.run(_initialize_shell())
    try:
        if not start_repl:
            print_shell_imports()
            return 0

        print("Database initialized. Use top-level await for ORM calls.")
        print_shell_imports()
        _start_ipython(user_ns)
        return 0
    finally:
        close_shell_database()


def main() -> None:
    """Run the developer shell command."""

    start_repl = os.getenv(SHELL_EXIT_AFTER_BOOT_ENV) != "1"
    raise SystemExit(run_shell(start_repl=start_repl))
