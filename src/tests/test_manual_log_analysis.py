from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pytest_mock import MockerFixture

from devtools import manual_log_analysis
from schemas import LogAnalysisWorkflowResult, LogCollectionWindow
from tests.test_cli import _log_analysis_result


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


@pytest.mark.asyncio
async def test_manual_fixture_records_email_delivery_attempt(
    mocker: MockerFixture,
) -> None:
    class FakeEmailDeliveryRepository:
        def __init__(self) -> None:
            self.created: list[Any] = []

        async def create(self, data: Any) -> Any:
            self.created.append(data)
            return data

    class FakeLogAnalysisRepository:
        def __init__(self) -> None:
            self.updated: list[tuple[Any, dict[str, Any]]] = []

        async def update(self, analysis: Any, **updates: Any) -> Any:
            self.updated.append((analysis, updates))
            return analysis.model_copy(update=updates)

    class FakeLogAnalysisService:
        create_log_collection_window = staticmethod(
            lambda analysis_date: LogCollectionWindow(
                since="2026-05-18T22:00:00Z",
                until="2026-05-19T22:00:00Z",
                since_datetime=datetime(2026, 5, 18, 22, tzinfo=UTC),
                until_datetime=datetime(2026, 5, 19, 22, tzinfo=UTC),
            )
        )

        async def run_log_analysis(
            self,
            *,
            analysis_date: date,
            log_window: LogCollectionWindow,
            force: bool,
        ) -> LogAnalysisWorkflowResult:
            return _log_analysis_result(analysis_date)

    fake_repository = FakeLogAnalysisRepository()
    email_delivery_repository = FakeEmailDeliveryRepository()
    email_service = AsyncMock()
    mocker.patch.object(
        manual_log_analysis,
        "_today_in_log_timezone",
        return_value=date(2026, 5, 19),
    )
    mocker.patch.object(manual_log_analysis, "seed_manual_fixture_initial_data", AsyncMock())
    mocker.patch.object(
        manual_log_analysis,
        "LogAnalysisService",
        return_value=FakeLogAnalysisService(),
    )
    mocker.patch.object(
        manual_log_analysis,
        "MonitoringWorkflowAgent",
        return_value=object(),
    )
    mocker.patch.object(manual_log_analysis, "get_llm_provider", return_value=object())
    mocker.patch.object(manual_log_analysis, "LogAnalysisRepository", return_value=fake_repository)
    mocker.patch.object(
        manual_log_analysis,
        "EmailDeliveryRepository",
        return_value=email_delivery_repository,
    )
    mocker.patch.object(
        manual_log_analysis.MonitoringEmailService,
        "create_default",
        return_value=email_service,
    )

    run_manual_fixture = cast(Any, manual_log_analysis.run_manual_fixture)
    await run_manual_fixture.__wrapped__.__wrapped__(
        scenario="backend_5xx",
        force=False,
        send_email=True,
        compare_history=True,
        use_private_context=False,
    )

    email_service.send_log_analysis.assert_awaited_once()
    assert fake_repository.updated[0][1] == {"email_sent": True}
    delivery = email_delivery_repository.created[0]
    assert delivery.report_kind == "log_analysis"
    assert delivery.report_id == 1
    assert delivery.recipient_target == "log"
    assert delivery.status == "succeeded"
    assert delivery.analysis_date == date(2026, 5, 19)
