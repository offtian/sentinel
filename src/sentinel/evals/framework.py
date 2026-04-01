"""
Core evaluation framework for Tier 2 component evals.

Provide base abstractions for evaluating agent outputs against golden
datasets using pluggable scorers (rule-based offline, LLM-as-judge online).
"""

from __future__ import annotations

import abc
from collections.abc import Sequence

import attrs

from sentinel.utils import logs


@attrs.frozen
class MetricResult:
    """
    Result of a single metric evaluation.

    A metric passes when its value meets or exceeds the threshold.
    """

    name: str
    value: float
    threshold: float
    passed: bool


@attrs.frozen
class EvalCaseResult:
    """
    Result of evaluating a single test case across all metrics.

    Passes only when every metric passes.
    """

    case_id: str
    metrics: tuple[MetricResult, ...]
    passed: bool


@attrs.frozen
class EvalReport:
    """
    Aggregate report for an evaluator run across a dataset.
    """

    evaluator_name: str
    results: tuple[EvalCaseResult, ...]
    pass_rate: float


def compute_pass_rate(*, results: Sequence[EvalCaseResult]) -> float:
    """
    Return the fraction of cases that passed.

    :returns: 0.0 when no results, otherwise passed / total.
    """
    if not results:
        return 0.0
    passed_count = sum(1 for r in results if r.passed)
    return passed_count / len(results)


def make_metric(*, name: str, value: float, threshold: float) -> MetricResult:
    """
    Build a MetricResult, computing the ``passed`` flag automatically.
    """
    return MetricResult(
        name=name,
        value=value,
        threshold=threshold,
        passed=value >= threshold,
    )


class BaseEvaluator(abc.ABC):
    """
    Abstract base for component evaluators.

    Subclasses implement ``evaluate_case`` for a specific agent and metric set.
    The ``run`` method iterates over a dataset and produces an ``EvalReport``.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Return a human-readable evaluator name."""
        ...

    @abc.abstractmethod
    async def evaluate_case(self, *, case: dict) -> EvalCaseResult:
        """
        Evaluate a single golden case and return metrics.

        :param case: A dictionary loaded from the golden dataset JSON.
        """
        ...

    async def run(self, *, dataset: Sequence[dict]) -> EvalReport:
        """
        Run all cases and produce an aggregate report.

        :param dataset: Sequence of golden case dictionaries.
        """
        results: list[EvalCaseResult] = []
        for case in dataset:
            result = await self.evaluate_case(case=case)
            results.append(result)
            logs.log_event(
                "eval.case_completed",
                params={
                    "evaluator": self.name,
                    "case_id": result.case_id,
                    "passed": result.passed,
                },
            )

        result_tuple = tuple(results)
        report = EvalReport(
            evaluator_name=self.name,
            results=result_tuple,
            pass_rate=compute_pass_rate(results=result_tuple),
        )
        logs.log_event(
            "eval.run_completed",
            params={
                "evaluator": self.name,
                "total_cases": len(result_tuple),
                "pass_rate": report.pass_rate,
            },
        )
        return report
