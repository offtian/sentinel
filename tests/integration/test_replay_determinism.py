"""
F4.8 / F8.6 — 30-run replay determinism for the SRE investigation pipeline.

Captures a synthetic crashloop bundle once via :class:`CapturingModel`
plumbed through the real ``investigate_alert`` LangGraph entry-point (with a
deterministic :class:`pydantic_ai.models.test.TestModel` behind every agent),
then replays the same bundle 30 times against a fresh
:class:`~sentinel.plugins.models.recorded.RecordedModel` per iteration
and asserts every replayed output is bit-identical to the first replay.

Updated for F8: uses the LangGraph workflow API
(``sre_investigation.investigate_alert(alert=, envelope=, graph=)``)
with an in-memory checkpointer and a patched ``get_config`` singleton,
replacing the pre-LangGraph Pydantic Graph API that was removed when the
SRE pipeline migrated to LangGraph in PR #35.

This guards against three regression classes the F4 phase B contract
must hold:

1. ``RecordedModel`` / ``RecordedToolset`` queue iterator state leaking
   between runs.
2. ``to_canonical_json`` producing different output for identical input
   (canonicalisation drift).
3. SRE pipeline-level non-determinism (graph nodes that depend on hash
   randomisation, dict ordering, asyncio race, etc.).

Marked ``slow`` because it spins the SRE graph 31 times. Wired into
``just test-integration`` via the default ``tests/integration/`` glob.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest import mock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic_ai.models import test as pydantic_ai_test_model

from sentinel.domain.alerts import entities as alert_entities
from sentinel.interfaces.graphs.agents import alert_classifier, investigator, root_cause_analyser
from sentinel.interfaces.workflows import sre_investigation as sre_mod
from sentinel.plugins.models import capturing as capturing_mod
from sentinel.plugins.models import recorded as recorded_model_mod
from sentinel.plugins.toolsets import _runtime as runtime_mod
from sentinel.plugins.toolsets import recorded as recorded_toolset_mod
from sentinel.utils import replay_bundle as bundle_mod
from tests import factories


_REPLAY_RUNS = 30
_SERDE = JsonPlusSerializer(pickle_fallback=True)


def _build_crashloop_alert() -> alert_entities.Alert:
    """Return the synthetic crashloop alert used as the bundle's input."""
    return factories.make_alert(
        alert_id="P-CRASHLOOP-001",
        title="api-service crashloop",
        description="api-service pod restart count exceeded threshold (12 in 5m).",
        severity=alert_entities.AlertSeverity.HIGH,
        service="api-service",
    )


def _build_real_agents() -> dict[str, Any]:
    """
    Construct the three SRE agents used by the investigation graph.

    Each agent gets a deterministic :class:`TestModel` whose
    ``custom_output_args`` is pinned to a schema-conforming dict so the
    pipeline's downstream enum coercion (``AlertSeverity(...)``) and
    structured fields don't trip on TestModel's default ``"a"`` filler.
    """
    classifier = alert_classifier.build_agent()
    analyser = root_cause_analyser.build_agent()
    inv_agent = investigator.build_agent()
    classifier.model = pydantic_ai_test_model.TestModel(
        custom_output_args={
            "severity": "high",
            "affected_service": "api-service",
            "category": "infrastructure",
            "summary": "Crashloop in api-service",
            "requires_immediate_action": True,
        },
    )
    analyser.model = pydantic_ai_test_model.TestModel(
        custom_output_args={
            "root_cause": "OOMKilled — memory limit too low for load",
            "confidence": 0.85,
            "evidence": ["pod restart count 12", "memory usage spiked to 950Mi"],
            "remediation_steps": ["raise memory limit", "investigate leak"],
            "affected_services": ["api-service"],
            "timeline": "started 2026-04-26 16:00 UTC",
        },
    )
    inv_agent.model = pydantic_ai_test_model.TestModel(
        custom_output_args={
            "summary": "",
            "sources_queried": [],
            "tool_calls": [],
        },
    )
    return {
        "alert_classifier": classifier,
        "root_cause_analyser": analyser,
        "investigator": inv_agent,
    }


def _wrap_with_capturing(agents: dict[str, Any]) -> None:
    """Replace each agent's model with a :class:`CapturingModel` over the same wrapped model."""
    for name, agent in agents.items():
        agent.model = capturing_mod.CapturingModel(wrapped=agent.model, agent_name=name)


