from __future__ import annotations

from llm_core.usage import Usage

# gpt-4o pricing (USD per 1M tokens) - update when OpenAI changes rates.
GPT4O_INPUT_COST_PER_M = 2.50
GPT4O_OUTPUT_COST_PER_M = 10.00
TOKENS_PER_MILLION = 1_000_000


def usage_cost_usd(usage: Usage | None) -> float:
    if usage is None:
        return 0.0
    if usage.cost_usd is not None:
        return usage.cost_usd

    cost_usd = (
        usage.prompt_tokens / TOKENS_PER_MILLION * GPT4O_INPUT_COST_PER_M
        + usage.completion_tokens / TOKENS_PER_MILLION * GPT4O_OUTPUT_COST_PER_M
    )
    return round(cost_usd, 6)
