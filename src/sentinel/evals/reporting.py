"""
Evaluation report wrapper with analysis helpers.

Wrap the ``pydantic_evals`` report with convenience methods for
computing average scores and assertion success rates.
"""

from __future__ import annotations

import dataclasses
import decimal

from pydantic_evals import evaluators, reporting


@dataclasses.dataclass
class EvaluationReport:
    """
    Wrapper around a ``pydantic_evals.reporting.EvaluationReport``.

    Compute and cache aggregate metrics on construction.
    """

    _report: reporting.EvaluationReport
    name: str = ""
    cases: list[reporting.ReportCase] = dataclasses.field(default_factory=list)
    failures: list[reporting.ReportCaseFailure] = dataclasses.field(default_factory=list)

    has_assertions: bool = False

    average_assertion_success_rate: decimal.Decimal = decimal.Decimal(0)
    average_duration: decimal.Decimal = decimal.Decimal(0)

    def __init__(self, report: reporting.EvaluationReport) -> None:
        self._report = report
        self.name = self._report.name
        self.cases = self._report.cases
        self.failures = self._report.failures
        self.has_assertions = any(case.assertions for case in self.cases)

        averages = self._report.averages()
        if averages is not None and self.cases:
            case_assertions = [get_assertion_average(case=case) for case in self.cases]
            self.average_assertion_success_rate = decimal.Decimal(
                (sum(case_assertions) / len(case_assertions)) * 100 if case_assertions else 0
            )
            self.average_duration = decimal.Decimal(averages.task_duration)
        else:
            self.average_assertion_success_rate = decimal.Decimal(0)
            self.average_duration = decimal.Decimal(0)


def get_assertion_average(*, case: reporting.ReportCase) -> decimal.Decimal:
    """
    Return the fraction of assertions that passed for a single case.

    Return 0 when there are no assertions.
    """
    assertions = list(case.assertions.values())
    if not assertions:
        return decimal.Decimal(0)

    passing_count = sum(1 for a in assertions if a.value)
    return decimal.Decimal(round(passing_count / len(assertions), 2))


def get_assertions_as_str(
    assertions: dict[str, evaluators.EvaluationResult[bool]],
) -> str:
    """
    Format assertions as a human-readable multi-line string.
    """
    if not assertions:
        return "N/A"

    lines: list[str] = []
    for assertion in assertions.values():
        emoji = "PASS" if assertion.value else "FAIL"
        name_part = f"{assertion.name}: " if assertion.name else ""
        reason_part = assertion.reason or ""
        lines.append(f"[{emoji}] {name_part}{reason_part}")

    return "\n".join(lines)


def get_task_duration_as_str(*, duration: float) -> str:
    """
    Format the task duration as a string with two decimal places.
    """
    return f"{duration:.2f}s"