def _swap_in_recorded(
    *, agents: dict[str, Any], bundle: bundle_mod.ReplayBundle
) -> recorded_model_mod.RecordedModel:
    """
    Swap every agent's model to a fresh shared :class:`RecordedModel`.

    A fresh instance per iteration is mandatory — RecordedModel's queue
    is consumed on each ``.request()`` call.
    """
    recorded_model = recorded_model_mod.RecordedModel(bundle.llm_io)
    for agent in agents.values():
        agent.model = recorded_model
    return recorded_model


def _build_fake_config(agents: dict[str, Any]) -> mock.MagicMock:
    """Build a minimal config mock that serves the given agents."""
    cfg = mock.MagicMock()
    cfg.agent_for = mock.MagicMock(side_effect=lambda name: agents.get(name, mock.MagicMock()))
    cfg.runbooks = None
    cfg.db_session_factory = None
    cfg.k8s_adapter = None
    cfg.require_approval_below_confidence = 0.7
    cfg.post_to_slack = False
    cfg.investigator_toolsets = ()
    cfg.analyser_toolsets = ()
    return cfg


def _outcome_to_dict(outcome: sre_mod.InvestigationOutcome) -> dict[str, Any]:
    """Serialise an ``InvestigationOutcome`` to a JSON-stable dict for comparison."""
    confidence = outcome.confidence
    return {
        "classification_category": outcome.classification_category,
        "root_cause": outcome.root_cause,
        "remediation": outcome.remediation,
        "confidence_total": confidence.total if confidence else None,
        "confidence_label": confidence.label.value if confidence else None,
        "needs_approval": outcome.needs_approval,
        "findings_published": outcome.findings_published,
        "approval_decision": (
            outcome.approval_decision.value if outcome.approval_decision else None
        ),
    }


async def _run_pipeline(
    *,
    alert: alert_entities.Alert,
    agents: dict[str, Any],
    recorded_toolset: recorded_toolset_mod.RecordedToolset | None = None,
) -> dict[str, Any]:
    """
    Drive the SRE investigation graph against *agents* and return its output dict.

    Uses a fresh :class:`MemorySaver` checkpointer per call so state never
    bleeds between capture and replay iterations.
    """
    cfg = _build_fake_config(agents)
    if recorded_toolset is not None:
        cfg.investigator_toolsets = (recorded_toolset,)
        cfg.analyser_toolsets = (recorded_toolset,)

    graph = sre_mod.build_sre_investigation_graph(checkpointer=MemorySaver(serde=_SERDE))
    envelope = factories.make_envelope()

    with mock.patch.object(sre_mod, "get_config", return_value=cfg):
        outcome = await sre_mod.investigate_alert(alert=alert, envelope=envelope, graph=graph)

    return _outcome_to_dict(outcome)


@pytest.mark.asyncio
@pytest.mark.slow
async def test_replay_is_deterministic_across_30_runs() -> None:
    """
    Test that replaying a captured bundle 30 times yields identical outputs.

    Catches iterator-state leaks, canonicalisation drift, and pipeline-
    level non-determinism in one assertion sweep.
    """
    # Given a synthetic crashloop alert and the three SRE agents wrapped for capture
    alert = _build_crashloop_alert()
    agents = _build_real_agents()
    _wrap_with_capturing(agents)

    # And given a freshly bound replay-bundle builder
    builder = bundle_mod.ReplayBundleBuilder()
    capture_token = runtime_mod.bind_replay_builder(builder)
    try:
        captured_output = await _run_pipeline(alert=alert, agents=agents)
    finally:
        runtime_mod.unbind_replay_builder(capture_token)

    # When the captured bundle is materialised
    captured_bundle = builder.build(
        envelope=factories.make_envelope(),
        alert_payload=alert.model_dump(mode="json"),
        runbook_id=None,
        runbook_version_sha=None,
        final_outputs=captured_output,
    )

    # Then the bundle has at least the SRE agent calls (classifier + analyser)
    assert len(captured_bundle.llm_io) >= 2, (
        "expected at least two LLM entries (classifier + analyser) in the captured bundle"
    )

    # When the bundle is replayed 30 times, each iteration with a fresh RecordedModel
    replay_outputs: list[dict[str, Any]] = []
    for _ in range(_REPLAY_RUNS):
        _swap_in_recorded(agents=agents, bundle=captured_bundle)
        recorded_toolset = recorded_toolset_mod.RecordedToolset(captured_bundle.tool_io)
        replay_outputs.append(
            await _run_pipeline(alert=alert, agents=agents, recorded_toolset=recorded_toolset)
        )

    # Then every replay output equals the captured output bit-for-bit
    canonical_first = json.dumps(replay_outputs[0], sort_keys=True, default=str)
    canonical_captured = json.dumps(captured_output, sort_keys=True, default=str)
    assert canonical_first == canonical_captured, (
        "first replay diverged from captured output — replay path drifted from capture"
    )
    for index, replay_output in enumerate(replay_outputs[1:], start=2):
        canonical_iteration = json.dumps(replay_output, sort_keys=True, default=str)
        assert canonical_iteration == canonical_first, (
            f"replay iteration {index} diverged from iteration 1 — non-determinism in the replay loop"
        )


