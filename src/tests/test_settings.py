import settings as settings_module
from conf import Settings


def test_settings_expose_uppercase_fields():
    runtime_settings = Settings()

    assert runtime_settings.DATABASE_HOST == settings_module.DATABASE_HOST
    assert runtime_settings.DATABASE_PORT == settings_module.DATABASE_PORT
    assert runtime_settings.DATABASE_NAME == settings_module.DATABASE_NAME
    assert runtime_settings.ENVIRONMENT == "dev"
    assert runtime_settings.MONITORING_PROJECT == "landingpage"
    assert runtime_settings.LOG_ANALYSIS_MCP_URL == "http://mcp-log-server:8000/mcp"


def test_settings_can_load_injected_source():
    runtime_settings = Settings(
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
        }
    )

    assert runtime_settings.ENVIRONMENT == "dev"
    assert runtime_settings.MONITORING_PROJECT == "landingpage"
    assert runtime_settings.LOG_ANALYSIS_MCP_URL == "http://mcp.local/mcp"
    assert runtime_settings.MCP_WORKFLOW_JWT == "jwt-token"


def test_settings_copy_can_override_values():
    runtime_settings = Settings(
        {
            "DATABASE_HOST": "db",
            "DATABASE_PORT": 5432,
            "DATABASE_NAME": "monitoring",
            "DATABASE_USER": "monitoring",
            "DATABASE_PASSWORD": "local",
        }
    )

    copied = runtime_settings.copy(DATABASE_NAME="monitoring_test")

    assert runtime_settings.DATABASE_NAME == "monitoring"
    assert copied.DATABASE_NAME == "monitoring_test"
