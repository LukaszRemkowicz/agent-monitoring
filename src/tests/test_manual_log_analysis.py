from pathlib import Path

from pytest_mock import MockerFixture

from devtools import manual_log_analysis


def test_manual_fixture_uses_public_safe_context_by_default(
    mocker: MockerFixture,
) -> None:
    context_loader = mocker.patch.object(manual_log_analysis, "load_private_monitoring_context")

    context = manual_log_analysis._resolve_manual_fixture_monitoring_context(
        use_private_context=False
    )

    assert "Demo shop" in context
    assert "host-security" in context
    assert "portfolio" not in context.lower()
    context_loader.assert_not_called()


def test_manual_fixture_private_context_is_explicit_opt_in(
    mocker: MockerFixture,
) -> None:
    context_loader = mocker.patch.object(
        manual_log_analysis,
        "load_private_monitoring_context",
        return_value="Real project context prompt",
    )
    mocker.patch.object(
        manual_log_analysis.settings,
        "PROJECT_CONTEXT_PROMPT_PATH",
        Path("/private/context.md"),
    )

    context = manual_log_analysis._resolve_manual_fixture_monitoring_context(
        use_private_context=True
    )

    assert context == "Real project context prompt"
    context_loader.assert_called_once_with(Path("/private/context.md"))
