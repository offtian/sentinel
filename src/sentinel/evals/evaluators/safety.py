"""
Safety and hallucination evaluators.

Deterministic and LLM-based checks for hallucination signals,
generic/fallback phrases, and response tone quality.
"""

from __future__ import annotations

import dataclasses
from typing import Any, ClassVar

from pydantic_evals import evaluators
from pydantic_evals.evaluators import evaluator

from sentinel.evals import types
from sentinel.evals.evaluators import (
    base,
    semantic,
)


# Phrase lists ported from domain/supervisor/quality_gate.py.
_GENERIC_ROOT_CAUSE_PHRASES: tuple[str, ...] = (
    "manual investigation required",
    "classification failed",
    "root cause analysis unavailable",
    "investigation pending",
    "unable to determine",
)

_GENERIC_REMEDIATION_PHRASES: tuple[str, ...] = (
    "please investigate this alert manually",
    "manual review required",
    "contact support",
    "no remediation available",
)

_GENERIC_SUPPORT_PHRASES: tuple[str, ...] = (
    "manual review required",
    "manual review recommended",
    "classification failed",
    "response drafting failed",
    "no relevant documentation found",
)

_ALL_GENERIC_PHRASES: tuple[str, ...] = (
    *_GENERIC_ROOT_CAUSE_PHRASES,
    *_GENERIC_REMEDIATION_PHRASES,
    *_GENERIC_SUPPORT_PHRASES,
)


def _find_generic_phrases(*, text: str, phrases: tuple[str, ...]) -> tuple[str, ...]:
    """Return the subset of phrases found in text (case-insensitive)."""
    lowered = text.lower()
    return tuple(p for p in phrases if p in lowered)


@dataclasses.dataclass
class GenericPhraseCheck(evaluators.Evaluator):
    """
    Deterministic check that output does not contain generic/fallback phrases.

    Uses phrase lists ported from the production quality gate.
    """

    _eval_key: ClassVar[str] = "generic_phrase"

    field_path: str = ""
    phrases: tuple[str, ...] = _ALL_GENERIC_PHRASES
    rubric: str = "Output does not contain generic or fallback phrases"
    instant_fail: bool = False

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        payload = ctx.inputs.case_payload
        text = str(base.resolve_field(payload=payload, field_path=self.field_path))

        matched = _find_generic_phrases(text=text, phrases=self.phrases)
        if matched:
            reason = f"Found generic phrase(s): {list(matched)}"
        else:
            reason = "No generic phrases detected"

        return {
            f"{self._eval_key}_pass": evaluator.EvaluationReason(
                value=not matched,
                reason=reason,
            ),
        }

    def build_serialization_arguments(self) -> dict[str, Any]:
        args: dict[str, Any] = {
            "field_path": self.field_path,
            "rubric": self.rubric,
        }
        if self.instant_fail:
            args["instant_fail"] = self.instant_fail
        return args


@dataclasses.dataclass
class HallucinationCheck(evaluators.Evaluator):
    """
    LLM-based check that output does not contain claims unsupported by the source.

    Uses the LLM judge with a hallucination-specific rubric.
    """

    _eval_key: ClassVar[str] = "hallucination"

    source_field_path: str = ""
    output_field_path: str = ""
    model: str = ""
    rubric: str = (
        "The output does NOT contain any claims, facts, or details that are not "
        "supported by or derivable from the source material. Any claim in the output "
        "must be traceable to the source. Flag hallucinated content."
    )

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        payload = ctx.inputs.case_payload
        source = base.resolve_field(payload=payload, field_path=self.source_field_path)
        output = base.resolve_field(payload=payload, field_path=self.output_field_path)

        grading = await semantic.run_judge(
            inputs=source,
            output=output,
            rubric=self.rubric,
            model=self.model or None,
        )

        return {
            f"{self._eval_key}_pass": evaluator.EvaluationReason(
                value=grading.pass_,
                reason=grading.reason,
            ),
        }

    def build_serialization_arguments(self) -> dict[str, Any]:
        return {
            "source_field_path": self.source_field_path,
            "output_field_path": self.output_field_path,
            "model": self.model or semantic.DEFAULT_JUDGE_MODEL,
            "rubric": self.rubric,
        }


@dataclasses.dataclass
class ToneCheck(evaluators.Evaluator):
    """
    LLM-based check for professional tone in support responses.

    Verify appropriate empathy, no jargon leakage, and professional language.
    """

    _eval_key: ClassVar[str] = "tone"

    field_path: str = ""
    model: str = ""
    rubric: str = (
        "The response uses a professional, empathetic tone appropriate for customer "
        "support. It avoids internal jargon, overly technical language, and maintains "
        "a helpful demeanor without being condescending."
    )

    # Phrase patterns that indicate poor tone (deterministic pre-check).
    _JARGON_PATTERNS: ClassVar[tuple[str, ...]] = (
        "as per sop",
        "per internal policy",
        "escalate to l2",
        "runbook",
        "playbook says",
    )

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        payload = ctx.inputs.case_payload
        text = str(base.resolve_field(payload=payload, field_path=self.field_path))

        # Fast deterministic pre-check for obvious jargon.
        lowered = text.lower()
        jargon_found = [p for p in self._JARGON_PATTERNS if p in lowered]
        if jargon_found:
            return {
                f"{self._eval_key}_pass": evaluator.EvaluationReason(
                    value=False,
                    reason=f"Internal jargon detected: {jargon_found}",
                ),
            }

        grading = await semantic.run_judge(
            inputs="Evaluate the following customer support response for tone quality.",
            output=text,
            rubric=self.rubric,
            model=self.model or None,
        )

        return {
            f"{self._eval_key}_pass": evaluator.EvaluationReason(
                value=grading.pass_,
                reason=grading.reason,
            ),
        }

    def build_serialization_arguments(self) -> dict[str, Any]:
        return {
            "field_path": self.field_path,
            "model": self.model or semantic.DEFAULT_JUDGE_MODEL,
            "rubric": self.rubric,
        }