@pytest.mark.asyncio
@pytest.mark.slow
async def test_bundle_sha_is_stable_across_repeated_serialisation() -> None:
    """
    Test that ``bundle_sha`` is stable for an identical bundle across recomputes.

    A fast canonicalisation-only check that runs alongside the heavier
    pipeline determinism test. Catches drift in
    :func:`to_canonical_json` (sort order, default coercion, separators)
    that the pipeline test would only surface as a confusing
    ``RecordedReplayMismatchError``.
    """
    # Given a synthetic bundle with both LLM and tool I/O entries
    envelope = factories.make_envelope()
    captured_at = datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC)
    bundle = bundle_mod.ReplayBundle(
        envelope=envelope,
        alert_payload={"alert_id": "P-CRASHLOOP-001", "service": "api-service"},
        runbook_id="rb-k8s-crashloop",
        runbook_version_sha="rb-sha-deadbeef",
        tool_io=(
            bundle_mod.ToolIOEntry(
                tool_name="query_logs",
                inputs={"service": "api-service", "limit": 50},
                outputs=[{"line": "ERROR pod restart"}],
                at=captured_at,
            ),
        ),
        llm_io=(
            bundle_mod.LLMIOEntry(
                agent_name="alert_classifier",
                model_id="stub-model",
                inputs={"messages": []},
                outputs={"role": "assistant", "content": "test"},
                at=captured_at,
            ),
        ),
        final_outputs={"alert_id": "P-CRASHLOOP-001", "root_cause": "crashloop"},
    )

    # When the canonical sha is computed three times and a fresh equivalent
    # bundle's sha is computed once
    shas = [bundle.bundle_sha for _ in range(3)]
    fresh_bundle = bundle_mod.ReplayBundle(
        envelope=envelope,
        alert_payload={"alert_id": "P-CRASHLOOP-001", "service": "api-service"},
        runbook_id="rb-k8s-crashloop",
        runbook_version_sha="rb-sha-deadbeef",
        tool_io=bundle.tool_io,
        llm_io=bundle.llm_io,
        final_outputs={"alert_id": "P-CRASHLOOP-001", "root_cause": "crashloop"},
    )
    fresh_sha = fresh_bundle.bundle_sha

    # Then all four shas agree
    assert len(set(shas)) == 1, "bundle_sha drifted across recomputes on the same instance"
    assert fresh_sha == shas[0], (
        "bundle_sha differs between two instances built from identical inputs"
    )

    # And changing any field flips the sha
    drifted_bundle = bundle_mod.ReplayBundle(
        envelope=envelope,
        alert_payload={"alert_id": "P-CRASHLOOP-001", "service": "api-service"},
        runbook_id="rb-k8s-crashloop",
        runbook_version_sha="rb-sha-deadbeef",
        tool_io=bundle.tool_io,
        llm_io=bundle.llm_io,
        final_outputs={
            "alert_id": "P-CRASHLOOP-001",
            "root_cause": "crashloop",
            "_drift": str(uuid.uuid4()),
        },
    )
    assert drifted_bundle.bundle_sha != shas[0], (
        "field-level drift did not change bundle_sha — canonicalisation is too loose"
    )
