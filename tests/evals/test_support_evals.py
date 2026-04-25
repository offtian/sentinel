"""
Evaluation tests for the support review pipeline.

These tests run golden cases through the pipeline with mocked LLM agents
and verify that the outputs meet quality rubrics.

Run with: ``just test-evals`` or ``uv run pytest tests/evals/ -x -vv``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sentinel.domain.support import entities as support_entities
from sentinel.interfaces.graphs import support_review
from sentinel.settings import PROMPTS_DIR
from tests import factories
from tests.functional.conftest import StubDocumentSearcher, StubPastTicketSearcher


DATASETS_DIR = Path(__file__).parent / "datasets"


def _load_golden_cases() -> list[dict[str, Any]]:
    with (DATASETS_DIR / "support_golden.json").open() as f:
        return json.load(f)


GOLDEN_CASES = _load_golden_cases()


def _build_ticket(case: dict[str, Any]) -> support_entities.Ticket:
    return support_entities.Ticket.model_validate(case["ticket"])


class TestSupportGoldenCases:
    """Run each golden case through the pipeline and verify quality rubrics."""

    @pytest.mark.parametrize(
        "case",
        GOLDEN_CASES,
        ids=[c["id"] for c in GOLDEN_CASES],
    )
    async def test_golden_case(self, case: dict[str, Any], fake_support_config: Any) -> None:
        # Given a golden test case with a known ticket
        ticket = _build_ticket(case)
        expected = case["expected"]

        # When running the support review pipeline
        reply = await support_review.review_ticket(
            ticket=ticket,
            envelope=factories.make_envelope(),
            agent_for=fake_support_config.agent_for,
            document_searcher=StubDocumentSearcher(),
            ticket_searcher=StubPastTicketSearcher(),
        )

        # Then the reply is populated
        assert reply.suggested_response, f"[{case['id']}] response should not be empty"
        assert reply.confidence is not None, f"[{case['id']}] confidence should not be None"

        # Then confidence meets minimum threshold
        assert reply.confidence.total >= expected["min_confidence"], (
            f"[{case['id']}] confidence {reply.confidence.total} < {expected['min_confidence']}"
        )

        # Then sources are cited when expected
        if expected.get("should_cite_sources"):
            assert reply.sources, f"[{case['id']}] expected sources to be cited"


class TestSupportPromptTemplatesExist:
    """Verify that all required prompt templates are present and non-empty."""

    @pytest.mark.parametrize(
        "template_name",
        ["ticket_reviewer", "response_drafter"],
    )
    def test_prompt_template_exists(self, template_name: str) -> None:
        # Given a required prompt template name
        template_path = PROMPTS_DIR / f"{template_name}.j2"

        # Then the template file exists and is non-empty
        assert template_path.exists(), f"Missing prompt template: {template_path}"
        assert template_path.stat().st_size > 0, f"Empty prompt template: {template_path}"
