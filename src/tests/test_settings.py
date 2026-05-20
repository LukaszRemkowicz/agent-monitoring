import settings as settings_module
from conf import Settings


def test_settings_expose_uppercase_fields() -> None:
    settings = Settings()

    assert settings.DATABASE_HOST == settings_module.DATABASE_HOST
    assert settings.DATABASE_PORT == settings_module.DATABASE_PORT
    assert settings.DATABASE_NAME == settings_module.DATABASE_NAME
    assert settings.ENVIRONMENT == "dev"
    assert settings.DEBUG is False
    assert settings.LOG_FORMAT == "json"
    assert settings.MONITORING_PROJECT == "landingpage"
    assert settings.LOG_ANALYSIS_MCP_URL == "http://127.0.0.1:8001/mcp"
    assert settings.MONITORING_LLM_PROVIDER == "openai-fast"


def test_settings_can_load_injected_source() -> None:
    settings = Settings(
        {
            "DATABASE_HOST": "db.example",
            "DATABASE_PORT": 15432,
            "DATABASE_NAME": "monitoring_test",
            "DATABASE_USER": "monitor",
            "DATABASE_PASSWORD": "secret",
            "ENVIRONMENT": "dev",
            "MONITORING_PROJECT": "landingpage",
            "LOG_ANALYSIS_MCP_URL": "http://mcp.local/mcp",
            "MCP_WORKFLOW_JWT": "jwt-token",
            "MONITORING_LLM_PROVIDER": "mock",
        }
    )

    assert settings.ENVIRONMENT == "dev"
    assert settings.MONITORING_PROJECT == "landingpage"
    assert settings.LOG_ANALYSIS_MCP_URL == "http://mcp.local/mcp"
    assert settings.MCP_WORKFLOW_JWT == "jwt-token"
    assert settings.MONITORING_LLM_PROVIDER == "mock"


def test_settings_copy_can_override_values() -> None:
    settings = Settings(
        {
            "DATABASE_HOST": "db",
            "DATABASE_PORT": 5432,
            "DATABASE_NAME": "monitoring",
            "DATABASE_USER": "monitoring",
            "DATABASE_PASSWORD": "local",
        }
    )

    copied = settings.copy(DATABASE_NAME="monitoring_test")

    assert settings.DATABASE_NAME == "monitoring"
    assert copied.DATABASE_NAME == "monitoring_test"
