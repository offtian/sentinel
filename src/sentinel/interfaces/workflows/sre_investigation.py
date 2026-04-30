"""
LangGraph SRE investigation workflow.

Seven async node functions plus routing helpers and entrypoints compose
the SRE investigation pipeline. This is the LangGraph-native counterpart
to the legacy Pydantic Graph implementation in
``interfaces/graphs/investigation.py``; only the orchestration glue
changes -- PydanticAI agent factories, the F2 envelope, and the domain
entities cross over unchanged.

Differences from the legacy harness (intentional):

- ``Dependencies`` injection is replaced by ``get_config()`` at the top
  of each node body. Nodes pull agents and toolsets directly from the
  singleton.
- ``status_update_client``, ``persist_fn`` and ``trace_collector`` are
  dropped from the in-graph plumbing; LangGraph's runtime instrumentation
  and the entrypoint layer own those concerns.
- A new ``wait_for_human`` node uses ``interrupt()`` to pause the run
  when ``determine_confidence`` flags low confidence; the resume
  payload from ``Command(resume=...)`` is mapped to an
  ``ApprovalDecision`` enum.
- A ``publish_findings`` node executes *after* the approval gate (when
  approved), unlike the support workflow that ends at the gate.
- All seven nodes are wrapped in ``with_envelope`` at graph-build time
  so the F2 envelope binds OTel span attributes and structlog context
  for the duration of every node body.

Graph shape::

    START
      → classify_alert
      → match_runbook
      → investigate
      → analyse_root_cause
      → determine_confidence
      → conditional: needs_approval ? wait_for_human : publish_findings
                     wait_for_human → conditional: APPROVED ? publish_findings : END
                     publish_findings → END
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import attrs
from langgraph import graph as lg_graph
from langgraph import types as lg_types
from langgraph.graph import state as lg_state

from sentinel import config as config_mod
from sentinel.data.primitives import envelope as envelope_module
from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.approval import entities as approval_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.investigations import adapters as investigation_adapters
from sentinel.domain.investigations import entities as investigation_entities
from sentinel.domain.runbooks import alert_view as runbook_alert_view
from sentinel.domain.runbooks import matcher as runbook_matcher_mod
from sentinel.domain.runbooks import models as runbook_models
from sentinel.domain.runbooks import persistence as runbook_persistence
from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    investigator,
    root_cause_analyser,
    runbook_disambiguator,
)
from sentinel.interfaces.graphs.agents import utils as agent_utils
from sentinel.interfaces.workflows import _envelope as envelope_mod
from sentinel.interfaces.workflows import sre_state as sre_state_mod
from sentinel.utils import logs, metrics
from sentinel.vendors import slack as slack_mod


# Module-local aliases for test patchability per python.md's mocking
# guidance. ``mock.patch.object(sre_mod, "<name>")`` requires the symbol
# live as a module attribute; rebinding here keeps the
# import-modules-not-objects rule at the import site while letting tests
# inject doubles.
get_config = config_mod.get_config
interrupt = lg_types.interrupt

# F7: when no investigation occurred AND the agent made no data-returning
# tool calls, cap the analyser's self-reported relevance at this ceiling.
# Defends against confident hallucinations: an LLM that diagnoses on the
# alert title alone cannot push past Medium confidence and cannot bypass
# the approval gate.
_EVIDENCE_FLOOR_RELEVANCE_CAP = 0.3


# ---------------------------------------------------------------------------
# T17 — classify_alert
# ---------------------------------------------------------------------------


async def classify_alert(state: sre_state_mod.InvestigationState) -> dict[str, Any]:
    """
    Run the alert classifier agent and return classification + initialised investigation.

    Reads ``state["alert"]``, invokes the configured PydanticAI
    ``alert_classifier`` agent, updates the alert with severity and
    service from the classifier output, initialises the investigation
    entity, and returns a partial-state dict keyed by
    ``classification_category``, ``alert``, and ``investigation``.
    Agent failures propagate so LangGraph's runtime records the failed step.
    """
    config = get_config()
    alert = state["alert"]

    classifier_agent = config.agent_for("alert_classifier")
    agent_utils.set_agent_span_attributes(
        prompt_sha256=alert_classifier.PROMPT_SHA256,
        model_name=agent_utils.get_model_name(classifier_agent),
        agent_name="alert_classifier",
    )
    try:
        result = await classifier_agent.run(
            user_prompt=f"Alert: {alert.title}\n\n{alert.description}",
            deps=alert_classifier.Dependencies(
                alert_title=alert.title,
                alert_description=alert.description,
                alert_source=alert.source,
            ),
            model_settings=agent_utils.build_cache_settings(
                model_name=agent_utils.get_model_name(classifier_agent),
                prompt_sha256=alert_classifier.PROMPT_SHA256,
            ),
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"alert_id": alert.id, "node": "classify_alert"},
        )
        raise

    agent_utils.stamp_usage_attributes(
        result.usage(), model_name=agent_utils.get_model_name(classifier_agent)
    )
    classification: alert_classifier.AlertClassification = result.output

    logs.log_event(
        "alert_classified",
        params={
            "alert_id": alert.id,
            "severity": classification.severity,
            "category": classification.category,
            "service": classification.affected_service,
        },
    )

    updated_alert = alert.model_copy(
        update={
            "severity": alert_entities.AlertSeverity(classification.severity),
            "service": classification.affected_service,
        }
    )
    investigation = investigation_entities.Investigation(
        alert=updated_alert,
        status=investigation_entities.InvestigationStatus.INVESTIGATING,
        started_at=datetime.now(tz=UTC),
    )
    return {
        "classification_category": classification.category,
        "alert": updated_alert,
        "investigation": investigation,
    }


# ---------------------------------------------------------------------------
# T18 — match_runbook
# ---------------------------------------------------------------------------


async def match_runbook(state: sre_state_mod.InvestigationState) -> dict[str, Any]:
    """
    Match the classified alert against the runbook catalog.

    Reads ``state["alert"]`` and ``state["envelope"]``, calls
    :func:`runbook_matcher_mod.match_runbook` against the pre-loaded
    catalog supplied via ``config.runbooks``. Always writes a result
    including ``no_match`` outcomes.

    Soft-degrade contract: ``config.runbooks is None`` (no catalog wired)
    or ``getattr(config, "build_runbook_matcher", None) is None`` short-
    circuits to a no-match result so unit tests that don't exercise the
    catalog can omit it. Matcher/persistence failures also soft-degrade
    to no-match so the investigation continues under the generic frame.
    """
    config = get_config()
    alert = state["alert"]
    envelope = state["envelope"]
    investigation = state.get("investigation")

    # Access the runbook catalog from config. If the catalog attribute
    # doesn't exist or returns None, soft-degrade to no-match.
    runbooks = cast("dict[str, runbook_models.Runbook] | None", getattr(config, "runbooks", None))

    if runbooks is None:
        logs.log_event(
            "runbook_match_skipped",
            params={
                "alert_id": alert.id,
                "reason": "no_catalog_configured",
            },
        )
        return {
            "runbook": None,
            "runbook_match": None,
            "runbook_match_id": None,
            "requires_approval": True,
        }

    matchable_alert = runbook_alert_view.MatchableAlertView.from_alert(
        alert=alert,
        envelope=envelope,
    )

    async def _disambiguator(
        summary: str,
        candidates: tuple[tuple[str, str], ...],
    ) -> runbook_models.DisambiguatorChoice:
        return await runbook_disambiguator.disambiguate(
            alert_summary=summary,
            candidates=candidates,
            model=None,
        )

    try:
        match = await runbook_matcher_mod.match_runbook(
            alert=matchable_alert,
            envelope=envelope,
            runbooks=runbooks,
            disambiguator=_disambiguator,
            rag_fallback=None,
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"alert_id": alert.id, "node": "match_runbook"},
        )
        return {
            "runbook": None,
            "runbook_match": None,
            "runbook_match_id": None,
            "requires_approval": True,
        }

    matched: runbook_models.Runbook | None = (
        runbooks.get(match.matched_runbook_id) if match.matched_runbook_id is not None else None
    )
    requires_approval = matched is None

    # Persist the runbook_match audit row if a DB session factory is available.
    runbook_match_id: uuid.UUID | None = None
    db_session_factory = getattr(config, "db_session_factory", None)
    if db_session_factory is not None and investigation is not None:
        try:
            async with db_session_factory() as session:
                runbook_match_id = await runbook_persistence.write_runbook_match(
                    session=session,
                    envelope=envelope,
                    match=match,
                )
                await session.commit()
        except Exception as exc:
            logs.log_exception(
                exc,
                params={"alert_id": alert.id, "node": "match_runbook", "step": "persist"},
            )

    logs.log_event(
        "runbook_matched",
        params={
            "alert_id": alert.id,
            "runbook_id": match.matched_runbook_id,
            "match_method": match.match_method,
            "tag_score": match.tag_score,
            "requires_approval": requires_approval,
        },
    )
    return {
        "runbook": matched,
        "runbook_match": match,
        "runbook_match_id": runbook_match_id,
        "requires_approval": requires_approval,
    }


async def _merge_k8s_results(
    *,
    alert: alert_entities.Alert,
    k8s_adapter: Any,
    investigation_analysis: str,
    investigation_sources: list[str],
    tool_calls_with_data: int,
    tool_calls_total: int,
    investigation_status: str,
) -> tuple[str, list[str], int, int, str]:
    """
    Merge K8s adapter findings into the investigation context.

    Extracted from :func:`investigate` to keep that node under the
    50-statement complexity cap (PLR0915). Returns an updated tuple of
    ``(analysis, sources, tool_calls_with_data, tool_calls_total, status)``.
    On adapter failure, logs the exception and returns the inputs unchanged.
    """
    try:
        k8s_context = investigation_adapters.InvestigationContext(
            cluster_name=alert.raw_payload.get("cluster", "default"),
            namespace=alert.raw_payload.get("namespace"),
        )
        k8s_result = await k8s_adapter.investigate(alert=alert, context=k8s_context)
        k8s_analysis = "\n".join(f.summary for f in k8s_result.findings)
        if k8s_analysis:
            investigation_analysis = (
                (investigation_analysis + "\n\n--- Kubernetes cluster state ---\n" + k8s_analysis)
                if investigation_analysis
                else f"--- Kubernetes cluster state ---\n{k8s_analysis}"
            )
            investigation_sources.extend(k8s_result.sources_queried)
            tool_calls_with_data += len(k8s_result.findings)
            tool_calls_total += len(k8s_result.findings)
            if investigation_status in ("skipped", "empty"):
                investigation_status = "ran"
        logs.log_event(
            "k8s_investigation_merged",
            params={
                "alert_id": alert.id,
                "k8s_sources": list(k8s_result.sources_queried),
                "k8s_findings_count": len(k8s_result.findings),
            },
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"alert_id": alert.id, "node": "investigate", "k8s_adapter": "failed"},
        )
    return (
        investigation_analysis,
        investigation_sources,
        tool_calls_with_data,
        tool_calls_total,
        investigation_status,
    )


# ---------------------------------------------------------------------------
# T19 — investigate
# ---------------------------------------------------------------------------


async def investigate(state: sre_state_mod.InvestigationState) -> dict[str, Any]:
    """
    Run the Sentinel-native investigator agent to gather evidence.

    Reads ``state["alert"]``, ``state["runbook"]``, and
    ``state["envelope"]``; invokes the PydanticAI ``investigator`` agent;
    merges K8s adapter results if configured; updates the investigation
    entity with findings and returns a partial-state dict keyed by
    ``investigation``.

    Soft-degrade contract: if the investigator agent is not registered,
    the investigation continues with an empty analysis. If the agent
    raises, the investigation records a failure message so downstream
    nodes can still produce a low-confidence result.
    """
    config = get_config()
    alert = state["alert"]
    envelope = state["envelope"]
    runbook = state.get("runbook")
    investigation = state.get("investigation")

    try:
        investigator_agent = config.agent_for("investigator")
    except (KeyError, Exception) as exc:
        logs.log_event(
            "investigator_skipped",
            params={
                "alert_id": alert.id,
                "reason": f"agent_not_configured: {type(exc).__name__}",
            },
        )
        return {"investigation": investigation}

    agent_utils.set_agent_span_attributes(
        prompt_sha256=investigator.PROMPT_SHA256,
        model_name=agent_utils.get_model_name(investigator_agent),
        agent_name="investigator",
    )
    investigation_analysis = ""
    investigation_sources: list[str] = []
    investigation_tool_calls: list[dict[str, Any]] = []
    investigation_status = "skipped"
    tool_calls_with_data = 0
    tool_calls_total = 0

    try:
        result = await investigator_agent.run(
            user_prompt=f"Investigate alert: {alert.title}",
            deps=investigator.Dependencies(
                alert_title=alert.title,
                alert_description=alert.description,
                alert_severity=alert.severity.value,
                service=alert.service or "",
                cluster_name=str(alert.raw_payload.get("cluster", "")),
                namespace=str(alert.raw_payload.get("namespace", "")),
                runbook=runbook,
                envelope=envelope,
            ),
            toolsets=list(getattr(config, "investigator_toolsets", ())) or None,
            model_settings=agent_utils.build_cache_settings(
                model_name=agent_utils.get_model_name(investigator_agent),
                prompt_sha256=investigator.PROMPT_SHA256,
            ),
        )
        agent_utils.stamp_usage_attributes(
            result.usage(), model_name=agent_utils.get_model_name(investigator_agent)
        )
        findings_output: investigator.InvestigationFindings = result.output
        investigation_analysis = findings_output.summary
        investigation_sources = list(findings_output.sources_queried)
        investigation_tool_calls = [
            {"tool": tc.tool, "query": tc.query, "result_kind": tc.result_kind}
            for tc in findings_output.tool_calls
        ]
        tool_calls_total = len(investigation_tool_calls)
        tool_calls_with_data = sum(
            1 for tc in findings_output.tool_calls if tc.result_kind == "data"
        )
        investigation_status = (
            "ran"
            if (investigation_analysis or investigation_sources or tool_calls_with_data > 0)
            else "empty"
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"alert_id": alert.id, "node": "investigate"},
        )
        investigation_analysis = (
            "Investigation engine raised an exception — proceeding with alert context only."
        )
        investigation_status = "failed"

    # Merge K8s adapter results if configured.
    k8s_adapter = getattr(config, "k8s_adapter", None)
    if k8s_adapter is not None and getattr(k8s_adapter, "is_configured", False):
        (
            investigation_analysis,
            investigation_sources,
            tool_calls_with_data,
            tool_calls_total,
            investigation_status,
        ) = await _merge_k8s_results(
            alert=alert,
            k8s_adapter=k8s_adapter,
            investigation_analysis=investigation_analysis,
            investigation_sources=investigation_sources,
            tool_calls_with_data=tool_calls_with_data,
            tool_calls_total=tool_calls_total,
            investigation_status=investigation_status,
        )

    logs.log_event(
        "investigator_completed",
        params={
            "alert_id": alert.id,
            "sources_queried": investigation_sources,
            "tool_calls_count": tool_calls_total,
            "tool_calls_with_data": tool_calls_with_data,
            "investigation_status": investigation_status,
        },
    )

    # Update the investigation entity status.
    updated_investigation = investigation
    if investigation is not None:
        updated_investigation = investigation.model_copy(
            update={
                "status": investigation_entities.InvestigationStatus.INVESTIGATING,
            }
        )

    # Stash investigation context for downstream nodes (analyse_root_cause,
    # determine_confidence) using a side-channel dict key so the TypedDict
    # shape stays clean.
    return {
        "investigation": updated_investigation,
        "_investigation_context": {
            "analysis": investigation_analysis,
            "sources": investigation_sources,
            "tool_calls": investigation_tool_calls,
            "status": investigation_status,
            "tool_calls_with_data": tool_calls_with_data,
            "tool_calls_total": tool_calls_total,
        },
    }


# ---------------------------------------------------------------------------
# T20 — analyse_root_cause
# ---------------------------------------------------------------------------


async def analyse_root_cause(
    state: sre_state_mod.InvestigationState,
) -> dict[str, Any]:
    """
    Synthesise the investigator's findings into a root cause analysis.

    Reads the investigation context set by ``investigate``, invokes the
    PydanticAI ``root_cause_analyser`` agent, and updates the investigation
    entity with root cause, remediation, and findings.

    On agent failure, returns a graceful fallback so ``determine_confidence``
    can still run and assign low confidence.
    """
    config = get_config()
    alert = state["alert"]
    investigation = state.get("investigation")
    runbook = state.get("runbook")
    classification_category = state.get("classification_category", "")

    # Read investigator context stashed by the previous node.
    inv_ctx: dict[str, Any] = state.get("_investigation_context", {})
    investigation_analysis: str = inv_ctx.get("analysis", "")
    investigation_sources: list[str] = list(inv_ctx.get("sources", []))
    investigation_tool_calls: list[dict[str, Any]] = list(inv_ctx.get("tool_calls", []))
    investigation_status: str = inv_ctx.get("status", "skipped")

    analyser_agent = config.agent_for("root_cause_analyser")
    agent_utils.set_agent_span_attributes(
        prompt_sha256=root_cause_analyser.PROMPT_SHA256,
        model_name=agent_utils.get_model_name(analyser_agent),
        agent_name="root_cause_analyser",
    )

    envelope = state.get("envelope")
    try:
        result = await analyser_agent.run(
            user_prompt=f"Analyse this alert: {alert.title}",
            deps=root_cause_analyser.Dependencies(
                alert_title=alert.title,
                alert_description=alert.description,
                alert_severity=alert.severity.value,
                investigation_analysis=investigation_analysis,
                investigation_tool_calls=investigation_tool_calls,
                investigation_sources=investigation_sources,
                category=classification_category,
                runbook=runbook,
                investigation_status=investigation_status,
                envelope=envelope,
            ),
            toolsets=list(getattr(config, "analyser_toolsets", ())) or None,
            model_settings=agent_utils.build_cache_settings(
                model_name=agent_utils.get_model_name(analyser_agent),
                prompt_sha256=root_cause_analyser.PROMPT_SHA256,
            ),
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"alert_id": alert.id, "node": "analyse_root_cause"},
        )
        fallback_investigation = investigation
        if investigation is not None:
            fallback_investigation = investigation.model_copy(
                update={
                    "root_cause": (
                        "Root cause analysis unavailable — LLM error. Manual investigation required."
                    ),
                    "remediation": "Please investigate this alert manually.",
                }
            )
        return {
            "investigation": fallback_investigation,
            "_investigation_context": {
                **inv_ctx,
                "raw_confidence": 0.0,
            },
        }

    agent_utils.stamp_usage_attributes(
        result.usage(), model_name=agent_utils.get_model_name(analyser_agent)
    )
    analysis: root_cause_analyser.RootCauseAnalysis = result.output

    logs.log_event(
        "root_cause_analysed",
        params={
            "alert_id": alert.id,
            "confidence": analysis.confidence,
            "affected_services": analysis.affected_services,
        },
    )

    findings = [
        investigation_entities.Finding(
            source=source,
            summary=evidence,
            relevance=analysis.confidence,
        )
        for source, evidence in zip(investigation_sources, analysis.evidence, strict=False)
    ]
    remediation = "\n".join(
        f"{i + 1}. {step}" for i, step in enumerate(analysis.remediation_steps)
    )

    updated_investigation = investigation
    if investigation is not None:
        updated_investigation = investigation.model_copy(
            update={
                "findings": findings,
                "root_cause": analysis.root_cause,
                "remediation": remediation,
            }
        )

    return {
        "investigation": updated_investigation,
        "_investigation_context": {
            **inv_ctx,
            "raw_confidence": analysis.confidence,
        },
    }


# ---------------------------------------------------------------------------
# T21 — determine_confidence
# ---------------------------------------------------------------------------


async def determine_confidence(
    state: sre_state_mod.InvestigationState,
) -> dict[str, Any]:
    """
    Compute the confidence score and the approval-gate routing flag.

    Applies the F7 evidence floor before scoring: when the upstream
    investigation produced no real evidence (``investigation_status !=
    "ran"`` and ``tool_calls_with_data == 0``), the analyser's
    self-reported ``raw_confidence`` is clipped to
    ``_EVIDENCE_FLOOR_RELEVANCE_CAP``. This stops eloquent-but-ungrounded
    LLM output from earning a high enough composite score to skip the
    approval gate.

    Also forces ``needs_approval = True`` when ``requires_approval`` is
    set (i.e. runbook matcher returned no-match).
    """
    config = get_config()
    alert = state["alert"]
    investigation = state.get("investigation")
    requires_approval = bool(state.get("requires_approval", False))

    # Read context from the investigation context stashed by previous nodes.
    inv_ctx: dict[str, Any] = state.get("_investigation_context", {})
    investigation_status: str = inv_ctx.get("status", "skipped")
    tool_calls_with_data: int = int(inv_ctx.get("tool_calls_with_data", 0))
    tool_calls_total: int = int(inv_ctx.get("tool_calls_total", 0))
    raw_confidence: float = float(inv_ctx.get("raw_confidence", 0.0))

    # F7 evidence floor: clip relevance when no real investigation data exists.
    no_real_investigation = investigation_status != "ran"
    no_tool_evidence = tool_calls_with_data == 0
    effective_relevance = raw_confidence
    if no_real_investigation and no_tool_evidence:
        effective_relevance = min(raw_confidence, _EVIDENCE_FLOOR_RELEVANCE_CAP)
        logs.log_event(
            "evidence_floor_applied",
            params={
                "alert_id": alert.id,
                "raw_confidence": raw_confidence,
                "capped_relevance": effective_relevance,
                "investigation_status": investigation_status,
                "tool_calls_total": tool_calls_total,
                "tool_calls_with_data": tool_calls_with_data,
            },
        )

    findings_count = len(investigation.findings) if investigation else 0
    try:
        confidence = confidence_entities.ConfidenceScore.from_factors(
            source_count=findings_count,
            max_expected_sources=5,
            relevance=effective_relevance,
            recency=0.8,
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"alert_id": alert.id, "node": "determine_confidence"},
        )
        confidence = confidence_entities.ConfidenceScore.from_total(0.0)

    threshold = config.require_approval_below_confidence
    needs_approval = requires_approval or (confidence.total < threshold)

    logs.log_event(
        "investigation_confidence_determined",
        params={
            "alert_id": alert.id,
            "confidence_label": confidence.label.value,
            "confidence_total": confidence.total,
            "needs_approval": needs_approval,
            "requires_approval": requires_approval,
        },
    )
    metrics.record_confidence_score(pipeline="investigation", score=confidence.total)

    return {"confidence": confidence, "needs_approval": needs_approval}


# ---------------------------------------------------------------------------
# T22 — wait_for_human
# ---------------------------------------------------------------------------


async def wait_for_human(state: sre_state_mod.InvestigationState) -> dict[str, Any]:
    """
    Pause the workflow at the approval gate via ``interrupt()``.

    LangGraph's ``interrupt`` raises ``GraphInterrupt`` on first
    invocation; on resume the entire node body re-executes and
    ``interrupt`` returns the ``Command(resume=...)`` payload. This
    node maps the resume payload onto an ``ApprovalDecision`` enum so
    the rest of the application sees a typed value rather than a
    transport-shaped dict.

    Note on F7 RunbookGrant counters: when the graph resumes, the
    PydanticAI ``RuntimeContext`` (including ``_tool_call_counters``) is
    reconstructed fresh. Counters were per-run by design; resume = same
    logical run, fresh counters — this is correct and expected behaviour.
    The grant budgets are therefore not cumulative across the
    interrupt/resume boundary.
    """
    investigation = state.get("investigation")
    confidence = state.get("confidence")
    envelope = state["envelope"]

    if confidence is None:
        msg = "wait_for_human requires confidence in state"
        raise ValueError(msg)

    payload: dict[str, Any] = {
        "action": "approve_investigation",
        "request_id": str(envelope.request_id),
        "summary": investigation.root_cause if investigation else None,
        "root_cause": investigation.root_cause if investigation else None,
        "remediation": investigation.remediation if investigation else None,
        "confidence_total": confidence.total,
        "confidence_label": confidence.label.value,
    }
    resume_payload = interrupt(payload)
    decision = _approval_decision_from_resume(resume_payload)
    return {"approval_decision": decision}


def _approval_decision_from_resume(resume_payload: Any) -> approval_entities.ApprovalDecision:
    """
    Map a ``Command(resume=...)`` payload onto an ``ApprovalDecision``.

    A truthy ``"approved"`` key maps to ``APPROVED``; any other shape
    (including an explicit ``False``) maps to ``REJECTED``. The
    transport contract is intentionally narrow: approval endpoints own
    the JSON body shape and validate it before resuming the graph.
    """
    if isinstance(resume_payload, dict) and resume_payload.get("approved"):
        return approval_entities.ApprovalDecision.APPROVED
    return approval_entities.ApprovalDecision.REJECTED


# ---------------------------------------------------------------------------
# T23 — publish_findings
# ---------------------------------------------------------------------------


async def publish_findings(state: sre_state_mod.InvestigationState) -> dict[str, Any]:
    """
    Publish investigation findings to Slack and PagerDuty.

    Gated: when ``needs_approval`` is True, publishes only if
    ``approval_decision == APPROVED``. When ``needs_approval`` is False
    (auto-publish path), always publishes. Returns
    ``{"findings_published": True}`` on publish, ``{"findings_published": False}``
    when the publish was skipped (rejected approval).
    """
    config = get_config()
    alert = state["alert"]
    investigation = state.get("investigation")
    confidence = state.get("confidence")
    needs_approval = bool(state.get("needs_approval", False))
    approval_decision = state.get("approval_decision")

    # Gate: skip publish if rejected.
    if needs_approval and approval_decision is not approval_entities.ApprovalDecision.APPROVED:
        logs.log_event(
            "findings_publish_skipped",
            params={
                "alert_id": alert.id,
                "reason": "approval_rejected_or_missing",
                "approval_decision": (approval_decision.value if approval_decision else None),
            },
        )
        return {"findings_published": False}

    # Complete the investigation entity.
    if investigation is not None:
        investigation = investigation.model_copy(
            update={
                "status": investigation_entities.InvestigationStatus.COMPLETED,
                "completed_at": datetime.now(tz=UTC),
                "confidence_score": confidence.total if confidence else None,
            }
        )

    findings_summary = ""
    if investigation and investigation.findings:
        findings_summary = "\n".join(f"- [{f.source}] {f.summary}" for f in investigation.findings)

    confidence_label = confidence.label.value if confidence else None
    root_cause = investigation.root_cause if investigation else None
    remediation = investigation.remediation if investigation else None

    publish_tasks: list[Any] = []

    if getattr(config, "post_to_slack", True):
        publish_tasks.append(
            slack_mod.post_investigation_summary(
                alert_id=alert.id,
                alert_title=alert.title,
                root_cause=root_cause,
                remediation=remediation,
                confidence_label=confidence_label,
                findings_summary=findings_summary,
            )
        )

    pagerduty_client = getattr(config, "pagerduty_client", None)
    if pagerduty_client is not None and alert.source == "pagerduty":
        note_content = pagerduty_client.format_investigation_note(
            root_cause=root_cause,
            remediation=remediation,
            confidence_label=confidence_label,
            findings_summary=findings_summary,
        )
        publish_tasks.append(
            pagerduty_client.add_incident_note(
                incident_id=alert.id,
                content=note_content,
            )
        )

    if publish_tasks:
        results = await asyncio.gather(*publish_tasks, return_exceptions=True)
        for i, publish_result in enumerate(results):
            if isinstance(publish_result, Exception):
                logs.log_exception(
                    publish_result,
                    params={
                        "alert_id": alert.id,
                        "node": "publish_findings",
                        "publish_channel_index": i,
                    },
                )

    logs.log_event(
        "investigation_completed",
        params={
            "alert_id": alert.id,
            "root_cause": root_cause,
            "confidence": confidence.total if confidence else None,
        },
    )

    metrics.record_investigation_completed(
        confidence_label=confidence_label if confidence_label is not None else "unknown",
        approval_required=needs_approval,
        outcome="completed",
    )

    return {"findings_published": True}


# ---------------------------------------------------------------------------
# T24 — routing helpers
# ---------------------------------------------------------------------------


def _route_after_confidence(state: sre_state_mod.InvestigationState) -> str:
    """
    Route either to the approval gate or straight to ``publish_findings``.

    Inspects ``state["needs_approval"]`` (set by ``determine_confidence``)
    and picks the next node accordingly.
    """
    if state.get("needs_approval"):
        return "wait_for_human"
    return "publish_findings"


def _route_after_approval(state: sre_state_mod.InvestigationState) -> str:
    """
    Route either to ``publish_findings`` or terminate at ``END``.

    Inspects ``state["approval_decision"]`` (set by ``wait_for_human``)
    and routes to ``publish_findings`` only when approved.
    """
    if state.get("approval_decision") is approval_entities.ApprovalDecision.APPROVED:
        return "publish_findings"
    return lg_graph.END


# ---------------------------------------------------------------------------
# T26 — graph builder
# ---------------------------------------------------------------------------


def build_sre_investigation_graph(
    *,
    checkpointer: Any,
) -> lg_state.CompiledStateGraph[Any, Any, Any, Any]:
    """
    Compose the SRE investigation ``StateGraph`` and return the compiled graph.

    Every node is wrapped in :func:`with_envelope` so the F2 envelope is
    bound to structlog and OTel for the duration of the node body. The
    conditional edge from ``determine_confidence`` branches to either the
    approval gate or directly to ``publish_findings``; from
    ``wait_for_human`` it branches to ``publish_findings`` (approved) or
    ``END`` (rejected).

    :param checkpointer: A LangGraph checkpointer (typically the
        ``AsyncPostgresSaver`` returned by
        :func:`sentinel.interfaces.workflows._checkpointer.build_checkpointer`).
        Supplied by the FastAPI lifespan.
    """
    builder: lg_graph.StateGraph[Any, Any, Any, Any] = lg_graph.StateGraph(
        sre_state_mod.InvestigationState,
    )

    # Wrap every node in with_envelope so the F2 envelope binds OTel span
    # attributes and structlog context for the duration of the node body.
    # ``cast`` to ``Any`` at the add_node boundary — see support_review.py
    # for the full mypy overload explanation.
    builder.add_node("classify_alert", cast("Any", envelope_mod.with_envelope(classify_alert)))
    builder.add_node("match_runbook", cast("Any", envelope_mod.with_envelope(match_runbook)))
    builder.add_node("investigate", cast("Any", envelope_mod.with_envelope(investigate)))
    builder.add_node(
        "analyse_root_cause", cast("Any", envelope_mod.with_envelope(analyse_root_cause))
    )
    builder.add_node(
        "determine_confidence", cast("Any", envelope_mod.with_envelope(determine_confidence))
    )
    builder.add_node("wait_for_human", cast("Any", envelope_mod.with_envelope(wait_for_human)))
    builder.add_node("publish_findings", cast("Any", envelope_mod.with_envelope(publish_findings)))

    builder.add_edge(lg_graph.START, "classify_alert")
    builder.add_edge("classify_alert", "match_runbook")
    builder.add_edge("match_runbook", "investigate")
    builder.add_edge("investigate", "analyse_root_cause")
    builder.add_edge("analyse_root_cause", "determine_confidence")

    # ``path_map`` enumerates both possible targets so the static graph
    # view (used by visualisation tooling and shape-check tests) records
    # both edges.
    builder.add_conditional_edges(
        "determine_confidence",
        _route_after_confidence,
        path_map={
            "wait_for_human": "wait_for_human",
            "publish_findings": "publish_findings",
        },
    )
    builder.add_conditional_edges(
        "wait_for_human",
        _route_after_approval,
        path_map={
            "publish_findings": "publish_findings",
            lg_graph.END: lg_graph.END,
        },
    )
    builder.add_edge("publish_findings", lg_graph.END)

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# T27 — InvestigationOutcome
# ---------------------------------------------------------------------------


@attrs.frozen(kw_only=True)
class InvestigationOutcome:
    """
    Result of a single :func:`investigate_alert` or :func:`resume_investigation` run.

    The fields mirror the LangGraph state slots populated by the pipeline.
    ``root_cause``, ``remediation``, and ``confidence`` are ``None`` when
    the workflow paused before those nodes executed.
    ``interrupt_payload`` is set to the ``wait_for_human`` interrupt body
    when the run paused at the approval gate, ``None`` once the run
    completed all the way to ``END``.
    ``approval_decision`` is populated after :func:`resume_investigation`
    runs the approval gate to completion.
    """

    request_id: uuid.UUID
    classification_category: str
    root_cause: str | None
    remediation: str | None
    confidence: confidence_entities.ConfidenceScore | None
    needs_approval: bool
    findings_published: bool
    interrupt_payload: dict[str, Any] | None
    approval_decision: approval_entities.ApprovalDecision | None


def _outcome_from_state(state: dict[str, Any], request_id: uuid.UUID) -> InvestigationOutcome:
    """
    Map a LangGraph ``ainvoke`` return value onto an :class:`InvestigationOutcome`.

    LangGraph surfaces a paused run by including an ``__interrupt__`` key
    in the returned state whose value is a tuple of ``Interrupt`` objects.
    Each interrupt's ``.value`` carries the JSON-shaped payload the node
    passed to :func:`langgraph.types.interrupt`. We surface the first
    payload because the SRE graph only ever has one paused node
    (``wait_for_human``).
    """
    interrupts = state.get("__interrupt__")
    interrupt_payload: dict[str, Any] | None = None
    if interrupts:
        interrupt_payload = interrupts[0].value

    investigation: investigation_entities.Investigation | None = state.get("investigation")
    return InvestigationOutcome(
        request_id=request_id,
        classification_category=state.get("classification_category", ""),
        root_cause=investigation.root_cause if investigation else None,
        remediation=investigation.remediation if investigation else None,
        confidence=state.get("confidence"),
        needs_approval=bool(state.get("needs_approval", False)),
        findings_published=bool(state.get("findings_published", False)),
        interrupt_payload=interrupt_payload,
        approval_decision=state.get("approval_decision"),
    )


def _seed_state(
    *,
    alert: alert_entities.Alert,
    envelope: envelope_module.Envelope,
) -> sre_state_mod.InvestigationState:
    """
    Return the initial ``InvestigationState`` for a fresh run.

    Every node-output slot starts at its empty value so the TypedDict
    shape matches what LangGraph's checkpointer persists between steps.
    """
    return {
        "envelope": envelope,
        "alert": alert,
    }


def _thread_config(request_id: uuid.UUID) -> dict[str, Any]:
    """Return the LangGraph runtime config keyed by ``request_id``."""
    return {"configurable": {"thread_id": str(request_id)}}


# ---------------------------------------------------------------------------
# T28 — investigate_alert
# ---------------------------------------------------------------------------


async def investigate_alert(
    *,
    alert: alert_entities.Alert,
    envelope: envelope_module.Envelope,
    graph: lg_state.CompiledStateGraph[Any, Any, Any, Any],
) -> InvestigationOutcome:
    """
    Run the SRE investigation workflow for a single alert.

    Wraps ``graph.ainvoke`` with the seeded TypedDict state and a config
    keyed by ``thread_id = str(envelope.request_id)`` so a paused run
    can be resumed later via :func:`resume_investigation` against the
    same request_id.

    :param alert: The inbound alert to investigate.
    :param envelope: Identity envelope minted at FastAPI ingress.
    :param graph: The compiled SRE investigation graph (typically read off
        ``app.state.sre_investigation_graph`` by the webhook handler).
    """
    state = await cast("Any", graph).ainvoke(
        _seed_state(alert=alert, envelope=envelope),
        config=_thread_config(envelope.request_id),
    )
    return _outcome_from_state(state, envelope.request_id)


# ---------------------------------------------------------------------------
# T29 — resume_investigation
# ---------------------------------------------------------------------------


async def resume_investigation(
    *,
    request_id: uuid.UUID,
    decision: approval_entities.ApprovalDecision,
    graph: lg_state.CompiledStateGraph[Any, Any, Any, Any],
    approver: str | None = None,
    reason: str | None = None,
) -> InvestigationOutcome:
    """
    Resume a paused SRE investigation workflow with an approval decision.

    Builds a ``Command(resume=...)`` payload whose ``approved`` flag is
    ``True`` for :attr:`ApprovalDecision.APPROVED` and ``False`` for every
    other value. The optional ``approver`` and ``reason`` strings ride
    along for the audit trail when supplied.
    """
    resume_payload: dict[str, Any] = {
        "approved": decision is approval_entities.ApprovalDecision.APPROVED,
    }
    if approver is not None:
        resume_payload["approver"] = approver
    if reason is not None:
        resume_payload["reason"] = reason

    state = await cast("Any", graph).ainvoke(
        lg_types.Command(resume=resume_payload),
        config=_thread_config(request_id),
    )
    return _outcome_from_state(state, request_id)


# ---------------------------------------------------------------------------
# T30 — get_investigation_status
# ---------------------------------------------------------------------------


@attrs.frozen(kw_only=True)
class InvestigationStatus:
    """
    Snapshot of an investigation thread's checkpointed state.

    ``status`` is one of ``"pending"`` (paused at the approval gate),
    ``"approved"`` / ``"rejected"`` (resumed and recorded the decision),
    or ``"completed"`` (ran to ``END`` without ever needing approval).
    ``approval_decision`` mirrors the decision when one has been recorded,
    otherwise ``None``.
    """

    request_id: uuid.UUID
    status: str
    needs_approval: bool
    approval_decision: approval_entities.ApprovalDecision | None


async def get_investigation_status(
    *,
    request_id: uuid.UUID,
    graph: lg_state.CompiledStateGraph[Any, Any, Any, Any],
) -> InvestigationStatus | None:
    """
    Return the current status of an investigation thread, or ``None`` when
    no checkpoint exists for the supplied ``request_id``.

    Reads the thread state via ``graph.aget_state(...)``; LangGraph's
    saver returns an empty ``values`` dict for a thread it has never seen,
    which the helper maps onto ``None`` so callers can return HTTP 404.
    """
    snapshot = await cast("Any", graph).aget_state(_thread_config(request_id))
    values: dict[str, Any] = getattr(snapshot, "values", {}) or {}
    if not values:
        return None
    decision = values.get("approval_decision")
    next_nodes = tuple(getattr(snapshot, "next", ()) or ())
    if decision is approval_entities.ApprovalDecision.APPROVED:
        status = "approved"
    elif decision is approval_entities.ApprovalDecision.REJECTED:
        status = "rejected"
    elif "wait_for_human" in next_nodes:
        status = "pending"
    else:
        status = "completed"
    return InvestigationStatus(
        request_id=request_id,
        status=status,
        needs_approval=bool(values.get("needs_approval")),
        approval_decision=decision,
    )
