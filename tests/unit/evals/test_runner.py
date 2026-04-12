"""
Tests for the eval runner persistence wiring and helpers.
"""

from __future__ import annotations

import decimal
from unittest import mock

import pytest

from sentinel.evals import runner


class TestBuildResultsJson:
    def test_returns_empty_list_for_no_cases(self) -> None:
        # Given a report with no cases
        report = mock.MagicMock()
        report.cases = []

        # When building results JSON
        result = runner._build_results_json(report=report)

        # Then it returns an empty list
        assert result == []

    def test_returns_case_name_and_assertions(self) -> None:
        # Given a report with one case containing two assertions
        passing_assertion = mock.MagicMock()
        passing_assertion.value = True
        failing_assertion = mock.MagicMock()
        failing_assertion.value = False

        case = mock.MagicMock()
        case.name = "case_high_severity"
        case.assertions = {
            "severity_match": passing_assertion,
            "category_match": failing_assertion,
        }

        report = mock.MagicMock()
        report.cases = [case]

        # When building results JSON
        result = runner._build_results_json(report=report)

        # Then it contains the case with assertion pass/fail booleans
        assert len(result) == 1
        assert result[0]["case_name"] == "case_high_severity"
        assert result[0]["assertions"] == {
            "severity_match": True,
            "category_match": False,
        }

    def test_returns_multiple_cases(self) -> None:
        # Given a report with multiple cases
        pass_assertion = mock.MagicMock()
        pass_assertion.value = True
        fail_assertion = mock.MagicMock()
        fail_assertion.value = False

        first_case = mock.MagicMock()
        first_case.name = "case_a"
        first_case.assertions = {"check": pass_assertion}

        second_case = mock.MagicMock()
        second_case.name = "case_b"
        second_case.assertions = {"check": fail_assertion}

        report = mock.MagicMock()
        report.cases = [first_case, second_case]

        # When building results JSON
        result = runner._build_results_json(report=report)

        # Then both cases are present
        assert len(result) == 2
        assert result[0]["case_name"] == "case_a"
        assert result[0]["assertions"] == {"check": True}
        assert result[1]["case_name"] == "case_b"
        assert result[1]["assertions"] == {"check": False}

    def test_handles_case_with_no_assertions(self) -> None:
        # Given a report case with an empty assertions dict
        case = mock.MagicMock()
        case.name = "case_empty"
        case.assertions = {}

        report = mock.MagicMock()
        report.cases = [case]

        # When building results JSON
        result = runner._build_results_json(report=report)

        # Then the case entry has an empty assertions dict
        assert result[0]["assertions"] == {}


