import pytest
from llm_core.exceptions import ProviderExecutionError
from llm_core.providers.mock import MockProvider
from llm_core.providers.openai import OpenAIProvider
from llm_core.registry import LLMProviderRegistry
from llm_core.types import GenerationOptions, ResponseFormat

from llm import IncompleteLLMResponseError, configure_llm_providers, get_llm_provider
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


@pytest.mark.parametrize("text", ['{"action":', '{"action":"final_report"}', ""])
def test_openai_incomplete_response_retains_usage_before_json_parsing(text: str) -> None:
    provider = get_llm_provider("gpt-5")
    assert isinstance(provider, OpenAIProvider)
    with pytest.raises(ProviderExecutionError, match="max_output_tokens") as error:
        provider.parse_response_payload(
            {
                "id": "resp_truncated",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output_text": text,
                "model": "gpt-5",
                "usage": {"input_tokens": 20840, "output_tokens": 4000, "total_tokens": 24840},
            },
            options=GenerationOptions(response_format=ResponseFormat.JSON_OBJECT),
        )
    assert isinstance(error.value, IncompleteLLMResponseError)
    response = error.value.response
    assert response.usage is not None
    assert response.raw_response is not None
    assert response.usage.total_tokens == 24840
    assert response.raw_response["id"] == "resp_truncated"
