"""
Evaluate alert classification quality against golden datasets.

Metrics:
- severity_accuracy: exact match on severity label (threshold 0.9)
- category_accuracy: exact match on category (threshold 0.85)
- has_summary: non-empty summary (threshold 1.0)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sentinel.evals import framework

_DATASETS_DIR = Path(__file__).parent.parent / "datasets"
_DATASET_FILE = _DATASETS_DIR / "alert_classifier_cases.json"


def _load_dataset() -> list[dict[str, Any]]:
    with _DATASET_FILE.open() as f:
        return json.load(f)  # type: ignore[no-any-return]


def _severity_matches(*, actual: str, expected: str) -> float:
    """Return 1.0 if severity labels match (case-insensitive), else 0.0."""
    return 1.0 if actual.strip().lower() == expected.strip().lower() else 0.0


def _category_matches(*, actual: str, expected: str) -> float:
    """Return 1.0 if category labels match (case-insensitive), else 0.0."""
    return 1.0 if actual.strip().lower() == expected.strip().lower() else 0.0


def _has_summary(*, summary: str) -> float:
    """Return 1.0 if summary is non-empty, else 0.0."""
    return 1.0 if summary.strip() else 0.0


class AlertClassifierEvaluator(framework.BaseEvaluator):
    """
    Evaluate alert classification quality.

    Metrics:
    - severity_accuracy: exact match on severity label (threshold 0.9)
    - category_accuracy: exact match on category (threshold 0.85)
    - has_summary: non-empty summary (threshold 1.0)
    """

    @property
    def name(self) -> str:
        return "alert_classifier"

    def load_dataset(self) -> list[dict[str, Any]]:
        """
        Load the alert classifier golden dataset.
        """
        return _load_dataset()

    async def evaluate_case(self, *, case: dict) -> framework.EvalCaseResult:
        """
        Evaluate a single alert classification case.

        :param case: Dict with ``id``, ``output``, and ``expected`` keys.
        """
        case_id: str = case["id"]
        output = case["output"]
        expected = case["expected"]

        severity_metric = framework.make_metric(
            name="severity_accuracy",
            value=_severity_matches(
                actual=output["severity"],
                expected=expected["severity"],
            ),
            threshold=0.9,
        )
        category_metric = framework.make_metric(
            name="category_accuracy",
            value=_category_matches(
                actual=output["category"],
                expected=expected["category"],
            ),
            threshold=0.85,
        )
        summary_metric = framework.make_metric(
            name="has_summary",
            value=_has_summary(summary=output["summary"]),
            threshold=1.0,
        )

        metrics = (severity_metric, category_metric, summary_metric)
        return framework.EvalCaseResult(
            case_id=case_id,
            metrics=metrics,
            passed=all(m.passed for m in metrics),
        )
