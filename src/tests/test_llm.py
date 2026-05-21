from llm_core.providers.mock import MockProvider
from llm_core.providers.openai import OpenAIProvider
from llm_core.registry import LLMProviderRegistry

from conf import Settings
from llm import configure_llm_providers, get_monitoring_llm_provider


def teardown_function() -> None:
    LLMProviderRegistry.clear()


def test_configure_llm_providers_registers_mock_and_openai_profiles() -> None:
    configure_llm_providers(
        Settings(
            {
                "OPENAI_API_KEY": "test-key",
                "OPENAI_BASE_URL": "",
                "MONITORING_LLM_PROVIDER": "mock",
                "MONITORING_LLM_FAST_MODEL": "gpt-4.1-mini",
                "MONITORING_LLM_STRONG_MODEL": "gpt-5",
            }
        )
    )

    assert LLMProviderRegistry.list_available() == [
        "mock",
        "openai-fast",
        "openai-strong",
    ]
    assert isinstance(LLMProviderRegistry.create("mock"), MockProvider)
    assert isinstance(LLMProviderRegistry.create("openai-fast"), OpenAIProvider)


def test_get_monitoring_llm_provider_uses_configured_provider_name() -> None:
    provider = get_monitoring_llm_provider(
        Settings(
            {
                "OPENAI_API_KEY": "",
                "OPENAI_BASE_URL": "",
                "MONITORING_LLM_PROVIDER": "mock",
                "MONITORING_LLM_FAST_MODEL": "gpt-4.1-mini",
                "MONITORING_LLM_STRONG_MODEL": "gpt-5",
            }
        )
    )

    assert isinstance(provider, MockProvider)
