from pytest_mock import MockerFixture

import scripts


def test_console_script_entrypoints_configure_logging(mocker: MockerFixture) -> None:
    configure_logging = mocker.patch("scripts.configure_logging")
    typer_run = mocker.patch("scripts.typer.run")

    scripts.log_analysis_entry()

    configure_logging.assert_called_once_with(scripts.settings)
    typer_run.assert_called_once_with(scripts.log_analysis)


def test_monitoring_entrypoint_runs_full_typer_app(mocker: MockerFixture) -> None:
    configure_logging = mocker.patch("scripts.configure_logging")
    app = mocker.patch("scripts.app")

    scripts.monitoring_entry()

    configure_logging.assert_called_once_with(scripts.settings)
    app.assert_called_once_with()
