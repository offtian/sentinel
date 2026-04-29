"""
Unit tests for flag-aware SRE routing in the Streamlit chat app (T36).

When ``langgraph_sre_enabled`` is ``True``, ``_run_sre`` must call
``workflows_sre_investigation.investigate_alert`` (LangGraph path) and
return a shim ``InvestigationReply`` built from the outcome.
When the flag is ``False``, it must call the legacy
``investigation.investigate_alert`` (Pydantic Graph path).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sentinel.domain.approval import entities as approval_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.interfaces.chat import app as chat_app_mod
from sentinel.interfaces.graphs import common as common_mod
from sentinel.interfaces.workflows import sre_investigation as workflows_sre_investigation_mod


def _make_fake_outcome() -> workflows_sre_investigation_mod.InvestigationOutcome:
    """Return a minimal InvestigationOutcome for use in tests."""
    confidence = confidence_entities.ConfidenceScore.from_factors(
        source_count=4,
        max_expected_sources=5,
        relevance=0.85,
        recency=0.8,
    )
    return workflows_sre_investigation_mod.InvestigationOutcome(
        request_id=uuid.uuid4(),
        classification_category="infrastructure",
        root_cause="Node memory pressure caused OOMKill",
        remediation="Increase memory limits",
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
            chat_app_mod.settings,
            "langgraph_sre_enabled",
            True,
        )

        fake_outcome = _make_fake_outcome()
        mock_wf_investigate = AsyncMock(return_value=fake_outcome)
        mock_build_graph = MagicMock(return_value=MagicMock())

        monkeypatch.setattr(
            chat_app_mod.workflows_sre_investigation,
            "investigate_alert",
            mock_wf_investigate,
        )
        monkeypatch.setattr(
            chat_app_mod.workflows_sre_investigation,
            "build_sre_investigation_graph",
            mock_build_graph,
        )

        mock_cfg = MagicMock()
        mock_cfg.build_mcp_toolsets = MagicMock(return_value=())
        mock_cfg.build_k8s_investigation_adapter = MagicMock(return_value=None)

        with (
            patch("sentinel.interfaces.chat.app.config_mod.get_config", return_value=mock_cfg),
            patch("sentinel.interfaces.chat.app.st") as mock_st,
        ):
            mock_st.session_state = {"k8s_backend": "Disabled"}

            # When _run_sre is called
            result = await chat_app_mod._run_sre(
                "pod is crashlooping",
                on_status=lambda _msg: None,
            )

        # Then the LangGraph workflow investigate_alert was called
        mock_wf_investigate.assert_awaited_once()
        call_kwargs = mock_wf_investigate.call_args.kwargs
        assert "alert" in call_kwargs
        assert "envelope" in call_kwargs
        assert "graph" in call_kwargs

        # And the result is a shim InvestigationReply with mapped fields
        assert isinstance(result, common_mod.InvestigationReply)
        assert result.root_cause == fake_outcome.root_cause
        assert result.remediation == fake_outcome.remediation

    @pytest.mark.asyncio
    async def test_uses_legacy_when_flag_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given the langgraph_sre_enabled flag is False
        monkeypatch.setattr(
            chat_app_mod.settings,
            "langgraph_sre_enabled",
            False,
        )

        fake_reply = common_mod.InvestigationReply(
            alert_id="chat-12345",
            root_cause="Memory leak in pod",
            remediation="Restart and patch",
            findings_summary="Found leak in payments service",
        )
        mock_legacy_investigate = AsyncMock(return_value=fake_reply)

        monkeypatch.setattr(
            chat_app_mod.investigation,
            "investigate_alert",
            mock_legacy_investigate,
        )

        mock_cfg = MagicMock()
        mock_cfg.agent_for = MagicMock(return_value=MagicMock())
        mock_cfg.build_mcp_toolsets = MagicMock(return_value=())
        mock_cfg.build_k8s_investigation_adapter = MagicMock(return_value=None)
        mock_cfg.build_challenger_adapter = MagicMock(return_value=None)

        with (
            patch("sentinel.interfaces.chat.app.config_mod.get_config", return_value=mock_cfg),
            patch("sentinel.interfaces.chat.app.st") as mock_st,
        ):
            mock_st.session_state = {"k8s_backend": "Disabled"}

            # When _run_sre is called
            result = await chat_app_mod._run_sre(
                "pod is crashlooping",
                on_status=lambda _msg: None,
            )

        # Then the legacy investigation.investigate_alert was called
        mock_legacy_investigate.assert_awaited_once()
        call_kwargs = mock_legacy_investigate.call_args.kwargs
        assert "alert" in call_kwargs
        assert "envelope" in call_kwargs
        assert "agent_for" in call_kwargs

        # And the original InvestigationReply is returned unchanged
        assert result is fake_reply
