from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from llm_core.usage import Usage

from utils.llm_usage import usage_cost_usd, usage_raw_json, usage_telemetry


def test_usage_cost_prefers_provider_cost_when_available() -> None:
    usage = Usage(prompt_tokens=100, completion_tokens=20, total_tokens=120, cost_usd=0.123)

    assert usage_cost_usd(usage) == 0.123


def test_usage_cost_prices_gpt5_cached_input_separately() -> None:
    usage = Usage(
        prompt_tokens=1_000_000,
        completion_tokens=250_000,
        total_tokens=1_250_000,
        raw={"input_tokens_details": {"cached_tokens": 600_000}},
    )

    assert usage_cost_usd(usage, model_name="gpt-5") == 3.075


def test_usage_cost_prices_gpt41_mini_cached_input_separately() -> None:
    usage = Usage(
        prompt_tokens=1_000_000,
        completion_tokens=250_000,
        total_tokens=1_250_000,
        raw={"input_tokens_details": {"cached_tokens": 600_000}},
    )

    assert usage_cost_usd(usage, model_name="gpt-4.1-mini") == 0.62


def test_usage_cost_clamps_cached_tokens_to_prompt_total() -> None:
    usage = Usage(
        prompt_tokens=1_000_000,
        completion_tokens=0,
        total_tokens=1_000_000,
        raw={"input_tokens_details": {"cached_tokens": 2_000_000}},
    )

    assert usage_cost_usd(usage, model_name="gpt-5") == 0.125


def test_usage_cost_is_unavailable_without_usage_or_known_model() -> None:
    usage = Usage(prompt_tokens=1_000, completion_tokens=100, total_tokens=1_100)

    assert usage_cost_usd(None, model_name="gpt-5") is None
    assert usage_cost_usd(usage, model_name="future-model") is None


def test_usage_telemetry_preserves_provider_breakdown_without_double_counting() -> None:
    usage = Usage(
        prompt_tokens=1_000,
        completion_tokens=250,
        total_tokens=1_250,
        raw={
            "input_tokens": 1_000,
            "input_tokens_details": {"cached_tokens": 600},
            "output_tokens": 250,
            "output_tokens_details": {"reasoning_tokens": 150},
            "total_tokens": 1_250,
        },
    )

    telemetry = usage_telemetry(usage, model_name="gpt-5")

    assert telemetry["prompt_tokens"] == 1_000
    assert telemetry["completion_tokens"] == 250
    assert telemetry["total_tokens"] == 1_250
    assert telemetry["cached_prompt_tokens"] == 600
    assert telemetry["reasoning_completion_tokens"] == 150
    assert telemetry["cost_estimate_available"] is True
    assert telemetry["cost_usd"] == 0.003075


def test_usage_telemetry_tolerates_missing_or_malformed_provider_details() -> None:
    usage = Usage(
        prompt_tokens=10,
        completion_tokens=2,
        total_tokens=12,
        raw={
            "input_tokens_details": {"cached_tokens": "unknown"},
            "output_tokens_details": None,
        },
    )

    telemetry = usage_telemetry(usage, model_name="unknown-model")

    assert telemetry["cached_prompt_tokens"] is None
    assert telemetry["reasoning_completion_tokens"] is None
    assert telemetry["cost_estimate_available"] is False
    assert telemetry["cost_usd"] is None


def test_usage_raw_json_normalizes_nested_non_json_provider_values() -> None:
    class ProviderValue:
        pass

    usage = Usage(
        raw={
            "nested": {
                "timestamp": datetime(2026, 8, 6, tzinfo=UTC),
                "cost": Decimal("1.25"),
                "provider_value": ProviderValue(),
                "not_a_number": float("nan"),
                "positive_infinity": float("inf"),
                "negative_infinity": float("-inf"),
            }
        }
    )

    normalized = usage_raw_json(usage)

    assert normalized == {
        "nested": {
            "timestamp": "2026-08-06T00:00:00Z",
            "cost": "1.25",
            "provider_value": "<ProviderValue>",
            "not_a_number": None,
            "positive_infinity": None,
            "negative_infinity": None,
        }
    }
    json.dumps(normalized)
