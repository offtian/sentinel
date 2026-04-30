"""
Unit tests for flag-aware SRE routing in the Slack event handler (T34).

When ``langgraph_sre_enabled`` is ``True``, ``_run_sre`` must call
``workflows_sre_investigation.investigate_alert`` (LangGraph path).
When the flag is ``False``, it must call the legacy
``investigation.investigate_alert`` (Pydantic Graph path).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sentinel.domain.approval import entities as approval_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.pipeline import types as pipeline_types
from sentinel.interfaces.slack import event_handlers as event_handlers_mod
from sentinel.interfaces.workflows import sre_investigation as workflows_sre_investigation_mod


def _make_fake_outcome() -> workflows_sre_investigation_mod.InvestigationOutcome:
    """Return a minimal InvestigationOutcome for use in tests."""
    confidence = confidence_entities.ConfidenceScore.from_factors(
        source_count=5,
        max_expected_sources=5,
        relevance=0.9,
        recency=0.8,
    )
    return workflows_sre_investigation_mod.InvestigationOutcome(
        request_id=uuid.uuid4(),
        classification_category="performance",
        root_cause="High CPU usage caused by runaway process",
        remediation="Restart the affected pod",
        confidence=confidence,
        needs_approval=False,
        findings_published=True,
        interrupt_payload=None,
        approval_decision=approval_entities.ApprovalDecision.APPROVED,
    )


class TestRunSreRouting:
    @pytest.mark.asyncio
    async def test_uses_langgraph_when_flag_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given the langgraph_sre_enabled flag is True
        monkeypatch.setattr(
            event_handlers_mod.settings,
            "langgraph_sre_enabled",
            True,
        )

        fake_outcome = _make_fake_outcome()
        mock_wf_investigate = AsyncMock(return_value=fake_outcome)
        mock_build_graph = MagicMock(return_value=MagicMock())

        monkeypatch.setattr(
            event_handlers_mod.workflows_sre_investigation,
            "investigate_alert",
            mock_wf_investigate,
        )
        monkeypatch.setattr(
            event_handlers_mod.workflows_sre_investigation,
            "build_sre_investigation_graph",
            mock_build_graph,
        )

        mock_client = AsyncMock()
        mock_status_client = AsyncMock()

        with patch(
            "sentinel.interfaces.slack.event_handlers.SlackStatusUpdateClient",
            return_value=mock_status_client,
        ):
            # When _run_sre is called
            await event_handlers_mod._run_sre(
                "pods are crashlooping",
                client=mock_client,
                channel="C123",
                thread_ts="12345.678",
            )

        # Then the LangGraph workflow investigate_alert was called
        mock_wf_investigate.assert_awaited_once()
        call_kwargs = mock_wf_investigate.call_args.kwargs
        assert "alert" in call_kwargs
        assert "envelope" in call_kwargs
        assert "graph" in call_kwargs

    @pytest.mark.asyncio
    async def test_uses_legacy_when_flag_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given the langgraph_sre_enabled flag is False
        monkeypatch.setattr(
            event_handlers_mod.settings,
            "langgraph_sre_enabled",
            False,
        )

        fake_reply = pipeline_types.InvestigationReply(
            alert_id="slack-12345.678",
            root_cause="Memory leak in pod",
            remediation="Restart and patch",
            findings_summary="Found leak in payments service",
        )
        mock_legacy_investigate = AsyncMock(return_value=fake_reply)

        monkeypatch.setattr(
            event_handlers_mod.investigation,
            "investigate_alert",
            mock_legacy_investigate,
        )

        mock_client = AsyncMock()
        mock_status_client = AsyncMock()
        mock_cfg = MagicMock()
        mock_cfg.agent_for = MagicMock(return_value=MagicMock())
        mock_cfg.build_k8s_investigation_adapter = MagicMock(return_value=None)
        mock_cfg.build_challenger_adapter = MagicMock(return_value=None)

        with (
            patch(
                "sentinel.interfaces.slack.event_handlers.SlackStatusUpdateClient",
                return_value=mock_status_client,
            ),
            patch(
                "sentinel.interfaces.slack.event_handlers.config_mod.get_config",
                return_value=mock_cfg,
            ),
        ):
            # When _run_sre is called
            await event_handlers_mod._run_sre(
                "pods are crashlooping",
                client=mock_client,
                channel="C123",
                thread_ts="12345.678",
            )

        # Then the legacy investigation.investigate_alert was called
        mock_legacy_investigate.assert_awaited_once()
        call_kwargs = mock_legacy_investigate.call_args.kwargs
        assert "alert" in call_kwargs
        assert "envelope" in call_kwargs
        assert "agent_for" in call_kwargs
