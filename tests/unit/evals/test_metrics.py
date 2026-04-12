"""Tests for evals.metrics — scoring model and composite scoring."""

from __future__ import annotations

from sentinel.evals import metrics


class TestMetricWeight:
    def test_is_immutable(self) -> None:
        # Given a MetricWeight instance
        mw = metrics.MetricWeight(name="test", weight=0.5, evaluator_key="test_pass")

        # Then it cannot be mutated
        try:
            mw.name = "changed"  # type: ignore[misc]
            raise AssertionError("Should have raised")
        except AttributeError:
            pass


class TestAgentMetricSpec:
    def test_stores_agent_name_and_metrics(self) -> None:
        # Given a spec with two metrics
        spec = metrics.AgentMetricSpec(
            agent_name="test_agent",
            metrics=(
                metrics.MetricWeight(name="a", weight=0.6, evaluator_key="a_pass"),
                metrics.MetricWeight(name="b", weight=0.4, evaluator_key="b_pass"),
            ),
        )

        # Then fields are accessible
        assert spec.agent_name == "test_agent"
        assert len(spec.metrics) == 2


class TestComputeCompositeScore:
    def test_all_pass(self) -> None:
        # Given a spec where all assertions pass
        spec = metrics.AgentMetricSpec(
            agent_name="test",
            metrics=(
                metrics.MetricWeight(name="a", weight=0.6, evaluator_key="a_pass"),
                metrics.MetricWeight(name="b", weight=0.4, evaluator_key="b_pass"),
            ),
        )

        # When computing composite score with all True
        score = metrics.compute_composite_score(
            spec=spec,
            assertion_results={"a_pass": True, "b_pass": True},
        )

        # Then score is 1.0
        assert score == 1.0

    def test_all_fail(self) -> None:
        # Given a spec where all assertions fail
        spec = metrics.AgentMetricSpec(
            agent_name="test",
            metrics=(
                metrics.MetricWeight(name="a", weight=0.6, evaluator_key="a_pass"),
                metrics.MetricWeight(name="b", weight=0.4, evaluator_key="b_pass"),
            ),
        )

        # When computing composite score with all False
        score = metrics.compute_composite_score(
            spec=spec,
            assertion_results={"a_pass": False, "b_pass": False},
        )

        # Then score is 0.0
        assert score == 0.0

    def test_partial_pass_respects_weights(self) -> None:
        # Given a spec with unequal weights
        spec = metrics.AgentMetricSpec(
            agent_name="test",
            metrics=(
                metrics.MetricWeight(name="heavy", weight=0.8, evaluator_key="heavy_pass"),
                metrics.MetricWeight(name="light", weight=0.2, evaluator_key="light_pass"),
            ),
        )

        # When the heavy metric passes and light fails
        score = metrics.compute_composite_score(
            spec=spec,
            assertion_results={"heavy_pass": True, "light_pass": False},
        )

        # Then score reflects the heavy weight
        assert score == 0.8

    def test_missing_key_redistributes_weight(self) -> None:
        # Given a spec with three metrics
        spec = metrics.AgentMetricSpec(
            agent_name="test",
            metrics=(
                metrics.MetricWeight(name="a", weight=0.5, evaluator_key="a_pass"),
                metrics.MetricWeight(name="b", weight=0.3, evaluator_key="b_pass"),
                metrics.MetricWeight(name="c", weight=0.2, evaluator_key="c_pass"),
            ),
        )

        # When only a and b are present (both pass), c is missing
        score = metrics.compute_composite_score(
            spec=spec,
            assertion_results={"a_pass": True, "b_pass": True},
        )

        # Then score is 1.0 (weight redistributed among present metrics)
        assert score == 1.0

    def test_float_values_used_directly(self) -> None:
        # Given a spec with one metric
        spec = metrics.AgentMetricSpec(
            agent_name="test",
            metrics=(metrics.MetricWeight(name="a", weight=1.0, evaluator_key="a_pass"),),
        )

        # When the result is a float (0.75)
        score = metrics.compute_composite_score(
            spec=spec,
            assertion_results={"a_pass": 0.75},
        )

        # Then score reflects the float value
        assert score == 0.75

    def test_empty_results_returns_zero(self) -> None:
        # Given a spec with metrics
        spec = metrics.AgentMetricSpec(
            agent_name="test",
            metrics=(metrics.MetricWeight(name="a", weight=1.0, evaluator_key="a_pass"),),
        )

        # When no assertion results are provided
        score = metrics.compute_composite_score(
            spec=spec,
            assertion_results={},
        )

        # Then score is 0.0
        assert score == 0.0

    def test_score_is_rounded_to_four_decimal_places(self) -> None:
        # Given a spec that produces a long decimal
        spec = metrics.AgentMetricSpec(
            agent_name="test",
            metrics=(
                metrics.MetricWeight(name="a", weight=0.33, evaluator_key="a_pass"),
                metrics.MetricWeight(name="b", weight=0.33, evaluator_key="b_pass"),
                metrics.MetricWeight(name="c", weight=0.34, evaluator_key="c_pass"),
            ),
        )

        # When computing with mixed results
        score = metrics.compute_composite_score(
            spec=spec,
            assertion_results={"a_pass": True, "b_pass": False, "c_pass": True},
        )

        # Then score is rounded to 4 decimal places
        assert score == round(score, 4)

    def test_score_clamped_to_zero_one(self) -> None:
        # Given a spec
        spec = metrics.AgentMetricSpec(
            agent_name="test",
            metrics=(metrics.MetricWeight(name="a", weight=1.0, evaluator_key="a_pass"),),
        )

        # When the result is a very high float
        score = metrics.compute_composite_score(
            spec=spec,
            assertion_results={"a_pass": 1.5},
        )

        # Then score is clamped to 1.0
        assert score == 1.0


class TestAgentMetricSpecs:
    def test_has_seven_agents(self) -> None:
        # Given the AGENT_METRIC_SPECS constant

        # Then it contains specs for all 7 agents
        assert len(metrics.AGENT_METRIC_SPECS) == 7

    def test_all_agents_have_specs(self) -> None:
        # Given the expected agent names
        expected_agents = {
            "alert_classifier",
            "root_cause_analyser",
            "response_drafter",
            "chart_generator",
            "intent_router",
            "ticket_reviewer",
            "k8s_investigator",
        }

        # Then all agents are present
        assert set(metrics.AGENT_METRIC_SPECS.keys()) == expected_agents

    def test_weights_sum_to_one_per_agent(self) -> None:
        # Given all agent metric specs

        # Then weights sum to 1.0 for each agent
        for agent_name, spec in metrics.AGENT_METRIC_SPECS.items():
            total = sum(m.weight for m in spec.metrics)
            assert abs(total - 1.0) < 0.001, f"{agent_name} weights sum to {total}"

    def test_each_metric_has_evaluator_key(self) -> None:
        # Given all agent metric specs

        # Then every metric has a non-empty evaluator_key
        for spec in metrics.AGENT_METRIC_SPECS.values():
            for metric in spec.metrics:
                assert metric.evaluator_key, (
                    f"{spec.agent_name}.{metric.name} has empty evaluator_key"
                )
