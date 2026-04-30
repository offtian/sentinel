"""
T37 — Integration test: SRE happy-path workflow.

webhook → classify → match_runbook → investigate → analyse_root_cause
         → determine_confidence (high) → publish_findings → END

Uses ``InMemorySaver`` with ``allowed_msgpack_modules=True`` as the
checkpointer (no Postgres required; compatible with ``just test-integration``
without a running DB). The permissive serializer is needed because
``Envelope`` and the domain entity types are attrs frozen classes that
``ormsgpack`` cannot serialize with the default strict allowlist.

Monkeypatches ``get_config`` on the ``sre_investigation`` module so the
agents return deterministic high-confidence output and no real LLM calls
are made. The Slack ``post_investigation_summary`` vendor call is also
patched to an async no-op.

Asserts:
- ``InvestigationOutcome.findings_published`` is ``True``
- ``InvestigationOutcome.root_cause`` is non-empty
- ``InvestigationOutcome.approval_decision`` is ``None`` (no approval gate
  reached — high confidence skips the interrupt)
- ``InvestigationOutcome.interrupt_payload`` is ``None``
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any
from unittest import mock

import pytest
from langgraph.checkpoint import memory as lg_memory
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from sentinel.domain.confidence import entities as confidence_entities
from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    investigator,
    root_cause_analyser,
)
from sentinel.interfaces.workflows import sre_investigation as sre_mod
from sentinel.vendors import slack as slack_mod
from tests import factories


# ---------------------------------------------------------------------------
# Fake agent doubles — mirrors tests/functional/conftest.py patterns
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _FakeUsage:
    """Stub usage data with zero tokens for test doubles."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclasses.dataclass(frozen=True)
class _FakeResult[T]:
    """Generic fake PydanticAI result wrapping a typed output."""

    output: T

    def usage(self) -> _FakeUsage:
        """Return zero-token usage."""
        return _FakeUsage()

    def all_messages(self) -> list:
        """Return empty message list."""
        return []


async def _fake_classify_alert_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return a deterministic high-severity infrastructure classification."""
    return _FakeResult(
        alert_classifier.AlertClassification(
            severity="high",
            affected_service="api-service",
            category="infrastructure",
            summary="Pod OOMKilled causing 5xx errors",
            requires_immediate_action=True,
        )
    )


async def _fake_investigator_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return populated investigation findings to satisfy the evidence floor."""
    return _FakeResult(
        investigator.InvestigationFindings(
            summary="Memory usage at 2.1Gi/2Gi limit before OOMKill",
            sources_queried=["datadog_metrics", "kubernetes_events"],
            tool_calls=[
                investigator.ToolCallRecord(
                    tool="get_pod_metrics",
                    query="api-service pod memory",
                    result_kind="data",
                ),
            ],
        )
    )


