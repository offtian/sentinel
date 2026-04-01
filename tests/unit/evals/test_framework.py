"""
Tests for the eval framework types, reporting helpers, and runner resolution.
"""

from __future__ import annotations

import decimal
from unittest import mock

import pytest

from sentinel.evals import reporting, runner, types


class TestRunType:
    def test_scheduled_value(self) -> None:
        # Given the SCHEDULED enum member

        # When accessing its value
        value = types.RunType.SCHEDULED.value

        # Then it is "scheduled"
        assert value == "scheduled"

    def test_adhoc_value(self) -> None:
        # Given the ADHOC enum member

        # When accessing its value
        value = types.RunType.ADHOC.value

        # Then it is "adhoc"
        assert value == "adhoc"


class TestInputData:
    def test_stores_fields(self) -> None:
        # Given an InputData with known values
        data = types.InputData(
            agent_name="alert_classifier",
            case_payload={"id": "test-1"},
        )

        # Then fields are accessible
        assert data.agent_name == "alert_classifier"
        assert data.case_payload == {"id": "test-1"}

    def test_is_frozen(self) -> None:
        # Given an InputData
        data = types.InputData(agent_name="test", case_payload={})

        # Then mutation raises an error
        with pytest.raises(AttributeError):
            data.agent_name = "other"  # type: ignore[misc]

    def test_str_returns_json(self) -> None:
        # Given an InputData
        data = types.InputData(agent_name="test", case_payload={"x": 1})

        # When converting to string
        result = str(data)

        # Then it contains the agent name and payload
        assert "test" in result
        assert "x" in result


class TestGetAssertionAverage:
    def test_returns_zero_for_no_assertions(self) -> None:
        # Given a report case mock with no assertions
        case = mock.MagicMock()
        case.assertions = {}

        # When computing the average
        result = reporting.get_assertion_average(case=case)

        # Then it is zero
        assert result == decimal.Decimal(0)

    def test_returns_one_when_all_pass(self) -> None:
        # Given a report case where all assertions pass
        case = mock.MagicMock()
        assertion_a = mock.MagicMock()
        assertion_a.value = True
        assertion_b = mock.MagicMock()
        assertion_b.value = True
        case.assertions = {"a": assertion_a, "b": assertion_b}

        # When computing the average
        result = reporting.get_assertion_average(case=case)

        # Then it is 1.0
        assert result == decimal.Decimal(1)

    def test_returns_fraction_for_mixed(self) -> None:
        # Given a report case with mixed assertion results
        case = mock.MagicMock()
        passing = mock.MagicMock()
        passing.value = True
        failing = mock.MagicMock()
        failing.value = False
        case.assertions = {"pass": passing, "fail": failing}

        # When computing the average
        result = reporting.get_assertion_average(case=case)

        # Then it reflects the fraction
        assert result == decimal.Decimal("0.5")


class TestGetAssertionsAsStr:
    def test_returns_na_for_empty(self) -> None:
        # Given no assertions
        result = reporting.get_assertions_as_str({})

        # Then it returns N/A
        assert result == "N/A"

    def test_formats_passing_assertion(self) -> None:
        # Given a passing assertion
        assertion = mock.MagicMock()
        assertion.value = True
        assertion.name = "check_severity"
        assertion.reason = "Values match"

        # When formatting
        result = reporting.get_assertions_as_str({"check": assertion})

        # Then it shows PASS
        assert "[PASS]" in result
        assert "check_severity" in result

    def test_formats_failing_assertion(self) -> None:
        # Given a failing assertion
        assertion = mock.MagicMock()
        assertion.value = False
        assertion.name = "check_severity"
        assertion.reason = "Values differ"

        # When formatting
        result = reporting.get_assertions_as_str({"check": assertion})

        # Then it shows FAIL
        assert "[FAIL]" in result


class TestResolveAgents:
    def test_returns_all_agents_when_none(self) -> None:
        # Given no specific agent name

        # When resolving agents
        agents = runner._resolve_agents(agent_name=None)

        # Then all known agents are returned
        assert "alert_classifier" in agents
        assert "root_cause_analyser" in agents
        assert "response_drafter" in agents

    def test_returns_single_agent(self) -> None:
        # Given a specific agent name

        # When resolving agents
        agents = runner._resolve_agents(agent_name="alert_classifier")

        # Then only that agent is returned
        assert agents == ("alert_classifier",)

    def test_raises_for_unknown_agent(self) -> None:
        # Given an unknown agent name

        # When resolving agents
        # Then a ValueError is raised
        with pytest.raises(ValueError, match="Unknown agent name"):
            runner._resolve_agents(agent_name="nonexistent")


class TestGetTaskDurationAsStr:
    def test_formats_duration(self) -> None:
        # Given a duration value

        # When formatting
        result = reporting.get_task_duration_as_str(duration=1.234)

        # Then it shows seconds with two decimal places
        assert result == "1.23s"
