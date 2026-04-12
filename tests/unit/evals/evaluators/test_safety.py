"""Tests for evaluators.safety — hallucination and safety evaluators."""

from __future__ import annotations

from unittest import mock

from pydantic_evals import evaluators

from sentinel.evals import types
from sentinel.evals.evaluators import safety, semantic


def _make_ctx(
    *,
    case_payload: dict,
) -> evaluators.EvaluatorContext[types.InputData, str, object]:
    """Build a minimal EvaluatorContext for testing."""
    return mock.MagicMock(
        inputs=types.InputData(agent_name="test", case_payload=case_payload),
    )


class TestGenericPhraseCheck:
    async def test_passes_when_no_generic_phrases(self) -> None:
        # Given a generic phrase evaluator and clean text
        evaluator = safety.GenericPhraseCheck(field_path="output.text")
        ctx = _make_ctx(
            case_payload={"output": {"text": "The pod crashed due to OOM at 14:30 UTC."}},
        )

        # When evaluated
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assert result["GenericPhraseCheck_pass"].value is True

    async def test_fails_when_generic_phrase_detected(self) -> None:
        # Given a generic phrase evaluator and text with a fallback phrase
        evaluator = safety.GenericPhraseCheck(field_path="output.text")
        ctx = _make_ctx(
            case_payload={"output": {"text": "Manual investigation required for this alert."}},
        )

        # When evaluated
        result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        assert result["GenericPhraseCheck_pass"].value is False

    async def test_fails_for_support_generic_phrase(self) -> None:
        # Given text containing a support-specific generic phrase
        evaluator = safety.GenericPhraseCheck(field_path="output.text")
        ctx = _make_ctx(
            case_payload={"output": {"text": "No relevant documentation found for your query."}},
        )

        # When evaluated
        result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        assert result["GenericPhraseCheck_pass"].value is False

    async def test_reason_includes_matched_phrases(self) -> None:
        # Given text with a known generic phrase
        evaluator = safety.GenericPhraseCheck(field_path="output.text")
        ctx = _make_ctx(
            case_payload={"output": {"text": "Classification failed for this input."}},
        )

        # When evaluated
        result = await evaluator.evaluate(ctx)

        # Then the reason mentions the matched phrase
        assert "classification failed" in result["GenericPhraseCheck_pass"].reason.lower()

    async def test_custom_phrases(self) -> None:
        # Given an evaluator with custom phrases
        evaluator = safety.GenericPhraseCheck(
            field_path="output.text",
            phrases=("custom bad phrase",),
        )
        ctx = _make_ctx(
            case_payload={"output": {"text": "This contains a custom bad phrase here."}},
        )

        # When evaluated
        result = await evaluator.evaluate(ctx)

        # Then the custom phrase is detected
        assert result["GenericPhraseCheck_pass"].value is False

    async def test_case_insensitive_matching(self) -> None:
        # Given text with mixed-case generic phrase
        evaluator = safety.GenericPhraseCheck(field_path="output.text")
        ctx = _make_ctx(
            case_payload={"output": {"text": "MANUAL INVESTIGATION REQUIRED immediately."}},
        )

        # When evaluated
        result = await evaluator.evaluate(ctx)

        # Then the phrase is still detected (case-insensitive)
        assert result["GenericPhraseCheck_pass"].value is False


class TestHallucinationCheck:
    async def test_passes_when_judge_finds_no_hallucination(self) -> None:
        # Given a hallucination evaluator and faithful output
        evaluator = safety.HallucinationCheck(
            source_field_path="input.source",
            output_field_path="output.analysis",
        )
        ctx = _make_ctx(
            case_payload={
                "input": {"source": "Pod OOMKilled at 14:30"},
                "output": {"analysis": "The pod was OOMKilled at 14:30"},
            },
        )

        # When the judge finds no hallucination
        with mock.patch.object(
            semantic,
            "run_judge",
            return_value=semantic._GradingOutput(pass_=True, reason="No hallucination", score=1.0),
        ):
            result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assert result["HallucinationCheck_pass"].value is True

    async def test_fails_when_judge_detects_hallucination(self) -> None:
        # Given a hallucination evaluator
        evaluator = safety.HallucinationCheck(
            source_field_path="input.source",
            output_field_path="output.analysis",
        )
        ctx = _make_ctx(
            case_payload={
                "input": {"source": "Pod OOMKilled at 14:30"},
                "output": {"analysis": "Database corruption caused cascading failures"},
            },
        )

        # When the judge detects hallucination
        with mock.patch.object(
            semantic,
            "run_judge",
            return_value=semantic._GradingOutput(
                pass_=False, reason="Hallucinated claims", score=0.1
            ),
        ):
            result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        assert result["HallucinationCheck_pass"].value is False


class TestToneCheck:
    async def test_fails_on_jargon_without_llm_call(self) -> None:
        # Given text containing internal jargon
        evaluator = safety.ToneCheck(field_path="output.response")
        ctx = _make_ctx(
            case_payload={"output": {"response": "As per SOP, escalate to L2 support."}},
        )

        # When evaluated (should not call LLM due to jargon pre-check)
        with mock.patch.object(semantic, "run_judge") as mock_judge:
            result = await evaluator.evaluate(ctx)

        # Then the assertion fails and LLM was NOT called
        assert result["ToneCheck_pass"].value is False
        mock_judge.assert_not_called()

    async def test_passes_when_judge_approves_tone(self) -> None:
        # Given professional text without jargon
        evaluator = safety.ToneCheck(field_path="output.response")
        ctx = _make_ctx(
            case_payload={
                "output": {
                    "response": "Thank you for reaching out. I understand this is frustrating. "
                    "Here is how to resolve the issue..."
                }
            },
        )

        # When the judge approves the tone
        with mock.patch.object(
            semantic,
            "run_judge",
            return_value=semantic._GradingOutput(
                pass_=True, reason="Professional tone", score=0.9
            ),
        ):
            result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assert result["ToneCheck_pass"].value is True

    async def test_detects_runbook_jargon(self) -> None:
        # Given text with "runbook" jargon
        evaluator = safety.ToneCheck(field_path="output.response")
        ctx = _make_ctx(
            case_payload={"output": {"response": "Please follow the runbook for this issue."}},
        )

        # When evaluated
        result = await evaluator.evaluate(ctx)

        # Then jargon is detected
        assert result["ToneCheck_pass"].value is False
        assert "jargon" in result["ToneCheck_pass"].reason.lower()
