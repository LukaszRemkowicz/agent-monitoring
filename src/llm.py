from __future__ import annotations

from dataclasses import replace
from functools import partial
from typing import Any

from llm_core.bootstrap import register_builtin_providers
from llm_core.exceptions import ProviderExecutionError
from llm_core.protocols import LLMProvider
from llm_core.providers.openai import OpenAIProvider, OpenAIProviderConfig
from llm_core.registry import LLMProviderRegistry
from llm_core.structured_output import parse_json_response
from llm_core.types import GenerationOptions, LLMResponse, ResponseFormat

from conf import settings
from logging_config import get_logger

logger = get_logger(__name__)


class IncompleteLLMResponseError(ProviderExecutionError):
    """Retain an unfinished provider response for accounting and recovery."""

    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        raw = response.raw_response or {}
        details = raw.get("incomplete_details")
        self.reason: str = (
            str(details.get("reason") or "unknown") if isinstance(details, dict) else "unknown"
        )
        super().__init__(f"LLM response incomplete: {self.reason}")


class MonitoringOpenAIProvider(OpenAIProvider):
    """Check completion before JSON parsing in the pinned llm-core provider."""

    def parse_response_payload(
        self, payload: Any, *, options: GenerationOptions = GenerationOptions()
    ) -> LLMResponse:
        response: LLMResponse = super().parse_response_payload(
            payload, options=replace(options, response_format=ResponseFormat.TEXT)
        )
        if (response.raw_response or {}).get("status") == "incomplete":
            raise IncompleteLLMResponseError(response)
        if options.response_format is ResponseFormat.JSON_OBJECT and response.text is not None:
            return replace(response, structured_output=parse_json_response(response.text))
        return response


def configure_llm_providers() -> None:
    """Register shared llm-core providers for this process."""

    register_builtin_providers(
        [
            {
                "name": "mock",
                "provider": "mock",
            },
        ],
        clear_existing=True,
    )
    for model_name in dict.fromkeys(settings.LLM_MODELS):
        LLMProviderRegistry.register(
            model_name,
            partial(
                MonitoringOpenAIProvider,
                OpenAIProviderConfig(api_key=settings.OPENAI_API_KEY, model=model_name),
            ),
        )
    logger.info(
        "configured LLM providers",
        extra={
            "event": "llm_providers_configured",
            "providers": LLMProviderRegistry.list_available(),
        },
    )


def get_llm_provider(provider_name: str) -> LLMProvider:
    """Return one registered LLM provider profile by name."""

    configure_llm_providers()
    provider = LLMProviderRegistry.create(provider_name)
    logger.info(
        "created monitoring LLM provider",
        extra={
            "event": "monitoring_llm_provider_created",
            "provider": provider_name,
        },
    )
    return provider
