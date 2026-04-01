"""
Evaluate response drafting quality against golden datasets.

Metrics:
- has_response: non-empty response text (threshold 1.0)
- source_citation: at least one source cited (threshold 1.0)
- keyword_coverage: fraction of expected keywords in response (threshold 0.4)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sentinel.evals import framework

_DATASETS_DIR = Path(__file__).parent.parent / "datasets"
_DATASET_FILE = _DATASETS_DIR / "response_drafter_cases.json"


def _load_dataset() -> list[dict[str, Any]]:
    with _DATASET_FILE.open() as f:
        return json.load(f)  # type: ignore[no-any-return]


def _has_response(*, response: str) -> float:
    """Return 1.0 if response text is non-empty, else 0.0."""
    return 1.0 if response.strip() else 0.0


def _has_sources(*, sources: list[Any]) -> float:
    """Return 1.0 if at least one source is cited, else 0.0."""
    return 1.0 if sources else 0.0


def _keyword_coverage(*, text: str, keywords: list[str]) -> float:
    """
    Return fraction of keywords found in text (case-insensitive).
    """
    if not keywords:
        return 1.0
    text_lower = text.lower()
    matched = sum(1 for kw in keywords if kw.lower() in text_lower)
    return matched / len(keywords)


class ResponseDrafterEvaluator(framework.BaseEvaluator):
    """
    Evaluate response drafting quality.

    Metrics:
    - has_response: non-empty response text (threshold 1.0)
    - source_citation: at least one source cited (threshold 1.0)
    - keyword_coverage: fraction of expected keywords in response (threshold 0.4)
    """

    @property
    def name(self) -> str:
        return "response_drafter"

    def load_dataset(self) -> list[dict[str, Any]]:
        """
        Load the response drafter golden dataset.
        """
        return _load_dataset()

    async def evaluate_case(self, *, case: dict) -> framework.EvalCaseResult:
        """
        Evaluate a single response draft case.

        :param case: Dict with ``id``, ``output``, and ``expected`` keys.
        """
        case_id: str = case["id"]
        output = case["output"]
        expected = case["expected"]

        response_metric = framework.make_metric(
            name="has_response",
            value=_has_response(response=output["response"]),
            threshold=1.0,
        )
        source_metric = framework.make_metric(
            name="source_citation",
            value=_has_sources(sources=output["sources_used"]),
            threshold=1.0,
        )
        keyword_metric = framework.make_metric(
            name="keyword_coverage",
            value=_keyword_coverage(
                text=output["response"],
                keywords=expected["response_keywords"],
            ),
            threshold=0.4,
        )

        metrics = (response_metric, source_metric, keyword_metric)
        return framework.EvalCaseResult(
            case_id=case_id,
            metrics=metrics,
            passed=all(m.passed for m in metrics),
        )
