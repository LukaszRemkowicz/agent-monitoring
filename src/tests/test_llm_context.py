"""Regression coverage for bounded, deterministic monitoring prompt snapshots."""

import json

import pytest
from llm_core.types import Message, TextPart


def _text(message: Message) -> str:
    part = message.parts[0]
    assert isinstance(part, TextPart)
    return part.text


def test_oversized_snapshot_keeps_rules_counts_and_marks_omitted_evidence() -> None:
    from utils.llm_context import bound_messages

    payload = {
        "final_report_allowed": True,
        "instruction": "Do not infer healthy services from missing evidence.",
        "evidence": {
            "current_grouped_errors": {
                "evidence_complete": True,
                "event_count": 1000,
                "severity_counts": {"critical": 1, "low": 999},
                "fingerprints": [
                    {
                        "fingerprint": f"routine-{i}",
                        "attention_priority": "routine",
                        "message_summary": "雪" * 1000,
                    }
                    for i in range(100)
                ]
                + [
                    {
                        "fingerprint": "critical-last",
                        "attention_priority": "actionable",
                        "severity": "critical",
                        "message_summary": "Database unavailable",
                    }
                ],
            }
        },
    }
    original = json.dumps(payload)
    messages = [Message.from_text("system", "MANDATORY RULES"), Message.from_text("user", original)]

    result = bound_messages(messages, max_bytes=6000)

    assert sum(len(_text(message).encode("utf-8")) for message in result.messages) <= 6000
    assert _text(result.messages[0]) == "MANDATORY RULES"
    projected = json.loads(_text(result.messages[1]))
    evidence = projected["evidence"]["current_grouped_errors"]
    assert evidence["event_count"] == 1000
    assert evidence["severity_counts"] == {"critical": 1, "low": 999}
    assert "critical-last" in _text(result.messages[1])
    assert projected["instruction"] == payload["instruction"]
    assert result.omitted_details
    assert projected["context_budget"]["omitted_items"] > 0
    assert "not evidence of absence" in projected["context_budget"]["instruction"]
    assert _text(messages[1]) == original


def test_small_snapshot_is_unchanged() -> None:
    from utils.llm_context import bound_messages

    messages = [Message.from_text("system", "Rules"), Message.from_text("user", '{"evidence": {}}')]
    result = bound_messages(messages, max_bytes=6000)
    assert result.messages == messages
    assert not result.omitted_details


@pytest.mark.parametrize("key", ["tool_results", "evidence", "previous_analysis"])
def test_all_evidence_entrypoints_are_bounded(key: str) -> None:
    from utils.llm_context import bound_messages

    payload = {key: {"raw_lines": ["long log " * 1000] * 500}, "final_report_allowed": False}
    messages = [
        Message.from_text("system", "Rules"),
        Message.from_text("user", json.dumps(payload)),
    ]
    result = bound_messages(messages, max_bytes=4000)
    assert sum(len(_text(message).encode()) for message in result.messages) <= 4000
    assert json.loads(_text(result.messages[1]))["final_report_allowed"] is False
    assert result.omitted_details


def test_oversized_mandatory_instructions_fail_before_provider_call() -> None:
    from utils.llm_context import bound_messages

    with pytest.raises(ValueError, match="mandatory.*budget"):
        bound_messages([Message.from_text("system", "rules" * 2000)], max_bytes=1000)


def test_context_error_is_detected_through_provider_wrapper_only_by_code() -> None:
    from utils.llm_context import is_context_length_error

    class APIError(Exception):
        code = "context_length_exceeded"

    wrapped = RuntimeError("OpenAI provider request failed")
    wrapped.__cause__ = APIError("too large")
    assert is_context_length_error(wrapped)
    assert not is_context_length_error(RuntimeError("OpenAI provider request failed"))


def test_real_tool_results_and_corrections_preserve_skills_arguments_and_scope() -> None:
    from utils.llm_context import bound_messages

    skills = {
        "tool_name": "read_skills",
        "arguments": {"skill_name": "critical_rules"},
        "structured_content": {"content": "Mandatory interpretation rules." * 20},
    }
    evidence = {
        "tool_name": "group_errors",
        "arguments": {"project_name": "shop", "source_keys": ["backend", "nginx"]},
        "structured_content": {
            "grouped_error_count": 100,
            "truncated": False,
            "groups": [{"message": "very long error message " * 500}] * 100,
        },
    }
    messages = [
        Message.from_text("system", "Mandatory rules"),
        Message.from_text(
            "user",
            json.dumps(
                {
                    "tool_results": [skills, evidence],
                    "previous_action": {"summary": "long incorrect claim " * 10_000},
                    "final_report_not_allowed_yet": True,
                    "instruction": "Call deterministic tools before reporting.",
                }
            ),
        ),
    ]
    result = bound_messages(messages, max_bytes=6000)
    payload = json.loads(_text(result.messages[-1]))
    assert payload["tool_results"][0] == skills
    assert payload["tool_results"][1]["arguments"] == evidence["arguments"]
    assert payload["tool_results"][1]["structured_content"]["grouped_error_count"] == 100
    assert payload["final_report_not_allowed_yet"] is True
    assert payload["instruction"] == "Call deterministic tools before reporting."
    assert sum(len(_text(message).encode()) for message in result.messages) <= 6000
    assert result.omitted_details


@pytest.mark.parametrize("multipart", [False, True])
def test_multipart_and_non_json_instructions_are_not_silently_cut(multipart: bool) -> None:
    from utils.llm_context import bound_messages

    text = "Mandatory plaintext " * 2000
    message = (
        Message(role="user", parts=(TextPart(text=text), TextPart(text="More rules")))
        if multipart
        else Message.from_text("user", text)
    )
    messages = [message]
    with pytest.raises(ValueError, match="mandatory.*budget"):
        bound_messages(messages, max_bytes=1000)


def test_smaller_snapshot_does_not_drop_below_mandatory_instruction_floor() -> None:
    from utils.llm_context import message_bytes, smaller_messages

    messages = [
        Message.from_text("system", "R" * 60_000),
        Message.from_text(
            "user",
            json.dumps(
                {
                    "evidence": {"rows": [{"message": "x" * 500} for _ in range(60)]},
                }
            ),
        ),
    ]
    result = smaller_messages(messages, rejected_bytes=message_bytes(messages))
    assert 60_000 < message_bytes(result.messages) < 70_000
    assert _text(result.messages[0]) == "R" * 60_000
    assert result.omitted_details


def test_smaller_snapshot_rejects_irreducible_input_instead_of_retrying_identically() -> None:
    from utils.llm_context import message_bytes, smaller_messages

    messages = [Message.from_text("system", "Mandatory rules")]
    with pytest.raises(ValueError, match="cannot be reduced"):
        smaller_messages(messages, rejected_bytes=message_bytes(messages))


def test_minimum_snapshot_uses_smallest_projection_including_omission_overhead() -> None:
    from utils.llm_context import message_bytes, smaller_messages

    messages = [
        Message.from_text("system", "R" * 1000),
        Message.from_text("user", json.dumps({"evidence": {"rows": ["a", "b"]}}, indent=4)),
    ]
    result = smaller_messages(messages, rejected_bytes=message_bytes(messages))
    assert message_bytes(result.messages) < message_bytes(messages)
    assert json.loads(_text(result.messages[-1]))["evidence"]["rows"] == ["a", "b"]
    assert not result.omitted_details
