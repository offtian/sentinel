"""
Evaluation framework tests — run all agents through the eval runner without LLM calls.

Mocks the semantic judge so that eval runs are deterministic and CI-safe.
These tests verify:
- All 7 agents load cases and run evaluators
- Composite scores are computed correctly
- Assertion keys match the metric spec evaluator_keys
"""

from __future__ import annotations

from unittest import mock

import pytest

from sentinel.evals import cases, metrics, runner
from sentinel.evals.evaluators import semantic


def _fake_grading(*, pass_: bool = True) -> semantic._GradingOutput:
    return semantic._GradingOutput(pass_=pass_, reason="mocked", score=1.0 if pass_ else 0.0)


@pytest.fixture(autouse=True)
def _mock_llm_judge() -> object:
    """Mock the LLM judge globally so no API calls are made."""
    with mock.patch.object(
        semantic,
        "run_judge",
        return_value=_fake_grading(pass_=True),
    ):
        yield


class TestEvalRunnerAllAgents:
    """Run the eval framework for every registered agent."""

    @pytest.mark.parametrize("agent_name", cases.AGENT_NAMES)
    async def test_agent_runs_without_errors(self, agent_name: str) -> None:
        # Given a registered agent name
        dataset = cases.load_cases(agent_name=agent_name)

        # When running the eval
        report = await dataset.evaluate(
            task=runner._noop_task,
            name=f"test_{agent_name}",
        )

        # Then cases were evaluated
        assert len(report.cases) == 5

        # Then every case has assertions
        for case in report.cases:
            assert len(case.assertions) > 0, f"{agent_name}/{case.name} has no assertions"


class TestAssertionKeysMatchMetricSpecs:
    """Verify that evaluator output keys match the metric spec evaluator_keys."""

    @pytest.mark.parametrize("agent_name", cases.AGENT_NAMES)
    async def test_metric_keys_are_subset_of_assertion_keys(self, agent_name: str) -> None:
        # Given the metric spec and a real eval run for this agent
        spec = metrics.AGENT_METRIC_SPECS.get(agent_name)
        if spec is None:
            pytest.skip(f"No metric spec for {agent_name}")

        dataset = cases.load_cases(agent_name=agent_name)
        report = await dataset.evaluate(
            task=runner._noop_task,
            name=f"test_{agent_name}",
        )

        # When collecting all assertion keys across cases
        all_keys: set[str] = set()
        for case in report.cases:
            all_keys.update(case.assertions.keys())

        # Then every metric spec evaluator_key appears in the assertion keys
        expected_keys = {m.evaluator_key for m in spec.metrics}
        missing = expected_keys - all_keys
        assert not missing, (
            f"{agent_name}: metric spec references keys not produced by evaluators: {missing}. "
            f"Available keys: {sorted(all_keys)}"
        )


class TestCompositeScoring:
    """Verify composite scores are computed from real evaluator output."""

    @pytest.mark.parametrize("agent_name", cases.AGENT_NAMES)
    async def test_composite_score_is_computed(self, agent_name: str) -> None:
        # Given a metric spec and eval results
        spec = metrics.AGENT_METRIC_SPECS.get(agent_name)
        if spec is None:
            pytest.skip(f"No metric spec for {agent_name}")

        dataset = cases.load_cases(agent_name=agent_name)
        report = await dataset.evaluate(
            task=runner._noop_task,
            name=f"test_{agent_name}",
        )

        # When collecting assertion results and computing composite score
        assertion_results = runner._collect_assertion_results(report=report)
        score = metrics.compute_composite_score(
            spec=spec,
            assertion_results=assertion_results,
        )

        # Then the score is valid (all mocked to pass, so should be 1.0)
        assert score == 1.0, f"{agent_name}: expected 1.0 with all-pass mock, got {score}"
