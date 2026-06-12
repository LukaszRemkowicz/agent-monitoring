from __future__ import annotations

from llm_core.bootstrap import register_builtin_providers
from llm_core.protocols import LLMProvider
from llm_core.registry import LLMProviderRegistry

from conf import settings
from logging_config import get_logger

logger = get_logger(__name__)


def configure_llm_providers() -> None:
    """Register shared llm-core providers for this process."""

    register_builtin_providers(
        [
            {
                "name": model_name,
                "provider": "openai",
                "api_key": settings.OPENAI_API_KEY,
                "model": model_name,
            }
            for model_name in dict.fromkeys(settings.LLM_MODELS)
        ]
        + [
            {
                "name": "mock",
                "provider": "mock",
            },
        ],
        clear_existing=True,
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
