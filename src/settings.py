"""Django-style settings for the monitoring app."""

from __future__ import annotations

from pathlib import Path

import environ  # type: ignore[import-untyped]

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPOSITORY_ROOT / "src/templates"
env = environ.Env(DEBUG=(bool, False))

env_file = REPOSITORY_ROOT / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)


ENVIRONMENT = env.str("ENVIRONMENT", default="dev")
DEBUG = env.bool("DEBUG", default=False)
LOG_LEVEL = env.str("LOG_LEVEL", default="INFO")
LOG_FORMAT = env.str("LOG_FORMAT", default=None)
LOG_TIMEZONE = env.str("LOG_TIMEZONE", default="Europe/Warsaw")
LOGS_DIR = env.str("LOGS_DIR", default=str(REPOSITORY_ROOT / "logs"))
if DEBUG and LOG_FORMAT in {None, "json"}:
    LOG_FORMAT = "pretty"
elif LOG_FORMAT is None:
    LOG_FORMAT = "json"
LOG_COLOR = env.str("LOG_COLOR", default="auto")

DATABASE_HOST = env.str("DATABASE_HOST", default="127.0.0.1")
DATABASE_PORT = env.int("DATABASE_PORT", default=env.int("DATABASE_PORT_HOST", default=5438))
DATABASE_NAME = env.str("DATABASE_NAME", default="monitoring")
DATABASE_USER = env.str("DATABASE_USER", default="monitoring")
DATABASE_PASSWORD = env.str("DATABASE_PASSWORD", default="monitoring")

MCP_URL = env.str("MCP_URL", default="http://127.0.0.1:8001/mcp")
MCP_WORKFLOW_JWT = env.str("MCP_WORKFLOW_JWT", default="")
MCP_KEYCLOAK_URL = env.str("MCP_KEYCLOAK_URL", default="")
MCP_KEYCLOAK_CLIENT_ID = env.str("MCP_KEYCLOAK_CLIENT_ID", default="")
MCP_KEYCLOAK_CLIENT_SECRET = env.str("MCP_KEYCLOAK_CLIENT_SECRET", default="")
PROJECT_CONTEXT_PROMPT_PATH = env.str(
    "PROJECT_CONTEXT_PROMPT_PATH",
    default=str(REPOSITORY_ROOT / "private/vps_monitoring_context.md"),
)
LLM_DEFAULT_MODEL = env.str("LLM_DEFAULT_MODEL", default="gpt-4.1-mini")
LLM_FAST_MODEL = env.str("LLM_FAST_MODEL", default="gpt-4.1-mini")
LLM_STRONG_MODEL = env.str("LLM_STRONG_MODEL", default="gpt-5")
LLM_MODELS = (
    LLM_DEFAULT_MODEL,
    LLM_FAST_MODEL,
    LLM_STRONG_MODEL,
)
OPENAI_API_KEY = env.str("OPENAI_API_KEY", default="")

EMAIL_BACKEND = env.str("EMAIL_BACKEND", default="smtp")
EMAIL_HOST = env.str("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USERNAME = env.str("EMAIL_USERNAME", default="")
EMAIL_PASSWORD = env.str("EMAIL_PASSWORD", default="")
EMAIL_FROM = env.str("EMAIL_FROM", default="monitoring@example.com")
EMAIL_TO = env.str("EMAIL_TO", default="")

SITEMAP_PUBLIC_HOST = env.str("SITEMAP_PUBLIC_HOST", default="")
SITEMAP_EMAIL_TO = env.str("SITEMAP_EMAIL_TO", default="")
RETENTION_DAYS = env.int("RETENTION_DAYS", default=90)
LOG_ANALYSIS_RETENTION_DAYS = env.int(
    "LOG_ANALYSIS_RETENTION_DAYS",
    default=RETENTION_DAYS,
)
SITEMAP_ANALYSIS_RETENTION_DAYS = env.int(
    "SITEMAP_ANALYSIS_RETENTION_DAYS",
    default=RETENTION_DAYS,
)
LOG_ANALYSIS_PROTECTED_HISTORY_COUNT = env.int(
    "LOG_ANALYSIS_PROTECTED_HISTORY_COUNT",
    default=5,
)
