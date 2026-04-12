"""
Structural check evaluator.

Perform deterministic structural checks on case payload fields:
non-empty text, non-empty lists, exact match, and numeric comparisons.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Literal, assert_never

from pydantic_evals import evaluators
from pydantic_evals.evaluators import evaluator

from sentinel.evals import types
from sentinel.evals.evaluators import base


CheckType = Literal["non_empty", "has_items", "exact_match", "gte"]


def _check_non_empty(*, value: Any) -> tuple[bool, str]:
    """
    Check that a string value is non-empty after stripping whitespace.
    """
    text = str(value).strip()
    if text:
        return True, "Field is non-empty"
    return False, "Field is empty"


def _check_has_items(*, value: Any) -> tuple[bool, str]:
    """
    Check that a list/sequence has at least one item.
    """
    if isinstance(value, (list, tuple)) and len(value) > 0:
        return True, f"Field has {len(value)} item(s)"
    return False, "Field has no items"


def _check_exact_match(*, actual: Any, expected: Any) -> tuple[bool, str]:
    """
    Check case-insensitive string equality.
    """
    actual_str = str(actual).strip().lower()
    expected_str = str(expected).strip().lower()
    if actual_str == expected_str:
        return True, f"Values match: {actual_str!r}"
    return False, f"Values differ: actual={actual_str!r}, expected={expected_str!r}"


def _check_gte(*, actual: Any, expected: Any) -> tuple[bool, str]:
    """
    Check that actual numeric value is greater than or equal to expected.
    """
    actual_num = float(actual)
    expected_num = float(expected)
    if actual_num >= expected_num:
        return True, f"Value {actual_num} >= {expected_num}"
    return False, f"Value {actual_num} < {expected_num}"


@dataclasses.dataclass
class StructuralCheck(evaluators.Evaluator):
    """
    Perform a deterministic structural check on a case payload field.

    Supported check types:
    - ``non_empty``: string field is non-empty after stripping
    - ``has_items``: list field has at least one element
    - ``exact_match``: case-insensitive string equality with expected field
    - ``gte``: numeric value >= expected field value
    """

    field_path: str = ""
    expected_field_path: str | None = None
    check_type: CheckType = "non_empty"
    rubric: str = "Structural check passes"
    instant_fail: bool = False

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        """
        Run the configured structural check and return a pass/fail assertion.
        """
        payload = ctx.inputs.case_payload
        actual = base.resolve_field(payload=payload, field_path=self.field_path)

        if self.check_type == "non_empty":
            passed, reason = _check_non_empty(value=actual)
        elif self.check_type == "has_items":
            passed, reason = _check_has_items(value=actual)
        elif self.check_type == "exact_match":
            expected = base.resolve_field(
                payload=payload, field_path=self.expected_field_path or ""
            )
            passed, reason = _check_exact_match(actual=actual, expected=expected)
        elif self.check_type == "gte":
            expected = base.resolve_field(
                payload=payload, field_path=self.expected_field_path or ""
            )
            passed, reason = _check_gte(actual=actual, expected=expected)
        else:
            assert_never(self.check_type)

        leaf_field = self.field_path.rsplit(".", maxsplit=1)[-1]
        key = f"{leaf_field}_{self.check_type}"
        return {
            f"{key}_pass": evaluator.EvaluationReason(
                value=passed,
                reason=reason,
            ),
        }

    def build_serialization_arguments(self) -> dict[str, Any]:
        """
        Include key fields in serialization for logging and reporting.
        """
        args: dict[str, Any] = {
            "field_path": self.field_path,
            "check_type": self.check_type,
            "rubric": self.rubric,
        }
        if self.expected_field_path is not None:
            args["expected_field_path"] = self.expected_field_path
        if self.instant_fail:
            args["instant_fail"] = self.instant_fail
        return args