class TestRunPersistParameter:
    @pytest.mark.asyncio
    async def test_run_does_not_persist_when_persist_is_false(self) -> None:
        # Given a runner configured with persist=False
        mock_dataset = mock.MagicMock()
        mock_report = mock.MagicMock()
        mock_report.cases = [mock.MagicMock()]
        mock_report.failures = []
        mock_report.averages.return_value = None
        mock_report.name = "sentinel_eval_alert_classifier"
        mock_dataset.evaluate = mock.AsyncMock(return_value=mock_report)
        mock_dataset.cases = [mock.MagicMock()]

        with (
            mock.patch.object(runner, "cases") as mock_cases,
            mock.patch.object(runner, "rendering"),
            mock.patch.object(runner, "reporting") as mock_reporting,
            mock.patch.object(runner, "metrics") as mock_metrics,
            mock.patch.object(runner, "db") as mock_db,
        ):
            mock_cases.AGENT_NAMES = ("alert_classifier",)
            mock_cases.load_cases.return_value = mock_dataset
            mock_metrics.AGENT_METRIC_SPECS = {}

            report_wrapper = mock.MagicMock()
            report_wrapper.cases = [mock.MagicMock()]
            report_wrapper.failures = []
            report_wrapper.average_assertion_success_rate = decimal.Decimal("85.00")
            report_wrapper.composite_score = None
            mock_reporting.EvaluationReport.return_value = report_wrapper

            # When running with persist=False
            await runner.run(agent_name="alert_classifier", persist=False)

            # Then the database is never accessed
            mock_db.get_db.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_persists_when_persist_is_true(self) -> None:
        # Given a runner configured with persist=True and a working DB
        raw_case = mock.MagicMock()
        raw_case.name = "test_case"
        raw_case.assertions = {}

        mock_dataset = mock.MagicMock()
        mock_report = mock.MagicMock()
        mock_report.cases = [raw_case]
        mock_report.failures = []
        mock_report.averages.return_value = None
        mock_report.name = "sentinel_eval_alert_classifier"
        mock_dataset.evaluate = mock.AsyncMock(return_value=mock_report)
        mock_dataset.cases = [mock.MagicMock()]

        mock_db_conn = mock.MagicMock()

        with (
            mock.patch.object(runner, "cases") as mock_cases,
            mock.patch.object(runner, "rendering"),
            mock.patch.object(runner, "reporting") as mock_reporting,
            mock.patch.object(runner, "metrics") as mock_metrics,
            mock.patch.object(runner, "db") as mock_db,
            mock.patch.object(runner, "operations") as mock_operations,
        ):
            mock_cases.AGENT_NAMES = ("alert_classifier",)
            mock_cases.load_cases.return_value = mock_dataset
            mock_metrics.AGENT_METRIC_SPECS = {}

            report_wrapper = mock.MagicMock()
            report_wrapper.cases = [raw_case]
            report_wrapper.failures = []
            report_wrapper.average_assertion_success_rate = decimal.Decimal("85.00")
            report_wrapper.composite_score = None
            mock_reporting.EvaluationReport.return_value = report_wrapper

            mock_db.get_db.return_value = mock_db_conn
            mock_operations.persist_eval_run = mock.AsyncMock()

            # When running with persist=True
            await runner.run(agent_name="alert_classifier", persist=True)

            # Then persist_eval_run is called
            mock_operations.persist_eval_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_passes_correct_args_to_persist(self) -> None:
        # Given a runner with persist=True, known report metrics, and a composite score
        passing_assertion = mock.MagicMock()
        passing_assertion.value = True
        failing_assertion = mock.MagicMock()
        failing_assertion.value = False

        case_mock = mock.MagicMock()
        case_mock.name = "test_case"
        case_mock.assertions = {"sev_match": passing_assertion, "cat_match": failing_assertion}

        mock_report = mock.MagicMock()
        mock_report.cases = [case_mock]
        mock_report.failures = []
        mock_report.averages.return_value = None
        mock_report.name = "sentinel_eval_alert_classifier"

        mock_dataset = mock.MagicMock()
        mock_dataset.evaluate = mock.AsyncMock(return_value=mock_report)
        mock_dataset.cases = [mock.MagicMock()]

        mock_db_conn = mock.MagicMock()

        with (
            mock.patch.object(runner, "cases") as mock_cases,
            mock.patch.object(runner, "rendering"),
            mock.patch.object(runner, "reporting") as mock_reporting,
            mock.patch.object(runner, "metrics") as mock_metrics,
            mock.patch.object(runner, "db") as mock_db,
            mock.patch.object(runner, "operations") as mock_operations,
            mock.patch("time.monotonic", side_effect=[10.0, 10.5]),
        ):
            mock_cases.AGENT_NAMES = ("alert_classifier",)
            mock_cases.load_cases.return_value = mock_dataset
            mock_metrics.AGENT_METRIC_SPECS = {
                "alert_classifier": mock.MagicMock(),
            }
            mock_metrics.compute_composite_score.return_value = 0.85

            report_wrapper = mock.MagicMock()
            report_wrapper.cases = [case_mock]
            report_wrapper.failures = []
            report_wrapper.average_assertion_success_rate = decimal.Decimal("50.00")
            report_wrapper.composite_score = 0.85
            mock_reporting.EvaluationReport.return_value = report_wrapper

            mock_db.get_db.return_value = mock_db_conn
            mock_operations.persist_eval_run = mock.AsyncMock()

            # When running with persist=True
            await runner.run(agent_name="alert_classifier", persist=True)

            # Then persist_eval_run is called with the correct keyword args
            call_kwargs = mock_operations.persist_eval_run.call_args.kwargs
            assert call_kwargs["db"] == mock_db_conn
            assert call_kwargs["dataset_name"] == "sentinel_eval_alert_classifier"
            assert call_kwargs["agent_name"] == "alert_classifier"
            assert call_kwargs["total_cases"] == 1
            assert call_kwargs["passed_cases"] == 1
            assert call_kwargs["failed_cases"] == 0
            assert call_kwargs["average_score"] == pytest.approx(0.50)
            assert call_kwargs["composite_score"] == 0.85
            assert call_kwargs["run_duration_ms"] == 500
            assert call_kwargs["assertion_details_json"] == {
                "sev_match": True,
                "cat_match": False,
            }
            assert call_kwargs["results_json"] == {
                "cases": [
                    {
                        "case_name": "test_case",
                        "assertions": {"sev_match": True, "cat_match": False},
                    },
                ],
            }


