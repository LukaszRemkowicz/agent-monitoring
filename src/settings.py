"""Django-style settings for the monitoring app."""

from __future__ import annotations

from pathlib import Path

import environ  # type: ignore[import-untyped]

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
env = environ.Env(DEBUG=(bool, False))

env_file = REPOSITORY_ROOT / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)


ENVIRONMENT = env.str("ENVIRONMENT", default="dev")
DEBUG = env.bool("DEBUG", default=False)
LOG_LEVEL = env.str("LOG_LEVEL", default="INFO")
LOG_FORMAT = env.str("LOG_FORMAT", default=None)
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

LOG_ANALYSIS_MCP_URL = env.str(
    "LOG_ANALYSIS_MCP_URL",
    default="http://127.0.0.1:8001/mcp",
)
MCP_WORKFLOW_JWT = env.str("MCP_WORKFLOW_JWT", default="")
MONITORING_PROJECT = env.str("MONITORING_PROJECT", default="landingpage")
MONITORING_LLM_PROVIDER = env.str("MONITORING_LLM_PROVIDER", default="openai-fast")
MONITORING_LLM_FAST_MODEL = env.str("MONITORING_LLM_FAST_MODEL", default="gpt-4.1-mini")
MONITORING_LLM_STRONG_MODEL = env.str("MONITORING_LLM_STRONG_MODEL", default="gpt-5")
OPENAI_API_KEY = env.str("OPENAI_API_KEY", default="")
OPENAI_BASE_URL = env.str("OPENAI_BASE_URL", default="")

EMAIL_HOST = env.str("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_USERNAME = env.str("EMAIL_USERNAME", default="")
EMAIL_PASSWORD = env.str("EMAIL_PASSWORD", default="")
EMAIL_FROM = env.str("EMAIL_FROM", default="monitoring@example.com")
EMAIL_TO = env.str("EMAIL_TO", default="")

SITEMAP_ROOT_URL = env.str("SITEMAP_ROOT_URL", default="")
SITEMAP_EMAIL_TO = env.str("SITEMAP_EMAIL_TO", default="")
RETENTION_DAYS = env.int("RETENTION_DAYS", default=90)
