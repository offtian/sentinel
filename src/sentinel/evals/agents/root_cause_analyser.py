"""
Evaluate root cause analysis quality against golden datasets.

Metrics:
- keyword_coverage: fraction of expected keywords found in root_cause (threshold 0.5)
- has_remediation: non-empty remediation steps (threshold 1.0)
- has_evidence: at least one evidence item (threshold 1.0)
- confidence_above_minimum: raw confidence >= expected min (threshold 1.0)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sentinel.evals import framework

_DATASETS_DIR = Path(__file__).parent.parent / "datasets"
_DATASET_FILE = _DATASETS_DIR / "root_cause_cases.json"


def _load_dataset() -> list[dict[str, Any]]:
    with _DATASET_FILE.open() as f:
        return json.load(f)  # type: ignore[no-any-return]


def _keyword_coverage(*, text: str, keywords: list[str]) -> float:
    """
    Return fraction of keywords found in text (case-insensitive).
    """
    if not keywords:
        return 1.0
    text_lower = text.lower()
    matched = sum(1 for kw in keywords if kw.lower() in text_lower)
    return matched / len(keywords)


def _has_items(*, items: list[Any]) -> float:
    """Return 1.0 if the list has at least one item, else 0.0."""
    return 1.0 if items else 0.0


def _confidence_above_minimum(*, actual: float, minimum: float) -> float:
    """Return 1.0 if actual confidence >= minimum, else 0.0."""
    return 1.0 if actual >= minimum else 0.0


class RootCauseAnalyserEvaluator(framework.BaseEvaluator):
    """
    Evaluate root cause analysis quality.

    Metrics:
    - keyword_coverage: fraction of expected keywords found in root_cause (threshold 0.5)
    - has_remediation: non-empty remediation steps (threshold 1.0)
    - has_evidence: at least one evidence item (threshold 1.0)
    - confidence_above_minimum: raw confidence >= expected min (threshold 1.0)
    """

    @property
    def name(self) -> str:
        return "root_cause_analyser"

    def load_dataset(self) -> list[dict[str, Any]]:
        """
        Load the root cause analyser golden dataset.
        """
        return _load_dataset()

    async def evaluate_case(self, *, case: dict) -> framework.EvalCaseResult:
        """
        Evaluate a single root cause analysis case.

        :param case: Dict with ``id``, ``output``, and ``expected`` keys.
        """
        case_id: str = case["id"]
        output = case["output"]
        expected = case["expected"]

        kw_metric = framework.make_metric(
            name="keyword_coverage",
            value=_keyword_coverage(
                text=output["root_cause"],
                keywords=expected["root_cause_keywords"],
            ),
            threshold=0.5,
        )
        remediation_metric = framework.make_metric(
            name="has_remediation",
            value=_has_items(items=output["remediation_steps"]),
            threshold=1.0,
        )
        evidence_metric = framework.make_metric(
            name="has_evidence",
            value=_has_items(items=output["evidence"]),
            threshold=1.0,
        )
        confidence_metric = framework.make_metric(
            name="confidence_above_minimum",
            value=_confidence_above_minimum(
                actual=output["confidence"],
                minimum=expected["min_confidence"],
            ),
            threshold=1.0,
        )

        metrics = (kw_metric, remediation_metric, evidence_metric, confidence_metric)
        return framework.EvalCaseResult(
            case_id=case_id,
            metrics=metrics,
            passed=all(m.passed for m in metrics),
        )
