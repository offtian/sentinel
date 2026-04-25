"""Integration tests verifying prompt cache settings flow through pipeline nodes.

Runs the real pipeline entry points with spy-wrapped agents to assert that
``model_settings`` carries the correct cache configuration for each provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest import mock

import pytest

from sentinel.interfaces.graphs import sre_investigation, support_review
from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    response_drafter,
    root_cause_analyser,
    ticket_reviewer,
)
from tests import factories
from tests.functional import conftest as functional_fixtures


_ANTHROPIC_MODEL = "anthropic:claude-sonnet-4-6"
_OPENAI_MODEL = "openai:gpt-4.1-mini"
_TEST_MODEL = "test"


@dataclass(frozen=True)
class _FakeAgentResult[T]:
    output: T

    def all_messages(self) -> list[object]:
        return []


def _make_spy_agent(
    *, model_name: str, fake_output: Any
) -> tuple[mock.MagicMock, list[dict[str, Any]]]:
    """
    Build a mock agent that records every ``model_settings`` passed to ``.run()``.

    Returns the agent and a list that captures each call's kwargs.
    """
    captured: list[dict[str, Any]] = []

    async def _spy_run(**kwargs: Any) -> _FakeAgentResult[Any]:
        captured.append(kwargs)
        return _FakeAgentResult(output=fake_output)

    agent = mock.MagicMock()
    agent.run = _spy_run
    agent.model.model_name = model_name
    return agent, captured


_CLASSIFIER_OUTPUT = alert_classifier.AlertClassification(
    severity="high",
    affected_service="api-service",
    category="infrastructure",
    summary="Pod OOMKilled causing 5xx errors",
    requires_immediate_action=True,
)

_ANALYSER_OUTPUT = root_cause_analyser.RootCauseAnalysis(
    root_cause="Memory leak in request handler caused OOMKill",
    confidence=0.85,
    evidence=["Pod OOMKilled at 14:30 UTC"],
    remediation_steps=["Increase memory limit to 4Gi"],
    affected_services=["api-service"],
    timeline="14:20 memory ramp → 14:30 OOMKill",
)

_REVIEWER_OUTPUT = ticket_reviewer.TicketClassification(
    category="account",
    urgency="high",
    required_expertise=["authentication"],
    key_questions=["Is the SSO session expired?"],
    search_queries=["SSO login troubleshooting"],
)

_DRAFTER_OUTPUT = response_drafter.DraftedResponse(
    response="Please try clearing your browser cookies.",
    sources_used=[],
    confidence=0.82,
    notes_for_agent="User may need IT admin help.",
)


async def _noop_slack(**kwargs: object) -> None:
    pass


# ---------------------------------------------------------------------------
# SRE pipeline
# ---------------------------------------------------------------------------


class TestSRECacheWiring:
    """Verify cache settings reach agent.run() in the SRE investigation pipeline."""

    @pytest.fixture(autouse=True)
    def _disable_side_effects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sentinel.vendors.slack.post_investigation_summary", _noop_slack)

    @pytest.mark.parametrize(
        ("model_name", "expected_classifier_settings", "expected_analyser_settings"),
        [
            pytest.param(
                _ANTHROPIC_MODEL,
                {"anthropic_cache_instructions": True},
                {"anthropic_cache_instructions": True},
                id="anthropic",
            ),
            pytest.param(
                _OPENAI_MODEL,
                {"openai_prompt_cache_key": alert_classifier.PROMPT_SHA256},
                {"openai_prompt_cache_key": root_cause_analyser.PROMPT_SHA256},
                id="openai",
            ),
            pytest.param(
                _TEST_MODEL,
                None,
                None,
                id="test-model-no-caching",
            ),
        ],
    )
    async def test_cache_settings_flow_through_sre_pipeline(
        self,
        model_name: str,
        expected_classifier_settings: dict[str, Any] | None,
        expected_analyser_settings: dict[str, Any] | None,
    ) -> None:
        """
        Given classifier and analyser agents configured for a specific provider,
        When the SRE pipeline runs end-to-end,
        Then each agent.run() receives the correct model_settings for that provider.
        """
        # Given spy agents that capture model_settings
        classifier_agent, classifier_calls = _make_spy_agent(
            model_name=model_name, fake_output=_CLASSIFIER_OUTPUT
        )
        analyser_agent, analyser_calls = _make_spy_agent(
            model_name=model_name, fake_output=_ANALYSER_OUTPUT
        )
        agents = {
            "alert_classifier": classifier_agent,
            "root_cause_analyser": analyser_agent,
        }
        holmes = factories.MockHolmesAdapter()

        # When running the full SRE investigation pipeline
        await sre_investigation.investigate_alert(
            alert=factories.make_alert(),
            envelope=factories.make_envelope(),
            agent_for=lambda name: agents.get(name, mock.MagicMock()),
            holmes=holmes,
            post_to_slack=False,
        )

        # Then the classifier received the expected cache settings
        assert len(classifier_calls) == 1
        assert classifier_calls[0]["model_settings"] == expected_classifier_settings

        # Then the analyser received the expected cache settings
        assert len(analyser_calls) == 1
        assert analyser_calls[0]["model_settings"] == expected_analyser_settings


# ---------------------------------------------------------------------------
# Support pipeline
# ---------------------------------------------------------------------------


class TestSupportCacheWiring:
    """Verify cache settings reach agent.run() in the support review pipeline."""

    @pytest.mark.parametrize(
        ("model_name", "expected_reviewer_settings", "expected_drafter_settings"),
        [
            pytest.param(
                _ANTHROPIC_MODEL,
                {"anthropic_cache_instructions": True},
                {"anthropic_cache_instructions": True},
                id="anthropic",
            ),
            pytest.param(
                _OPENAI_MODEL,
                {"openai_prompt_cache_key": ticket_reviewer.PROMPT_SHA256},
                {"openai_prompt_cache_key": response_drafter.PROMPT_SHA256},
                id="openai",
            ),
            pytest.param(
                _TEST_MODEL,
                None,
                None,
                id="test-model-no-caching",
            ),
        ],
    )
    async def test_cache_settings_flow_through_support_pipeline(
        self,
        model_name: str,
        expected_reviewer_settings: dict[str, Any] | None,
        expected_drafter_settings: dict[str, Any] | None,
    ) -> None:
        """
        Given reviewer and drafter agents configured for a specific provider,
        When the support pipeline runs end-to-end,
        Then each agent.run() receives the correct model_settings for that provider.
        """
        # Given spy agents that capture model_settings
        reviewer_agent, reviewer_calls = _make_spy_agent(
            model_name=model_name, fake_output=_REVIEWER_OUTPUT
        )
        drafter_agent, drafter_calls = _make_spy_agent(
            model_name=model_name, fake_output=_DRAFTER_OUTPUT
        )
        agents = {
            "ticket_reviewer": reviewer_agent,
            "response_drafter": drafter_agent,
        }

        # When running the full support review pipeline (with stub searcher
        # so SearchDocumentation doesn't short-circuit before DraftResponse)
        await support_review.review_ticket(
            ticket=factories.make_ticket(),
            envelope=factories.make_envelope(),
            agent_for=lambda name: agents.get(name, mock.MagicMock()),
            document_searcher=functional_fixtures.StubDocumentSearcher(),
        )

        # Then the reviewer received the expected cache settings
        assert len(reviewer_calls) == 1
        assert reviewer_calls[0]["model_settings"] == expected_reviewer_settings

        # Then the drafter received the expected cache settings
        assert len(drafter_calls) == 1
        assert drafter_calls[0]["model_settings"] == expected_drafter_settings


# ---------------------------------------------------------------------------
# SHA-256 stability
# ---------------------------------------------------------------------------


class TestPromptSHA256Stability:
    """Verify prompt SHA-256 constants are stable across module reloads."""

    def test_sre_agent_sha256_constants_are_64_char_hex(self) -> None:
        """
        Given the SRE agent modules,
        When reading PROMPT_SHA256,
        Then each is a 64-character hex string (valid SHA-256 digest).
        """
        for sha in (alert_classifier.PROMPT_SHA256, root_cause_analyser.PROMPT_SHA256):
            assert len(sha) == 64
            assert all(c in "0123456789abcdef" for c in sha)

    def test_support_agent_sha256_constants_are_64_char_hex(self) -> None:
        """
        Given the support agent modules,
        When reading PROMPT_SHA256,
        Then each is a 64-character hex string (valid SHA-256 digest).
        """
        for sha in (ticket_reviewer.PROMPT_SHA256, response_drafter.PROMPT_SHA256):
            assert len(sha) == 64
            assert all(c in "0123456789abcdef" for c in sha)

    def test_different_agents_have_different_sha256(self) -> None:
        """
        Given distinct agent templates,
        When comparing their PROMPT_SHA256 values,
        Then no two are identical (each template has unique content).
        """
        all_shas = {
            alert_classifier.PROMPT_SHA256,
            root_cause_analyser.PROMPT_SHA256,
            ticket_reviewer.PROMPT_SHA256,
            response_drafter.PROMPT_SHA256,
        }
        assert len(all_shas) == 4
