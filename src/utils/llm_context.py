"""Bound prompt-only evidence views without changing deterministic artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from llm_core.types import Message, TextPart

CONTEXT_LIMITATION = (
    "Some evidence details were omitted from the model input to fit its context budget. "
    "Full deterministic artifacts were retained; omitted details are not evidence of absence."
)
_PROTECTED_FIELDS = frozenset(
    {"decision_prompt", "instruction", "instructions", "limitations", "coverage_gaps"}
)


@dataclass(frozen=True)
class BoundedMessages:
    messages: list[Message]
    omitted_details: bool = False


class ContextBudgetExceeded(ValueError):
    """Requested ceiling is below the smallest supported evidence projection."""

    def __init__(self, max_bytes: int, minimum: BoundedMessages) -> None:
        self.minimum = minimum
        super().__init__(
            "LLM mandatory instructions or minimum evidence exceed the input byte budget "
            f"({max_bytes} bytes). Reduce private/skill context or narrow the analysis scope; "
            "required instructions were not truncated."
        )


def smaller_messages(messages: list[Message], *, rejected_bytes: int) -> BoundedMessages:
    """Reduce a rejected snapshot, falling back to the immutable evidence floor."""
    try:
        snapshot = bound_messages(messages, max_bytes=max(1, rejected_bytes // 2))
    except ContextBudgetExceeded as exc:
        snapshot = exc.minimum
    if message_bytes(snapshot.messages) >= rejected_bytes:
        raise ValueError(
            "LLM input cannot be reduced further without truncating required instructions "
            "or minimum evidence. Narrow the analysis scope or use a larger-context model."
        )
    return snapshot


def message_bytes(messages: list[Message]) -> int:
    """Count UTF-8 text bytes, conservatively budgeting arbitrary log text."""
    return sum(
        len(part.text.encode("utf-8"))
        for message in messages
        for part in message.parts
        if isinstance(part, TextPart)
    )


def is_context_length_error(exc: BaseException) -> bool:
    """Recognize the API error code through llm-core's exception wrapper."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "code", None) == "context_length_exceeded":
            return True
        body = getattr(current, "body", None)
        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, dict) and error.get("code") == "context_length_exceeded":
                return True
        current = current.__cause__ or current.__context__
    return False


def bound_messages(messages: list[Message], *, max_bytes: int) -> BoundedMessages:
    """Fit evidence into one stateless snapshot; never truncate instructions.

    Small requests are unchanged. Oversized JSON evidence gets progressively
    smaller detail views, with exact scalar aggregates and explicit omissions.
    UTF-8 bytes avoid undercounting arbitrary Unicode logs as characters / 4.
    This is an application ceiling, not a model-specific tokenizer estimate.
    """
    if max_bytes <= 0:
        raise ValueError("LLM input byte budget must be positive")
    if message_bytes(messages) <= max_bytes:
        return BoundedMessages(messages)

    minimum = BoundedMessages(messages)
    minimum_bytes = message_bytes(messages)
    for detail_limit in (256, 128, 64, 32, 16, 8, 4, 2, 1):
        projected: list[Message] = []
        omitted = False
        for message in messages:
            if message.role != "user" or len(message.parts) != 1:
                projected.append(message)
                continue
            part = message.parts[0]
            if not isinstance(part, TextPart):
                projected.append(message)
                continue
            try:
                payload = json.loads(part.text)
            except json.JSONDecodeError:
                projected.append(message)
                continue
            if not isinstance(payload, dict):
                projected.append(message)
                continue
            stats: dict[str, int] = {"omitted_items": 0, "omitted_string_bytes": 0}
            for key in ("evidence", "previous_analysis", "previous_action"):
                if key in payload:
                    payload[key] = _project(payload[key], detail_limit, stats)
            results = payload.get("tool_results")
            if isinstance(results, list):
                for result in results:
                    if isinstance(result, dict) and result.get("tool_name") != "read_skills":
                        if "structured_content" in result:
                            result["structured_content"] = _project(
                                result["structured_content"], detail_limit, stats
                            )
            # Evidence-only payloads in tests and future callers may be mappings.
            elif isinstance(results, dict):
                payload["tool_results"] = _project(results, detail_limit, stats)
            if any(stats.values()):
                omitted = True
                payload["context_budget"] = {
                    **stats,
                    "details_complete": False,
                    "instruction": (
                        CONTEXT_LIMITATION + " Aggregate counts remain exact. Collection "
                        "completeness flags describe collection, not visibility of all details. "
                        "Do not claim omitted families were reviewed, absent, resolved, "
                        "or harmless. "
                        "Request narrower deterministic evidence for material uncertainty and "
                        "include this limitation in final_report.coverage_gaps."
                    ),
                }
            projected.append(
                Message.from_text(
                    message.role,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    name=message.name,
                )
            )
        candidate = BoundedMessages(projected, omitted_details=omitted)
        candidate_bytes = message_bytes(projected)
        if candidate_bytes <= max_bytes:
            return candidate
        if candidate_bytes < minimum_bytes:
            minimum, minimum_bytes = candidate, candidate_bytes

    raise ContextBudgetExceeded(max_bytes, minimum=minimum)


def _priority(value: Any) -> int:
    """Keep explicit high-risk rows before low-risk examples when space is tight."""
    if not isinstance(value, dict):
        return 2
    severity = str(value.get("severity", "")).casefold()
    attention = str(value.get("attention_priority", "")).casefold()
    if severity in {"critical", "high"} or attention == "actionable":
        return 0
    if attention == "investigate":
        return 1
    if attention in {"watch_only", "routine"}:
        return 3
    return 2


def _project(value: Any, limit: int, stats: dict[str, int]) -> Any:
    """Project JSON detail while keeping mappings and scalar aggregates intact."""
    if isinstance(value, dict):
        return {
            key: item if key in _PROTECTED_FIELDS else _project(item, limit, stats)
            for key, item in value.items()
        }
    if isinstance(value, list):
        selected = value
        if len(value) > limit:
            selected = sorted(value, key=_priority)[:limit]
            stats["omitted_items"] += len(value) - limit
        result = [_project(item, limit, stats) for item in selected]
        if len(value) > limit:
            result.append({"_context_omitted_items": len(value) - limit})
        return result
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        byte_limit = limit * 64
        if len(encoded) > byte_limit:
            prefix = encoded[:byte_limit].decode("utf-8", errors="ignore")
            omitted = len(encoded) - len(prefix.encode("utf-8"))
            stats["omitted_string_bytes"] += omitted
            return prefix + f" [context omitted {omitted} UTF-8 bytes]"
    return value
