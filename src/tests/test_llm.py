from llm_core.providers.mock import MockProvider
from llm_core.providers.openai import OpenAIProvider
from llm_core.registry import LLMProviderRegistry

from llm import configure_llm_providers, get_llm_provider
from tests.conftest import override_settings


def teardown_function() -> None:
    LLMProviderRegistry.clear()


def test_configure_llm_providers_registers_mock_and_model_profiles() -> None:
    with override_settings(
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL="",
        MONITORING_LLM_PROVIDER="mock",
        MONITORING_LLM_FAST_MODEL="gpt-4.1-mini",
        MONITORING_LLM_STRONG_MODEL="gpt-5",
    ):
        configure_llm_providers()

        assert LLMProviderRegistry.list_available() == [
            "gpt-4.1-mini",
            "gpt-5",
            "mock",
        ]
        assert isinstance(LLMProviderRegistry.create("mock"), MockProvider)
        assert isinstance(LLMProviderRegistry.create("gpt-4.1-mini"), OpenAIProvider)
        assert isinstance(LLMProviderRegistry.create("gpt-5"), OpenAIProvider)


def test_get_llm_provider_uses_requested_provider_name() -> None:
    with override_settings(
        OPENAI_API_KEY="",
        OPENAI_BASE_URL="",
        MONITORING_LLM_PROVIDER="gpt-4.1-mini",
        MONITORING_LLM_FAST_MODEL="gpt-4.1-mini",
        MONITORING_LLM_STRONG_MODEL="gpt-5",
    ):
        provider = get_llm_provider("mock")

        assert isinstance(provider, MockProvider)
