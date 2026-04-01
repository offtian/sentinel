"""
Evaluation tests for the SRE investigation pipeline.

These tests run golden cases through the pipeline with mocked LLM agents
and verify that the outputs meet quality rubrics. They are designed to
catch regressions when prompts, models, or pipeline logic change.

Run with: ``make test-evals`` or ``uv run pytest tests/evals/ -x -vv``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sentinel.domain.sre import entities as sre_entities
from sentinel.domain.sre import holmes_adapter
from sentinel.interfaces.graphs import sre_investigation
from sentinel.settings import PROMPTS_DIR
from tests import factories


DATASETS_DIR = Path(__file__).parent / "datasets"


def _load_golden_cases() -> list[dict[str, Any]]:
    with (DATASETS_DIR / "sre_golden.json").open() as f:
        return json.load(f)


GOLDEN_CASES = _load_golden_cases()


def _build_alert(case: dict[str, Any]) -> sre_entities.Alert:
    return sre_entities.Alert.model_validate(case["alert"])


def _build_holmes(case: dict[str, Any]) -> factories.MockHolmesAdapter:
    return factories.MockHolmesAdapter(
        result=holmes_adapter.HolmesInvestigationResult(
            analysis=case["holmes_analysis"],
            tool_calls=case["holmes_tool_calls"],  # type: ignore[assignment]
            sources_queried=case["holmes_sources"],  # type: ignore[assignment]
        )
    )


@pytest.mark.usefixtures("patch_alert_classifier", "patch_root_cause_analyser")
class TestSreGoldenCases:
    """Run each golden case through the pipeline and verify quality rubrics."""

    @pytest.mark.parametrize(
        "case",
        GOLDEN_CASES,
        ids=[c["id"] for c in GOLDEN_CASES],
    )
    async def test_golden_case(
        self,
        case: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given a golden test case with known alert and Holmes data
        alert = _build_alert(case)
        holmes = _build_holmes(case)
        expected = case["expected"]

        monkeypatch.setattr(
            "sentinel.vendors.slack.post_investigation_summary",
            _noop_slack,
        )

        # When running the investigation pipeline
        reply = await sre_investigation.investigate_alert(
            alert=alert,
            holmes=holmes,
            post_to_slack=False,
        )

        # Then the reply is populated
        assert reply.root_cause is not None, f"[{case['id']}] root_cause should not be None"
        assert reply.remediation is not None, f"[{case['id']}] remediation should not be None"
        assert reply.confidence is not None, f"[{case['id']}] confidence should not be None"

        # Then confidence meets minimum threshold
        assert reply.confidence.total >= expected["min_confidence"], (
            f"[{case['id']}] confidence {reply.confidence.total} < {expected['min_confidence']}"
        )


class TestSrePromptTemplatesExist:
    """Verify that all required prompt templates are present and non-empty."""

    @pytest.mark.parametrize(
        "template_name",
        ["alert_classifier", "root_cause_analyser"],
    )
    def test_prompt_template_exists(self, template_name: str) -> None:
        # Given a required prompt template name
        template_path = PROMPTS_DIR / f"{template_name}.j2"

        # Then the template file exists and is non-empty
        assert template_path.exists(), f"Missing prompt template: {template_path}"
        assert template_path.stat().st_size > 0, f"Empty prompt template: {template_path}"


async def _noop_slack(**kwargs: object) -> None:
    pass
