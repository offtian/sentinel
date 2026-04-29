"""
Unit tests for _run_sre_investigation flag-aware routing (T32).

Covers three routing scenarios:
1. LangGraph path when flag enabled and graph initialised.
2. Legacy Pydantic Graph path when flag disabled.
3. Legacy Pydantic Graph path when flag enabled but graph not initialised.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from sentinel import worker as worker_mod
from sentinel.interfaces.workflows import sre_investigation as workflows_sre_investigation


# ---------------------------------------------------------------------------
# Helpers — build minimal fake objects the function under test touches
# ---------------------------------------------------------------------------


def _make_alert_payload() -> dict[str, object]:
    """Return a minimal alert payload dict that Alert.model_validate accepts."""
    return {
        "id": "P123ABC",
        "source": "pagerduty",
        "title": "High CPU on web-01",
        "description": "CPU > 90%",
        "severity": "high",
        "service": "api-service",
        "triggered_at": "2024-01-01T00:00:00Z",
        "raw_payload": {},
    }


def _make_fake_langgraph_outcome() -> workflows_sre_investigation.InvestigationOutcome:
    """Return a minimal InvestigationOutcome for the LangGraph path stub."""
    from sentinel.domain.confidence import entities as confidence_entities

    return workflows_sre_investigation.InvestigationOutcome(
        request_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        classification_category="performance",
        root_cause="OOM killer hit container",
        remediation="Increase memory limit",
        confidence=confidence_entities.ConfidenceScore.from_total(0.85),
        needs_approval=False,
        findings_published=True,
        interrupt_payload=None,
        approval_decision=None,
    )


def _make_fake_legacy_result() -> MagicMock:
    """Return a Pydantic-model-like object that model_dump_json returns JSON."""
    fake_result = MagicMock()
    fake_result.model_dump_json.return_value = json.dumps(
        {
            "alert_id": "P123ABC",
            "root_cause": "OOM killer",
            "remediation": "Increase limit",
        }
    )
    return fake_result


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestRunSreInvestigationRouting:
    """Tests for the flag-aware dispatch inside _run_sre_investigation."""

    @pytest.mark.asyncio
    async def test_routes_to_langgraph_when_flag_enabled_and_graph_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        When langgraph_sre_enabled is True and the graph is initialised,
        the LangGraph investigate_alert path is taken.
        """
        # Given the LangGraph feature flag is enabled
        monkeypatch.setattr(worker_mod.settings, "langgraph_sre_enabled", True)

        # Given the module-level graph is set (as _main would do at startup)
        fake_graph = MagicMock()
        monkeypatch.setattr(worker_mod, "_sre_investigation_graph", fake_graph)

        # Given the LangGraph investigate_alert returns a fake outcome
        fake_outcome = _make_fake_langgraph_outcome()
        lg_investigate_mock = AsyncMock(return_value=fake_outcome)
        monkeypatch.setattr(
            workflows_sre_investigation,
            "investigate_alert",
            lg_investigate_mock,
        )

        # Given legacy pipeline dependencies are mocked out
        _patch_heavy_dependencies(monkeypatch)

        # When _run_sre_investigation is called
        payload = _make_alert_payload()
        result_json = await worker_mod._run_sre_investigation(payload)

        # Then the LangGraph entrypoint was called with the graph
        lg_investigate_mock.assert_called_once()
        call_kwargs = lg_investigate_mock.call_args.kwargs
        assert call_kwargs["graph"] is fake_graph

        # Then the result is valid JSON containing the outcome fields
        result = json.loads(result_json)
        assert result["classification_category"] == "performance"
        assert result["root_cause"] == "OOM killer hit container"
        assert result["findings_published"] is True

    @pytest.mark.asyncio
    async def test_routes_to_legacy_when_flag_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        When langgraph_sre_enabled is False, the legacy Pydantic Graph
        investigation path is taken regardless of graph state.
        """
        # Given the feature flag is disabled
        monkeypatch.setattr(worker_mod.settings, "langgraph_sre_enabled", False)

        # Given the LangGraph graph slot is also uninitialised (shouldn't matter)
        monkeypatch.setattr(worker_mod, "_sre_investigation_graph", None)

        # Given the legacy investigation.investigate_alert returns a fake result
        fake_legacy_result = _make_fake_legacy_result()
        legacy_investigate_mock = AsyncMock(return_value=fake_legacy_result)

        from sentinel.interfaces.graphs import investigation as investigation_mod

        monkeypatch.setattr(
            investigation_mod,
            "investigate_alert",
            legacy_investigate_mock,
        )

        # Given a mocked LangGraph path that should NOT be called
        lg_investigate_mock = AsyncMock()
        monkeypatch.setattr(
            workflows_sre_investigation,
            "investigate_alert",
            lg_investigate_mock,
        )

        # Given heavy dependencies mocked
        _patch_heavy_dependencies(monkeypatch)

        # When _run_sre_investigation is called
        payload = _make_alert_payload()
        await worker_mod._run_sre_investigation(payload)

        # Then only the legacy path was called
        legacy_investigate_mock.assert_called_once()
        lg_investigate_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_routes_to_legacy_when_flag_enabled_but_graph_not_initialised(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        When langgraph_sre_enabled is True but the graph is None (startup
        did not initialise it), the legacy path is taken as a safe fallback.
        """
        # Given the feature flag is enabled
        monkeypatch.setattr(worker_mod.settings, "langgraph_sre_enabled", True)

        # Given the graph was not initialised (e.g. DB not configured)
        monkeypatch.setattr(worker_mod, "_sre_investigation_graph", None)

        # Given the legacy investigation.investigate_alert is mocked
        fake_legacy_result = _make_fake_legacy_result()
        legacy_investigate_mock = AsyncMock(return_value=fake_legacy_result)

        from sentinel.interfaces.graphs import investigation as investigation_mod

        monkeypatch.setattr(
            investigation_mod,
            "investigate_alert",
            legacy_investigate_mock,
        )

        # Given a LangGraph mock that must not be called
        lg_investigate_mock = AsyncMock()
        monkeypatch.setattr(
            workflows_sre_investigation,
            "investigate_alert",
            lg_investigate_mock,
        )

        # Given heavy dependencies mocked
        _patch_heavy_dependencies(monkeypatch)

        # When _run_sre_investigation is called
        payload = _make_alert_payload()
        await worker_mod._run_sre_investigation(payload)

        # Then the legacy path was taken (safe fallback)
        legacy_investigate_mock.assert_called_once()
        lg_investigate_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Shared patch helper
