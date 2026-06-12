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
        LLM_DEFAULT_MODEL="gpt-4.1-mini",
        LLM_FAST_MODEL="gpt-4.1-mini",
        LLM_STRONG_MODEL="gpt-5",
        LLM_MODELS=("gpt-4.1-mini", "gpt-4.1-mini", "gpt-5"),
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
        LLM_DEFAULT_MODEL="gpt-4.1-mini",
        LLM_FAST_MODEL="gpt-4.1-mini",
        LLM_STRONG_MODEL="gpt-5",
        LLM_MODELS=("gpt-4.1-mini", "gpt-4.1-mini", "gpt-5"),
    ):
        provider = get_llm_provider("mock")

        assert isinstance(provider, MockProvider)


def test_configure_llm_providers_registers_distinct_default_model() -> None:
    with override_settings(
        OPENAI_API_KEY="test-key",
        LLM_DEFAULT_MODEL="gpt-4o-mini",
        LLM_FAST_MODEL="gpt-4.1-mini",
        LLM_STRONG_MODEL="gpt-5",
        LLM_MODELS=("gpt-4o-mini", "gpt-4.1-mini", "gpt-5"),
    ):
        configure_llm_providers()

        assert LLMProviderRegistry.list_available() == [
            "gpt-4.1-mini",
            "gpt-4o-mini",
            "gpt-5",
            "mock",
        ]
