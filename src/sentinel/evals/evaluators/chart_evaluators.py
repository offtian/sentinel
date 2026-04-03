"""
Chart-specific evaluators for the evaluation framework.

Evaluators:
- YamlStructureCheck: verifies required Kubernetes resource files are present
- SpecCoverageCheck: verifies output has at least the expected number of files
"""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic_evals import evaluators
from pydantic_evals.evaluators import evaluator

from sentinel.evals import types


@dataclasses.dataclass
class YamlStructureCheck(evaluators.Evaluator):
    """
    Verify that required Kubernetes resource files are present in the output.

    Checks that the ``output.files`` list contains files matching each
    required pattern (e.g. ``"deployment"``, ``"service"``).
    """

    required_file_patterns: tuple[str, ...] = ()
    rubric: str = "Required Kubernetes resource files are present"

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        """
        Check that all required file patterns appear in output files.
        """
        payload = ctx.inputs.case_payload
        files = payload.get("output", {}).get("files", [])
        file_paths = [f.get("path", "").lower() for f in files]

        missing: list[str] = []
        for pattern in self.required_file_patterns:
            if not any(pattern in path for path in file_paths):
                missing.append(pattern)

        passed = len(missing) == 0
        reason = (
            "All required files present"
            if passed
            else f"Missing files matching: {', '.join(missing)}"
        )

        evaluation_name = self.get_default_evaluation_name()
        return {
            f"{evaluation_name}_pass": evaluator.EvaluationReason(
                value=passed,
                reason=reason,
            ),
        }

    def build_serialization_arguments(self) -> dict[str, Any]:
        """
        Return fields for serialization in logging and reporting.
        """
        return {
            "required_file_patterns": self.required_file_patterns,
            "rubric": self.rubric,
        }


@dataclasses.dataclass
class SpecCoverageCheck(evaluators.Evaluator):
    """
    Verify that the output has at least the minimum expected number of files.
    """

    min_files_field: str = "expected.min_files"
    rubric: str = "Generated file count meets minimum"

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        """
        Check file count against expected minimum.
        """
        payload = ctx.inputs.case_payload
        files = payload.get("output", {}).get("files", [])
        actual_count = len(files)

        expected_min: Any = payload
        for segment in self.min_files_field.split("."):
            expected_min = expected_min.get(segment, 0)

        passed = actual_count >= int(expected_min)
        reason = f"Generated {actual_count} files (minimum: {expected_min})"

        evaluation_name = self.get_default_evaluation_name()
        return {
            f"{evaluation_name}_pass": evaluator.EvaluationReason(
                value=passed,
                reason=reason,
            ),
        }

    def build_serialization_arguments(self) -> dict[str, Any]:
        """
        Return fields for serialization in logging and reporting.
        """
        return {
            "min_files_field": self.min_files_field,
            "rubric": self.rubric,
        }
