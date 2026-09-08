from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from llm_core.usage import Usage
from pydantic_core import to_jsonable_python

TOKENS_PER_MILLION = 1_000_000
_DATED_MODEL_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True, slots=True)
class ModelTokenPricing:
    """USD prices per one million text tokens for one model."""

    input_per_million: float
    cached_input_per_million: float
    output_per_million: float


# Official OpenAI model pages, verified 2026-08-29:
# https://developers.openai.com/api/docs/models/gpt-5
# https://developers.openai.com/api/docs/models/gpt-4.1-mini
MODEL_TOKEN_PRICING: dict[str, ModelTokenPricing] = {
    "gpt-5": ModelTokenPricing(1.25, 0.125, 10.00),
    "gpt-4.1-mini": ModelTokenPricing(0.40, 0.10, 1.60),
}


def usage_cost_usd(usage: Usage | None, *, model_name: str | None = None) -> float | None:
    if usage is None:
        return None
    if usage.cost_usd is not None:
        return usage.cost_usd

    pricing: ModelTokenPricing | None = _model_token_pricing(model_name)
    if pricing is None:
        return None

    cached_prompt_tokens: int = min(
        _nested_optional_int(
            usage.raw,
            container_key="input_tokens_details",
            value_key="cached_tokens",
        )
        or 0,
        usage.prompt_tokens,
    )
    uncached_prompt_tokens: int = usage.prompt_tokens - cached_prompt_tokens
    cost_usd = (
        uncached_prompt_tokens * pricing.input_per_million
        + cached_prompt_tokens * pricing.cached_input_per_million
        + usage.completion_tokens * pricing.output_per_million
    ) / TOKENS_PER_MILLION
    return round(cost_usd, 6)


def _model_token_pricing(model_name: str | None) -> ModelTokenPricing | None:
    if not model_name:
        return None
    direct: ModelTokenPricing | None = MODEL_TOKEN_PRICING.get(model_name)
    if direct is not None:
        return direct
    for base_model, pricing in MODEL_TOKEN_PRICING.items():
        suffix: str = model_name.removeprefix(base_model)
        if suffix != model_name and _DATED_MODEL_SUFFIX.fullmatch(suffix):
            return pricing
    return None


def usage_telemetry(
    usage: Usage,
    *,
    model_name: str | None = None,
) -> dict[str, int | float | bool | None]:
    """Return normalized usage plus optional provider-specific token subsets."""

    cost_usd: float | None = usage_cost_usd(usage, model_name=model_name)
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cost_usd": cost_usd,
        "cost_estimate_available": cost_usd is not None,
        "cached_prompt_tokens": _nested_optional_int(
            usage.raw,
            container_key="input_tokens_details",
            value_key="cached_tokens",
        ),
        "reasoning_completion_tokens": _nested_optional_int(
            usage.raw,
            container_key="output_tokens_details",
            value_key="reasoning_tokens",
        ),
    }


def usage_raw_json(usage: Usage) -> dict[str, Any]:
    """Normalize provider usage into JSON-safe data without breaking the run."""

    try:
        normalized = to_jsonable_python(
            dict(usage.raw),
            serialize_unknown=True,
            inf_nan_mode="null",
            fallback=_unknown_usage_value,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        return {"telemetry_serialization_error": type(exc).__name__}
    return normalized if isinstance(normalized, dict) else {}


def _unknown_usage_value(value: object) -> str:
    return f"<{type(value).__name__}>"


def _nested_optional_int(
    payload: Mapping[str, Any],
    *,
    container_key: str,
    value_key: str,
) -> int | None:
    container = payload.get(container_key)
    if not isinstance(container, Mapping):
        return None
    value = container.get(value_key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
