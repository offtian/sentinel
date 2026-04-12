"""Tests for evaluators.semantic — LLM-as-judge evaluators with mocked LLM."""

from __future__ import annotations

from unittest import mock

from pydantic_evals import evaluators

from sentinel.evals import types
from sentinel.evals.evaluators import semantic


def _make_ctx(
    *,
    case_payload: dict,
) -> evaluators.EvaluatorContext[types.InputData, str, object]:
    """Build a minimal EvaluatorContext for testing."""
    return mock.MagicMock(
        inputs=types.InputData(agent_name="test", case_payload=case_payload),
    )


def _make_grading(*, pass_: bool, reason: str, score: float) -> semantic._GradingOutput:
    return semantic._GradingOutput(pass_=pass_, reason=reason, score=score)


class TestFaithfulnessCheck:
    async def test_passes_when_judge_approves(self) -> None:
        # Given a faithfulness evaluator and a passing judge response
        evaluator = semantic.FaithfulnessCheck(
            source_field_path="input.source",
            output_field_path="output.text",
        )
        ctx = _make_ctx(
            case_payload={
                "input": {"source": "Server CPU at 95%"},
                "output": {"text": "High CPU usage detected"},
            },
        )

        # When the judge approves
        with mock.patch.object(
            semantic,
            "run_judge",
            return_value=_make_grading(pass_=True, reason="Faithful", score=1.0),
        ):
            result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assert result["FaithfulnessCheck_pass"].value is True

    async def test_fails_when_judge_rejects(self) -> None:
        # Given a faithfulness evaluator and a failing judge response
        evaluator = semantic.FaithfulnessCheck(
            source_field_path="input.source",
            output_field_path="output.text",
        )
        ctx = _make_ctx(
            case_payload={
                "input": {"source": "Server CPU at 95%"},
                "output": {"text": "Database corruption detected"},
            },
        )

        # When the judge rejects
        with mock.patch.object(
            semantic,
            "run_judge",
            return_value=_make_grading(pass_=False, reason="Not faithful", score=0.2),
        ):
            result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        assert result["FaithfulnessCheck_pass"].value is False


class TestRelevanceCheck:
    async def test_passes_when_output_addresses_input(self) -> None:
        # Given a relevance evaluator
        evaluator = semantic.RelevanceCheck(
            input_field_path="input.query",
            output_field_path="output.response",
        )
        ctx = _make_ctx(
            case_payload={
                "input": {"query": "How to reset password?"},
                "output": {"response": "To reset your password, go to Settings..."},
            },
        )

        # When the judge approves
        with mock.patch.object(
            semantic,
            "run_judge",
            return_value=_make_grading(pass_=True, reason="Relevant", score=0.9),
        ):
            result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assert result["RelevanceCheck_pass"].value is True


class TestCoherenceCheck:
    async def test_passes_for_coherent_text(self) -> None:
        # Given a coherence evaluator
        evaluator = semantic.CoherenceCheck(field_path="output.text")
        ctx = _make_ctx(
            case_payload={
                "output": {"text": "The server experienced high CPU due to a memory leak."}
            },
        )

        # When the judge approves
        with mock.patch.object(
            semantic,
            "run_judge",
            return_value=_make_grading(pass_=True, reason="Coherent", score=0.95),
        ):
            result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assert result["CoherenceCheck_pass"].value is True


class TestCompletenessCheck:
    async def test_passes_when_all_aspects_covered(self) -> None:
        # Given a completeness evaluator
        evaluator = semantic.CompletenessCheck(
            output_field_path="output.steps",
            aspects_field_path="expected.required_aspects",
        )
        ctx = _make_ctx(
            case_payload={
                "output": {"steps": "1. Restart pod 2. Check logs 3. Scale up"},
                "expected": {"required_aspects": ["restart", "logs", "scale"]},
            },
        )

        # When the judge approves
        with mock.patch.object(
            semantic,
            "run_judge",
            return_value=_make_grading(pass_=True, reason="Complete", score=1.0),
        ):
            result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assert result["CompletenessCheck_pass"].value is True

    async def test_handles_missing_aspects_field(self) -> None:
        # Given a completeness evaluator with a non-existent aspects path
        evaluator = semantic.CompletenessCheck(
            output_field_path="output.steps",
            aspects_field_path="expected.nonexistent",
        )
        ctx = _make_ctx(
            case_payload={"output": {"steps": "Do something"}},
        )

        # When evaluated (missing aspects defaults to empty list)
        with mock.patch.object(
            semantic,
            "run_judge",
            return_value=_make_grading(pass_=True, reason="No aspects", score=1.0),
        ):
            result = await evaluator.evaluate(ctx)

        # Then the assertion still passes
        assert result["CompletenessCheck_pass"].value is True


class TestBuildSerializationArguments:
    def test_faithfulness_includes_all_fields(self) -> None:
        # Given a configured faithfulness evaluator
        evaluator = semantic.FaithfulnessCheck(
            source_field_path="input.src",
            output_field_path="output.txt",
            model="openai/gpt-4.1",
        )

        # When serializing
        args = evaluator.build_serialization_arguments()

        # Then all fields are present
        assert args["source_field_path"] == "input.src"
        assert args["output_field_path"] == "output.txt"
        assert args["model"] == "openai/gpt-4.1"

    def test_default_model_used_when_not_set(self) -> None:
        # Given an evaluator without explicit model
        evaluator = semantic.RelevanceCheck(
            input_field_path="input.q",
            output_field_path="output.a",
        )

        # When serializing
        args = evaluator.build_serialization_arguments()

        # Then the default judge model is used
        assert args["model"] == semantic.DEFAULT_JUDGE_MODEL
