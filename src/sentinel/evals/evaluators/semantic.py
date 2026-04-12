"""
LLM-as-judge semantic evaluators.

Four evaluators assess text quality dimensions using an LLM judge:
faithfulness to source, relevance to input, internal coherence,
and completeness against an expected checklist.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings
from pydantic_evals import evaluators
from pydantic_evals.evaluators import evaluator

from sentinel.evals import types
from sentinel.evals.evaluators import base


DEFAULT_JUDGE_MODEL = os.environ.get("EVAL_JUDGE_LLM", "openai/gpt-4.1-mini")


class _GradingOutput(BaseModel, populate_by_name=True):
    reason: str
    pass_: bool = Field(validation_alias="pass", serialization_alias="pass")
    score: float


def _get_judge_agent(
    *, system_prompt: str, model: str | None = None
) -> Agent[None, _GradingOutput]:
    """
    Return a PydanticAI agent configured for structured grading output.

    Uses ``"test"`` as placeholder model; the actual model is passed at ``.run()`` time.
    """
    return Agent(
        model or "test",
        output_type=_GradingOutput,
        name="eval_judge",
        system_prompt=system_prompt,
    )


def _stringify(value: Any) -> str:
    """
    Convert a value to a string suitable for LLM judge input.
    """
    if isinstance(value, str):
        return value
    try:
        import pydantic_core

        return pydantic_core.to_json(value).decode()
    except Exception:
        return repr(value)


_JUDGE_SYSTEM_PROMPT = """\
You are grading output according to a user-specified rubric.
If the statement in the rubric is true for the provided input and output, \
then the output passes the test.
You respond with a JSON object with this structure: \
{reason: string, pass: boolean, score: number}
The score should be between 0.0 and 1.0."""


async def run_judge(
    *,
    inputs: Any,
    output: Any,
    rubric: str,
    model: str | None = None,
) -> _GradingOutput:
    """
    Run the LLM judge and return the structured grading output.
    """
    agent = _get_judge_agent(system_prompt=_JUDGE_SYSTEM_PROMPT)
    user_prompt = (
        f"<Input>\n{_stringify(inputs)}\n</Input>\n"
        f"<Output>\n{_stringify(output)}\n</Output>\n"
        f"<Rubric>\n{rubric}\n</Rubric>"
    )
    result = await agent.run(
        user_prompt,
        model=model or DEFAULT_JUDGE_MODEL,
        model_settings=ModelSettings(temperature=0),
    )
    return result.output


@dataclasses.dataclass
class FaithfulnessCheck(evaluators.Evaluator):
    """
    Judge whether the output is faithful to the source material.

    Compare a source field (e.g. input context) against an output field
    and assess whether claims in the output are supported by the source.
    """

    source_field_path: str = ""
    output_field_path: str = ""
    model: str = ""
    rubric: str = (
        "The output is faithful to the source material and does not introduce unsupported claims"
    )

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        payload = ctx.inputs.case_payload
        source = base.resolve_field(payload=payload, field_path=self.source_field_path)
        output = base.resolve_field(payload=payload, field_path=self.output_field_path)

        grading = await run_judge(
            inputs=source,
            output=output,
            rubric=self.rubric,
            model=self.model or None,
        )

        evaluation_name = self.get_default_evaluation_name()
        return {
            f"{evaluation_name}_pass": evaluator.EvaluationReason(
                value=grading.pass_,
                reason=grading.reason,
            ),
        }

    def build_serialization_arguments(self) -> dict[str, Any]:
        return {
            "source_field_path": self.source_field_path,
            "output_field_path": self.output_field_path,
            "model": self.model or DEFAULT_JUDGE_MODEL,
            "rubric": self.rubric,
        }


@dataclasses.dataclass
class RelevanceCheck(evaluators.Evaluator):
    """
    Judge whether the output addresses the input query.
    """

    input_field_path: str = ""
    output_field_path: str = ""
    model: str = ""
    rubric: str = "The output directly and completely addresses the input query"

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        payload = ctx.inputs.case_payload
        input_text = base.resolve_field(payload=payload, field_path=self.input_field_path)
        output_text = base.resolve_field(payload=payload, field_path=self.output_field_path)

        grading = await run_judge(
            inputs=input_text,
            output=output_text,
            rubric=self.rubric,
            model=self.model or None,
        )

        evaluation_name = self.get_default_evaluation_name()
        return {
            f"{evaluation_name}_pass": evaluator.EvaluationReason(
                value=grading.pass_,
                reason=grading.reason,
            ),
        }

    def build_serialization_arguments(self) -> dict[str, Any]:
        return {
            "input_field_path": self.input_field_path,
            "output_field_path": self.output_field_path,
            "model": self.model or DEFAULT_JUDGE_MODEL,
            "rubric": self.rubric,
        }


@dataclasses.dataclass
class CoherenceCheck(evaluators.Evaluator):
    """
    Judge whether the output text is internally consistent and well-structured.
    """

    field_path: str = ""
    model: str = ""
    rubric: str = (
        "The output is internally consistent, well-structured, and logically ordered. "
        "It does not contradict itself or present information in a confusing way."
    )

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        payload = ctx.inputs.case_payload
        text = base.resolve_field(payload=payload, field_path=self.field_path)

        grading = await run_judge(
            inputs="Evaluate the following text for internal coherence.",
            output=text,
            rubric=self.rubric,
            model=self.model or None,
        )

        evaluation_name = self.get_default_evaluation_name()
        return {
            f"{evaluation_name}_pass": evaluator.EvaluationReason(
                value=grading.pass_,
                reason=grading.reason,
            ),
        }

    def build_serialization_arguments(self) -> dict[str, Any]:
        return {
            "field_path": self.field_path,
            "model": self.model or DEFAULT_JUDGE_MODEL,
            "rubric": self.rubric,
        }


@dataclasses.dataclass
class CompletenessCheck(evaluators.Evaluator):
    """
    Judge whether the output covers all expected aspects.

    Compare the output against a list of expected aspects from the case.
    """

    output_field_path: str = ""
    aspects_field_path: str = ""
    model: str = ""
    rubric: str = "The output covers all expected aspects listed in the checklist"

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        payload = ctx.inputs.case_payload
        output = base.resolve_field(payload=payload, field_path=self.output_field_path)

        try:
            aspects = base.resolve_field(payload=payload, field_path=self.aspects_field_path)
        except KeyError:
            aspects = []

        rubric_with_aspects = f"{self.rubric}\n\nExpected aspects: {_stringify(aspects)}"

        grading = await run_judge(
            inputs=aspects,
            output=output,
            rubric=rubric_with_aspects,
            model=self.model or None,
        )

        evaluation_name = self.get_default_evaluation_name()
        return {
            f"{evaluation_name}_pass": evaluator.EvaluationReason(
                value=grading.pass_,
                reason=grading.reason,
            ),
        }

    def build_serialization_arguments(self) -> dict[str, Any]:
        return {
            "output_field_path": self.output_field_path,
            "aspects_field_path": self.aspects_field_path,
            "model": self.model or DEFAULT_JUDGE_MODEL,
            "rubric": self.rubric,
        }
