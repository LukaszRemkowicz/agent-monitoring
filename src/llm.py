from __future__ import annotations

from typing import TYPE_CHECKING

from llm_core.bootstrap import register_builtin_providers
from llm_core.protocols import LLMProvider
from llm_core.registry import LLMProviderRegistry

from conf import settings
from logging_config import get_logger

if TYPE_CHECKING:
    from conf import Settings

logger = get_logger(__name__)


def configure_llm_providers(_settings: Settings = settings) -> None:
    """Register shared llm-core providers for this process."""

    register_builtin_providers(
        [
            {
                "name": "openai-fast",
                "provider": "openai",
                "api_key": _settings.OPENAI_API_KEY,
                "model": _settings.MONITORING_LLM_FAST_MODEL,
                "base_url": _settings.OPENAI_BASE_URL or None,
            },
            {
                "name": "openai-strong",
                "provider": "openai",
                "api_key": _settings.OPENAI_API_KEY,
                "model": _settings.MONITORING_LLM_STRONG_MODEL,
                "base_url": _settings.OPENAI_BASE_URL or None,
            },
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


def get_monitoring_llm_provider(_settings: Settings = settings) -> LLMProvider:
    """Return the configured shared LLM provider for monitoring analysis."""

    configure_llm_providers(_settings)
    provider_name = _settings.MONITORING_LLM_PROVIDER
    provider = LLMProviderRegistry.create(provider_name)
    logger.info(
        "created monitoring LLM provider",
        extra={
            "event": "monitoring_llm_provider_created",
            "provider": provider_name,
        },
    )
    return provider
