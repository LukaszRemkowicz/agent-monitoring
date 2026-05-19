"""Django-style settings for the monitoring app."""

from __future__ import annotations

import os
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT = os.environ.get("LOG_FORMAT", "pretty" if ENVIRONMENT == "dev" else "json")
LOG_COLOR = os.environ.get("LOG_COLOR", "auto")

DATABASE_HOST = os.environ.get("DATABASE_HOST", "db")
DATABASE_PORT = int(os.environ.get("DATABASE_PORT", "5432"))
DATABASE_NAME = os.environ.get("DATABASE_NAME", "monitoring")
DATABASE_USER = os.environ.get("DATABASE_USER", "monitoring")
DATABASE_PASSWORD = os.environ.get("DATABASE_PASSWORD", "monitoring")

LOG_ANALYSIS_MCP_URL = os.environ.get(
    "LOG_ANALYSIS_MCP_URL",
    "http://mcp-log-server:8000/mcp",
)
MCP_WORKFLOW_JWT = os.environ.get("MCP_WORKFLOW_JWT", "")
MONITORING_PROJECT = os.environ.get("MONITORING_PROJECT", "landingpage")

EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_USERNAME = os.environ.get("EMAIL_USERNAME", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "monitoring@example.com")
EMAIL_TO = os.environ.get("EMAIL_TO", "")

SITEMAP_ROOT_URL = os.environ.get("SITEMAP_ROOT_URL", "")
SITEMAP_EMAIL_TO = os.environ.get("SITEMAP_EMAIL_TO", "")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "90"))