class TestRunPersistErrorHandling:
    @pytest.mark.asyncio
    async def test_run_continues_when_db_unavailable(self) -> None:
        # Given a runner with persist=True but the database is unavailable
        raw_case = mock.MagicMock()
        raw_case.name = "test_case"
        raw_case.assertions = {}

        mock_dataset = mock.MagicMock()
        mock_report = mock.MagicMock()
        mock_report.cases = [raw_case]
        mock_report.failures = []
        mock_report.averages.return_value = None
        mock_report.name = "sentinel_eval_alert_classifier"
        mock_dataset.evaluate = mock.AsyncMock(return_value=mock_report)
        mock_dataset.cases = [mock.MagicMock()]

        with (
            mock.patch.object(runner, "cases") as mock_cases,
            mock.patch.object(runner, "rendering"),
            mock.patch.object(runner, "reporting") as mock_reporting,
            mock.patch.object(runner, "metrics") as mock_metrics,
            mock.patch.object(runner, "db") as mock_db,
            mock.patch.object(runner, "logs") as mock_logs,
        ):
            mock_cases.AGENT_NAMES = ("alert_classifier",)
            mock_cases.load_cases.return_value = mock_dataset
            mock_metrics.AGENT_METRIC_SPECS = {}

            report_wrapper = mock.MagicMock()
            report_wrapper.cases = [raw_case]
            report_wrapper.failures = []
            report_wrapper.average_assertion_success_rate = decimal.Decimal("85.00")
            report_wrapper.composite_score = None
            mock_reporting.EvaluationReport.return_value = report_wrapper

            mock_db.get_db.side_effect = RuntimeError("DATABASE_URL is not configured")

            # When running with persist=True
            await runner.run(agent_name="alert_classifier", persist=True)

            # Then a warning is logged and the run completes without raising
            mock_logs.log_event.assert_any_call(
                "eval.persist_skipped",
                params=mock.ANY,
            )

    @pytest.mark.asyncio
    async def test_run_continues_when_persist_raises(self) -> None:
        # Given a runner with persist=True but the persist call fails
        raw_case = mock.MagicMock()
        raw_case.name = "test_case"
        raw_case.assertions = {}

        mock_dataset = mock.MagicMock()
        mock_report = mock.MagicMock()
        mock_report.cases = [raw_case]
        mock_report.failures = []
        mock_report.averages.return_value = None
        mock_report.name = "sentinel_eval_alert_classifier"
        mock_dataset.evaluate = mock.AsyncMock(return_value=mock_report)
        mock_dataset.cases = [mock.MagicMock()]

        mock_db_conn = mock.MagicMock()

        with (
            mock.patch.object(runner, "cases") as mock_cases,
            mock.patch.object(runner, "rendering"),
            mock.patch.object(runner, "reporting") as mock_reporting,
            mock.patch.object(runner, "metrics") as mock_metrics,
            mock.patch.object(runner, "db") as mock_db,
            mock.patch.object(runner, "operations") as mock_operations,
            mock.patch.object(runner, "logs") as mock_logs,
        ):
            mock_cases.AGENT_NAMES = ("alert_classifier",)
            mock_cases.load_cases.return_value = mock_dataset
            mock_metrics.AGENT_METRIC_SPECS = {}

            report_wrapper = mock.MagicMock()
            report_wrapper.cases = [raw_case]
            report_wrapper.failures = []
            report_wrapper.average_assertion_success_rate = decimal.Decimal("85.00")
            report_wrapper.composite_score = None
            mock_reporting.EvaluationReport.return_value = report_wrapper

            mock_db.get_db.return_value = mock_db_conn
            mock_operations.persist_eval_run = mock.AsyncMock(
                side_effect=Exception("DB write failed")
            )

            # When running with persist=True
            await runner.run(agent_name="alert_classifier", persist=True)

            # Then a warning is logged and the run completes without raising
            mock_logs.log_event.assert_any_call(
                "eval.persist_skipped",
                params=mock.ANY,
            )
