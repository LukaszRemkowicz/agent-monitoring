from __future__ import annotations

from llm_core.usage import Usage

from utils.llm_usage import usage_cost_usd


def test_usage_cost_prefers_provider_cost_when_available() -> None:
    usage = Usage(prompt_tokens=100, completion_tokens=20, total_tokens=120, cost_usd=0.123)

    assert usage_cost_usd(usage) == 0.123


def test_usage_cost_matches_landingpage_gpt4o_token_formula() -> None:
    usage = Usage(prompt_tokens=1_000_000, completion_tokens=500_000, total_tokens=1_500_000)

    assert usage_cost_usd(usage) == 7.5


def test_usage_cost_rounds_to_six_decimals_like_landingpage() -> None:
    usage = Usage(prompt_tokens=123, completion_tokens=45, total_tokens=168)

    assert usage_cost_usd(usage) == 0.000758


def test_usage_cost_returns_zero_without_usage() -> None:
    assert usage_cost_usd(None) == 0.0