# ---------------------------------------------------------------------------


def _patch_heavy_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Patch all heavy infrastructure dependencies in _run_sre_investigation
    so tests run without a real DB, LLM, or vendor SDK.
    """
    # Patch config.get_config() to return a lightweight stub
    fake_cfg = MagicMock()
    fake_cfg.pagerduty_client = None
    fake_cfg.build_k8s_investigation_adapter.return_value = None
    fake_cfg.build_challenger_adapter.return_value = None
    fake_cfg.build_mcp_toolsets.return_value = []
    fake_cfg.build_observability_toolset.return_value = None
    fake_cfg.agent_for.return_value = MagicMock()

    from sentinel import config as config_mod

    monkeypatch.setattr(config_mod, "get_config", MagicMock(return_value=fake_cfg))

    # Patch settings.pagerduty_api_key so no PD client is constructed
    monkeypatch.setattr(worker_mod.settings, "pagerduty_api_key", "")

    # Patch _get_optional_db to return None (no DB in unit tests)
    monkeypatch.setattr(worker_mod, "_get_optional_db", MagicMock(return_value=None))

    # Patch ExecutionTracer so start/complete_pipeline don't hit DB
    fake_tracer = MagicMock()
    fake_tracer.start_pipeline = AsyncMock()
    fake_tracer.complete_pipeline = AsyncMock()
    fake_tracer.trace_id = uuid.uuid4()

    from sentinel.domain.pipeline import tracer as pipeline_tracer_mod

    monkeypatch.setattr(
        pipeline_tracer_mod,
        "ExecutionTracer",
        MagicMock(return_value=fake_tracer),
    )

    # Patch prompt loading so no file I/O occurs
    fake_template = MagicMock()
    fake_template.version = "v1"
    fake_template.sha256 = "abc123"
    fake_template.system_text = "system prompt"

    from sentinel.domain import prompts as prompts_mod

    monkeypatch.setattr(
        prompts_mod,
        "load_template",
        MagicMock(return_value=fake_template),
    )

    # Patch canonical_input_hash so no hashing needed
    from sentinel.domain.pipeline import queries as pipeline_queries_mod

    monkeypatch.setattr(
        pipeline_queries_mod,
        "canonical_input_hash",
        MagicMock(return_value="fakehash"),
    )

    # Patch wrap_for_runbook_scope to pass through None (no toolsets in tests)
    from sentinel.plugins.toolsets import _runbook_scope as scope_mod

    monkeypatch.setattr(
        scope_mod,
        "wrap_for_runbook_scope",
        MagicMock(return_value=None),
    )

    # Patch audit_ops.record_audit_entry so no DB call is needed
    from sentinel.domain.audit import operations as audit_ops_mod

    monkeypatch.setattr(
        audit_ops_mod,
        "record_audit_entry",
        AsyncMock(),
    )