async def _fake_root_cause_analyser_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return a high-confidence root cause analysis."""
    return _FakeResult(
        root_cause_analyser.RootCauseAnalysis(
            root_cause="Memory leak in request handler caused OOMKill",
            confidence=0.92,
            evidence=[
                "Memory ramp from 1.2Gi to 2.1Gi over 10 minutes",
                "Pod OOMKilled at 14:30 UTC",
            ],
            remediation_steps=[
                "Increase memory limit to 4Gi",
                "Deploy memory-leak fix in handler",
            ],
            affected_services=["api-service"],
            timeline="14:20 memory ramp → 14:30 OOMKill → 14:32 5xx spike",
        )
    )


def _make_fake_agent(fake_run: Any) -> mock.MagicMock:
    """Return a mock agent whose ``.run`` is the supplied async callable."""
    agent = mock.MagicMock()
    agent.run = fake_run
    return agent


def _build_fake_config(*, require_approval_below_confidence: float = 0.0) -> mock.MagicMock:
    """
    Return a mock config whose ``agent_for()`` returns deterministic fakes.

    Sets ``require_approval_below_confidence`` to the supplied threshold
    (default ``0.0`` so high-confidence results never trigger the approval gate).

    Sets ``post_to_slack`` to ``False``, ``k8s_adapter`` to ``None``, and
    ``pagerduty_client`` to ``None`` to prevent any external I/O. Sets
    ``investigator_toolsets`` and ``analyser_toolsets`` to empty tuples so
    ``list(getattr(config, ...))`` iterates cleanly rather than returning a
    ``MagicMock``.
    """
    cfg = mock.MagicMock()
    cfg.require_approval_below_confidence = require_approval_below_confidence
    cfg.post_to_slack = False
    cfg.runbooks = None
    cfg.k8s_adapter = None
    cfg.pagerduty_client = None
    cfg.investigator_toolsets = ()
    cfg.analyser_toolsets = ()
    agents: dict[str, Any] = {
        "alert_classifier": _make_fake_agent(_fake_classify_alert_run),
        "investigator": _make_fake_agent(_fake_investigator_run),
        "root_cause_analyser": _make_fake_agent(_fake_root_cause_analyser_run),
    }
    cfg.agent_for = mock.MagicMock(side_effect=lambda name: agents.get(name, mock.MagicMock()))
    return cfg


def _make_memory_saver() -> lg_memory.InMemorySaver:
    """
    Return an ``InMemorySaver`` configured with pickle fallback.

    The default ``ormsgpack`` serializer cannot encode attrs frozen classes
    (``Envelope``, ``Alert``, domain entities) because they have no
    ``model_dump`` / ``_asdict`` method and attrs does not register a
    dataclass interface. ``pickle_fallback=True`` instructs ``JsonPlusSerializer``
    to fall back to ``pickle`` whenever ``ormsgpack`` raises
    ``MsgpackEncodeError`` on an unsupported type.

    Note: in production the ``AsyncPostgresSaver`` uses Postgres JSONB storage
    and the domain objects are serialized via their Pydantic/attrs representation.
    The pickle fallback is intentionally test-only and must NOT be used in
    production checkpointers.
    """
    return lg_memory.InMemorySaver(
        serde=JsonPlusSerializer(
            pickle_fallback=True,
            allowed_msgpack_modules=True,
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def _fake_match_runbook_no_approval(
    state: Any,
) -> dict[str, Any]:
    """
    Stub ``match_runbook`` node that skips the real catalog lookup and
    signals a matched runbook so ``requires_approval`` is ``False``.

    This isolates happy-path tests from runbook-catalog plumbing and ensures
    ``determine_confidence`` sees ``requires_approval=False``, so the
    only gate is the confidence threshold.
    """
    return {
        "runbook": None,
        "runbook_match": None,
        "runbook_match_id": None,
        "requires_approval": False,
    }


class TestSreWorkflowHappyPath:
    @pytest.mark.asyncio
    async def test_high_confidence_run_publishes_findings_without_approval(self) -> None:
        """
        Full happy-path: high-confidence result bypasses the approval gate.

        ``InMemorySaver`` with pickle fallback replaces ``AsyncPostgresSaver``
        — no DB required.

        ``match_runbook`` is stubbed to return ``requires_approval=False`` so
        the only gate is the confidence threshold. ``require_approval_below_confidence``
        is set to ``0.0`` so the High-confidence run always goes straight to
        ``publish_findings``.
        """
        # Given an in-memory checkpointer and test fixtures
        checkpointer = _make_memory_saver()
        alert = factories.make_alert(
            alert_id="happy-path-001",
            title="api-service OOMKilled",
            description="Pod restarted due to OOMKill; memory limit 2Gi exceeded",
        )
        envelope = factories.make_envelope(request_id=uuid.uuid4())
        fake_config = _build_fake_config(require_approval_below_confidence=0.0)

        # When the investigation workflow runs with fake agents and no Slack I/O
        # and match_runbook patched to return requires_approval=False.
        # The graph is built INSIDE the patch context so with_envelope(match_runbook)
        # wraps the stub rather than the real function.
        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=checkpointer)
            outcome = await sre_mod.investigate_alert(
                alert=alert,
                envelope=envelope,
                graph=graph,
            )

        # Then findings were published (workflow reached END via publish_findings)
        assert outcome.findings_published is True

        # And a root cause was captured
        assert outcome.root_cause is not None
        assert len(outcome.root_cause) > 0

        # And no approval gate was hit (high confidence bypasses wait_for_human)
        assert outcome.approval_decision is None
        assert outcome.interrupt_payload is None

    @pytest.mark.asyncio
    async def test_outcome_carries_classification_category(self) -> None:
        """
        The classification_category field is populated from the classifier agent output.
        """
        # Given fixtures for a fresh run
        checkpointer = _make_memory_saver()
        alert = factories.make_alert(alert_id="category-check-001")
        envelope = factories.make_envelope(request_id=uuid.uuid4())
        fake_config = _build_fake_config(require_approval_below_confidence=0.0)

        # When the workflow runs with match_runbook stubbed and graph built inside patch
        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=checkpointer)
            outcome = await sre_mod.investigate_alert(
                alert=alert,
                envelope=envelope,
                graph=graph,
            )

        # Then the outcome exposes the category string from the alert classifier
        assert outcome.classification_category == "infrastructure"

    @pytest.mark.asyncio
    async def test_outcome_carries_confidence_score(self) -> None:
        """
        The confidence field is a populated ConfidenceScore with a non-zero total.
        """
        # Given fixtures with threshold 0.0 so the high-confidence run doesn't interrupt
        checkpointer = _make_memory_saver()
        alert = factories.make_alert(alert_id="confidence-check-001")
        envelope = factories.make_envelope(request_id=uuid.uuid4())
        fake_config = _build_fake_config(require_approval_below_confidence=0.0)

        # When the workflow runs with graph built inside the patch context
        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=checkpointer)
            outcome = await sre_mod.investigate_alert(
                alert=alert,
                envelope=envelope,
                graph=graph,
            )

        # Then a confidence score was computed and populated
        assert outcome.confidence is not None
        assert isinstance(outcome.confidence, confidence_entities.ConfidenceScore)
        assert outcome.confidence.total > 0.0
