from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic_ai.toolsets import AbstractToolset
from pydantic_graph import BaseNode, End, Graph, GraphRunContext

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.evaluation import comparison
from sentinel.domain.investigations import adapters, holmes_adapter
from sentinel.domain.investigations import entities as investigation_entities
from sentinel.domain.vendor_adapters.pagerduty import PagerDutyClient
from sentinel.interfaces.graphs import _node_helpers, common
from sentinel.interfaces.graphs.agents import alert_classifier, root_cause_analyser
from sentinel.interfaces.graphs.agents import utils as agent_utils
from sentinel.utils import logs, metrics
from sentinel.vendors import slack


@dataclasses.dataclass
class Dependencies:
    status_update_client: common.StatusUpdateClient
    agent_for: Callable[[str], Any]
    holmes: holmes_adapter.BaseHolmesAdapter
    pagerduty_client: PagerDutyClient | None = None
    post_to_slack: bool = True
    persist_fn: common.PersistInvestigationFn | None = None
    trace_collector: common.TraceCollector | None = None
    require_approval_below: float = 0.0  # 0 = never require approval
    request_approval_fn: common.RequestApprovalFn | None = None
    # Toolsets injected at agent.run() time.  Built by config.py.
    # Ordering: per-agent toolsets first, shared MCP second.
    # PydanticAI uses first-wins for duplicate tool names.
    classifier_toolsets: Sequence[AbstractToolset[object]] = ()
    analyser_toolsets: Sequence[AbstractToolset[object]] = ()
    # Optional challenger adapter for comparison mode.
    challenger_adapter: adapters.BaseInvestigationAdapter | None = None
    # Optional K8s investigation adapter for cluster state queries.
    k8s_adapter: adapters.K8sInvestigationAdapter | None = None


@dataclasses.dataclass
class State:
    # Identity envelope minted at ingress and threaded through every node.
    # Required (no default) so a misconfigured caller fails closed at
    # State construction rather than silently losing tenant context.
    envelope: envelope_mod.Envelope
    alert: alert_entities.Alert
    investigation: investigation_entities.Investigation | None = None
    comparison_result: comparison.ComparisonResult | None = None
    # Populated by ClassifyAlert and consumed by AnalyseRootCause to drive
    # runbook Skills selection in the root_cause_analyser agent.
    classification_category: str = ""


@dataclasses.dataclass
class ClassifyAlert(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Classify the incoming alert using a PydanticAI agent."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> InvestigateWithHolmes | End[common.InvestigationReply]:
        async def _impl() -> InvestigateWithHolmes | End[common.InvestigationReply]:
            await ctx.deps.status_update_client.update_status("Classifying alert...")

            try:
                classifier_agent = ctx.deps.agent_for("alert_classifier")
                agent_utils.set_agent_span_attributes(
                    prompt_sha256=alert_classifier.PROMPT_SHA256,
                    model_name=agent_utils.get_model_name(classifier_agent),
                )
                result = await classifier_agent.run(
                    user_prompt=f"Alert: {ctx.state.alert.title}\n\n{ctx.state.alert.description}",
                    deps=alert_classifier.Dependencies(
                        alert_title=ctx.state.alert.title,
                        alert_description=ctx.state.alert.description,
                        alert_source=ctx.state.alert.source,
                    ),
                    toolsets=list(ctx.deps.classifier_toolsets) or None,
                    model_settings=agent_utils.build_cache_settings(
                        model_name=agent_utils.get_model_name(classifier_agent),
                        prompt_sha256=alert_classifier.PROMPT_SHA256,
                    ),
                )
            except Exception as exc:
                logs.log_exception(
                    exc,
                    params={"alert_id": ctx.state.alert.id, "node": "ClassifyAlert"},
                )
                return End(
                    common.InvestigationReply(
                        alert_id=ctx.state.alert.id,
                        root_cause=f"Classification failed: {type(exc).__name__} — {exc}",
                    )
                )

            if ctx.deps.trace_collector and hasattr(
                ctx.deps.trace_collector, "record_agent_result"
            ):
                await ctx.deps.trace_collector.record_agent_result(
                    node_id=uuid.uuid4(),
                    agent_name="Alert Classifier",
                    model_id=agent_utils.get_model_name(classifier_agent),
                    result=result,
                )
            elif ctx.deps.trace_collector:
                ctx.deps.trace_collector.record(
                    agent_name="Alert Classifier",
                    messages=result.all_messages(),
                )

            logs.log_event(
                "alert_classified",
                params={
                    "alert_id": ctx.state.alert.id,
                    "severity": result.output.severity,
                    "category": result.output.category,
                    "service": result.output.affected_service,
                },
            )

            # Update alert severity based on classification
            ctx.state.alert = ctx.state.alert.model_copy(
                update={
                    "severity": alert_entities.AlertSeverity(result.output.severity),
                    "service": result.output.affected_service,
                }
            )

            # Store the classification category so downstream agents can use
            # it to pick the right runbook Skill.
            ctx.state.classification_category = result.output.category

            # Initialise the investigation
            ctx.state.investigation = investigation_entities.Investigation(
                alert=ctx.state.alert,
                status=investigation_entities.InvestigationStatus.INVESTIGATING,
                started_at=datetime.now(tz=UTC),
            )

            return InvestigateWithHolmes()

        return await _node_helpers.run_node_with_envelope(
            pipeline="investigation",
            node="classify_alert",
            envelope=ctx.state.envelope,
            fn=_impl,
        )


@dataclasses.dataclass
class InvestigateWithHolmes(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Run HolmesGPT investigation to gather context from observability systems."""

    async def run(self, ctx: GraphRunContext[State, Dependencies]) -> AnalyseRootCause:
        async def _impl() -> AnalyseRootCause:
            await ctx.deps.status_update_client.update_status(
                "Investigating with observability tools..."
            )

            try:
                holmes_result = await ctx.deps.holmes.investigate(alert=ctx.state.alert)
            except Exception as exc:
                logs.log_exception(
                    exc,
                    params={"alert_id": ctx.state.alert.id, "node": "InvestigateWithHolmes"},
                )
                return AnalyseRootCause(
                    holmes_analysis="Observability investigation unavailable — proceeding with alert context only.",
                    holmes_tool_calls=[],
                    holmes_sources=[],
                )

            # Run K8s adapter if configured — merges cluster state into findings.
            if ctx.deps.k8s_adapter is not None and ctx.deps.k8s_adapter.is_configured:
                try:
                    k8s_context = adapters.InvestigationContext(
                        cluster_name=ctx.state.alert.raw_payload.get("cluster", "default"),
                        namespace=ctx.state.alert.raw_payload.get("namespace"),
                    )
                    k8s_result = await ctx.deps.k8s_adapter.investigate(
                        alert=ctx.state.alert,
                        context=k8s_context,
                    )
                    # Merge K8s findings into Holmes results.
                    holmes_result = holmes_adapter.HolmesInvestigationResult(
                        analysis=(
                            holmes_result.analysis
                            + "\n\n--- Kubernetes cluster state ---\n"
                            + "\n".join(f.summary for f in k8s_result.findings)
                        ),
                        tool_calls=holmes_result.tool_calls,
                        sources_queried=(
                            holmes_result.sources_queried + list(k8s_result.sources_queried)
                        ),
                    )
                    logs.log_event(
                        "k8s_investigation_merged",
                        params={
                            "alert_id": ctx.state.alert.id,
                            "k8s_sources": list(k8s_result.sources_queried),
                            "k8s_findings_count": len(k8s_result.findings),
                        },
                    )
                except Exception as exc:
                    logs.log_exception(
                        exc,
                        params={
                            "alert_id": ctx.state.alert.id,
                            "node": "InvestigateWithHolmes",
                            "k8s_adapter": "failed",
                        },
                    )

            # Run challenger adapter concurrently if configured (comparison mode)
            if ctx.deps.challenger_adapter is not None:
                try:
                    challenger_result = await ctx.deps.challenger_adapter.investigate(
                        alert=ctx.state.alert,
                    )
                    # Build a baseline InvestigationResult from Holmes output
                    baseline_result = adapters.InvestigationResult(
                        findings=tuple(
                            investigation_entities.Finding(source=s, summary="", relevance=0.5)
                            for s in holmes_result.sources_queried
                        ),
                        sources_queried=tuple(holmes_result.sources_queried),
                        duration_ms=0,
                        adapter_name="holmes",
                    )
                    ctx.state.comparison_result = (
                        comparison.ComparisonResult.from_investigation_results(
                            baseline=baseline_result,
                            challenger=challenger_result,
                            case_id=ctx.state.alert.id,
                        )
                    )
                except Exception as exc:
                    logs.log_exception(
                        exc,
                        params={
                            "alert_id": ctx.state.alert.id,
                            "node": "InvestigateWithHolmes",
                            "comparison": "challenger_failed",
                        },
                    )

            logs.log_event(
                "holmes_investigation_completed",
                params={
                    "alert_id": ctx.state.alert.id,
                    "sources_queried": holmes_result.sources_queried,
                    "tool_calls_count": len(holmes_result.tool_calls),
                },
            )

            return AnalyseRootCause(
                holmes_analysis=holmes_result.analysis,
                holmes_tool_calls=holmes_result.tool_calls,
                holmes_sources=holmes_result.sources_queried,
            )

        return await _node_helpers.run_node_with_envelope(
            pipeline="investigation",
            node="investigate_with_holmes",
            envelope=ctx.state.envelope,
            fn=_impl,
        )


@dataclasses.dataclass
class AnalyseRootCause(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Synthesise HolmesGPT findings into a root cause analysis using PydanticAI."""

    holmes_analysis: str = ""
    holmes_tool_calls: list[dict[str, object]] = dataclasses.field(default_factory=list)
    holmes_sources: list[str] = dataclasses.field(default_factory=list)

    async def run(self, ctx: GraphRunContext[State, Dependencies]) -> DetermineConfidence:
        async def _impl() -> DetermineConfidence:
            await ctx.deps.status_update_client.update_status("Analysing root cause...")

            try:
                analyser_agent = ctx.deps.agent_for("root_cause_analyser")
                agent_utils.set_agent_span_attributes(
                    prompt_sha256=root_cause_analyser.PROMPT_SHA256,
                    model_name=agent_utils.get_model_name(analyser_agent),
                )
                result = await analyser_agent.run(
                    user_prompt=f"Analyse this alert: {ctx.state.alert.title}",
                    deps=root_cause_analyser.Dependencies(
                        alert_title=ctx.state.alert.title,
                        alert_description=ctx.state.alert.description,
                        alert_severity=ctx.state.alert.severity.value,
                        holmes_analysis=self.holmes_analysis,
                        holmes_tool_calls=self.holmes_tool_calls,
                        holmes_sources=self.holmes_sources,
                        category=ctx.state.classification_category,
                    ),
                    toolsets=list(ctx.deps.analyser_toolsets) or None,
                    model_settings=agent_utils.build_cache_settings(
                        model_name=agent_utils.get_model_name(analyser_agent),
                        prompt_sha256=root_cause_analyser.PROMPT_SHA256,
                    ),
                )
            except Exception as exc:
                logs.log_exception(
                    exc,
                    params={"alert_id": ctx.state.alert.id, "node": "AnalyseRootCause"},
                )
                if ctx.state.investigation:
                    ctx.state.investigation = ctx.state.investigation.model_copy(
                        update={
                            "root_cause": "Root cause analysis unavailable — LLM error. Manual investigation required.",
                            "remediation": "Please investigate this alert manually.",
                        }
                    )
                return DetermineConfidence(raw_confidence=0.0)

            if ctx.deps.trace_collector and hasattr(
                ctx.deps.trace_collector, "record_agent_result"
            ):
                await ctx.deps.trace_collector.record_agent_result(
                    node_id=uuid.uuid4(),
                    agent_name="Root Cause Analyser",
                    model_id=agent_utils.get_model_name(analyser_agent),
                    result=result,
                )
            elif ctx.deps.trace_collector:
                ctx.deps.trace_collector.record(
                    agent_name="Root Cause Analyser",
                    messages=result.all_messages(),
                )

            logs.log_event(
                "root_cause_analysed",
                params={
                    "alert_id": ctx.state.alert.id,
                    "confidence": result.output.confidence,
                    "affected_services": result.output.affected_services,
                },
            )

            # Update investigation with findings
            findings = [
                investigation_entities.Finding(
                    source=source,
                    summary=evidence,
                    relevance=result.output.confidence,
                )
                for source, evidence in zip(
                    self.holmes_sources, result.output.evidence, strict=False
                )
            ]

            if ctx.state.investigation:
                ctx.state.investigation = ctx.state.investigation.model_copy(
                    update={
                        "findings": findings,
                        "root_cause": result.output.root_cause,
                        "remediation": "\n".join(
                            f"{i + 1}. {step}"
                            for i, step in enumerate(result.output.remediation_steps)
                        ),
                    }
                )

            return DetermineConfidence(raw_confidence=result.output.confidence)

        return await _node_helpers.run_node_with_envelope(
            pipeline="investigation",
            node="analyse_root_cause",
            envelope=ctx.state.envelope,
            fn=_impl,
        )


@dataclasses.dataclass
class DetermineConfidence(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Calculate confidence score using multi-factor analysis."""

    raw_confidence: float = 0.0

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> PublishFindings | End[common.InvestigationReply]:
        async def _impl() -> PublishFindings | End[common.InvestigationReply]:
            try:
                findings_count = (
                    len(ctx.state.investigation.findings) if ctx.state.investigation else 0
                )
                confidence = confidence_entities.ConfidenceScore.from_factors(
                    source_count=findings_count,
                    max_expected_sources=5,
                    relevance=self.raw_confidence,
                    recency=0.8,
                )
            except Exception as exc:
                logs.log_exception(
                    exc,
                    params={"alert_id": ctx.state.alert.id, "node": "DetermineConfidence"},
                )
                confidence = confidence_entities.ConfidenceScore.from_total(0.0)

            if ctx.state.investigation:
                ctx.state.investigation = ctx.state.investigation.model_copy(
                    update={"confidence_score": confidence.total}
                )

            metrics.record_confidence_score(
                pipeline="investigation",
                score=confidence.total,
            )

            # Gate: if confidence is below threshold and approval function is configured,
            # post approval request to Slack instead of publishing directly.
            if (
                ctx.deps.require_approval_below > 0
                and confidence.total < ctx.deps.require_approval_below
                and ctx.deps.request_approval_fn
            ):
                investigation_id = (
                    str(ctx.state.investigation.id) if ctx.state.investigation else "unknown"
                )
                findings_summary = ""
                if ctx.state.investigation and ctx.state.investigation.findings:
                    findings_summary = "\n".join(
                        f"- [{f.source}] {f.summary}" for f in ctx.state.investigation.findings
                    )

                await ctx.deps.request_approval_fn(
                    investigation_id,
                    ctx.state.alert.id,
                    ctx.state.alert.title,
                    ctx.state.investigation.root_cause if ctx.state.investigation else None,
                    ctx.state.investigation.remediation if ctx.state.investigation else None,
                    confidence.label.value if confidence else None,
                    findings_summary,
                )

                logs.log_event(
                    "approval_required",
                    params={
                        "alert_id": ctx.state.alert.id,
                        "confidence": confidence.total,
                        "threshold": ctx.deps.require_approval_below,
                    },
                )

                return End(
                    common.InvestigationReply(
                        alert_id=ctx.state.alert.id,
                        root_cause=(
                            ctx.state.investigation.root_cause if ctx.state.investigation else None
                        ),
                        remediation=(
                            ctx.state.investigation.remediation
                            if ctx.state.investigation
                            else None
                        ),
                        confidence=confidence,
                        findings_summary=findings_summary,
                        sources_queried=(
                            [f.source for f in ctx.state.investigation.findings]
                            if ctx.state.investigation
                            else []
                        ),
                        approval_status="pending",
                    )
                )

            return PublishFindings(confidence=confidence)

        return await _node_helpers.run_node_with_envelope(
            pipeline="investigation",
            node="determine_confidence",
            envelope=ctx.state.envelope,
            fn=_impl,
        )


@dataclasses.dataclass
class PublishFindings(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Format and publish the investigation results to Slack, PagerDuty, and database."""

    confidence: confidence_entities.ConfidenceScore | None = None

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> End[common.InvestigationReply]:
        async def _impl() -> End[common.InvestigationReply]:
            await ctx.deps.status_update_client.update_status("Publishing findings...")

            investigation = ctx.state.investigation
            if investigation:
                investigation = investigation.model_copy(
                    update={
                        "status": investigation_entities.InvestigationStatus.COMPLETED,
                        "completed_at": datetime.now(tz=UTC),
                    }
                )
                ctx.state.investigation = investigation

            findings_summary = ""
            if investigation and investigation.findings:
                findings_summary = "\n".join(
                    f"- [{f.source}] {f.summary}" for f in investigation.findings
                )

            confidence_label = self.confidence.label.value if self.confidence else None

            reply = common.InvestigationReply(
                alert_id=ctx.state.alert.id,
                root_cause=investigation.root_cause if investigation else None,
                remediation=investigation.remediation if investigation else None,
                confidence=self.confidence,
                findings_summary=findings_summary,
                sources_queried=(
                    [f.source for f in investigation.findings] if investigation else []
                ),
            )

            # Publish to all output channels concurrently
            publish_tasks: list[Awaitable[object]] = []

            if ctx.deps.post_to_slack:
                publish_tasks.append(
                    slack.post_investigation_summary(
                        alert_id=ctx.state.alert.id,
                        alert_title=ctx.state.alert.title,
                        root_cause=reply.root_cause,
                        remediation=reply.remediation,
                        confidence_label=confidence_label,
                        findings_summary=findings_summary,
                    )
                )

            if ctx.deps.pagerduty_client and ctx.state.alert.source == "pagerduty":
                note_content = ctx.deps.pagerduty_client.format_investigation_note(
                    root_cause=reply.root_cause,
                    remediation=reply.remediation,
                    confidence_label=confidence_label,
                    findings_summary=findings_summary,
                )
                publish_tasks.append(
                    ctx.deps.pagerduty_client.add_incident_note(
                        incident_id=ctx.state.alert.id,
                        content=note_content,
                    )
                )

            if ctx.deps.persist_fn:
                publish_tasks.append(ctx.deps.persist_fn(reply))

            if publish_tasks:
                results = await asyncio.gather(*publish_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logs.log_exception(
                            result,
                            params={
                                "alert_id": ctx.state.alert.id,
                                "node": "PublishFindings",
                                "publish_channel_index": i,
                            },
                        )

            logs.log_event(
                "investigation_completed",
                params={
                    "alert_id": ctx.state.alert.id,
                    "root_cause": reply.root_cause,
                    "confidence": self.confidence.total if self.confidence else None,
                },
            )

            metrics.record_investigation_completed(
                confidence_label=confidence_label if confidence_label is not None else "unknown",
                approval_required=False,
                outcome="completed",
            )

            return End(reply)

        return await _node_helpers.run_node_with_envelope(
            pipeline="investigation",
            node="publish_findings",
            envelope=ctx.state.envelope,
            fn=_impl,
        )


async def investigate_alert(
    alert: alert_entities.Alert,
    *,
    envelope: envelope_mod.Envelope,
    agent_for: Callable[[str], Any],
    holmes: holmes_adapter.BaseHolmesAdapter,
    status_update_client: common.StatusUpdateClient | None = None,
    pagerduty_client: PagerDutyClient | None = None,
    post_to_slack: bool = True,
    persist_fn: common.PersistInvestigationFn | None = None,
    trace_collector: common.TraceCollector | None = None,
    require_approval_below: float = 0.0,
    request_approval_fn: common.RequestApprovalFn | None = None,
    classifier_toolsets: Sequence[AbstractToolset[object]] = (),
    analyser_toolsets: Sequence[AbstractToolset[object]] = (),
    challenger_adapter: adapters.BaseInvestigationAdapter | None = None,
    k8s_adapter: adapters.K8sInvestigationAdapter | None = None,
) -> common.InvestigationReply:
    """
    Run the full SRE investigation pipeline for an alert.

    This is the main entry point for the investigation graph.

    :param alert: The alert to investigate.
    :param envelope: Identity envelope minted at ingress (RFC §3.1). Required;
        carries ``request_id``, ``tenant_id``, ``cluster_id``, ``region``,
        ``pii_class``, and ``received_at`` to every span and log line.
    """
    state = State(envelope=envelope, alert=alert)
    dependencies = Dependencies(
        status_update_client=status_update_client or common.NoOpStatusUpdateClient(),
        agent_for=agent_for,
        holmes=holmes,
        pagerduty_client=pagerduty_client,
        post_to_slack=post_to_slack,
        persist_fn=persist_fn,
        trace_collector=trace_collector,
        require_approval_below=require_approval_below,
        request_approval_fn=request_approval_fn,
        classifier_toolsets=classifier_toolsets,
        analyser_toolsets=analyser_toolsets,
        challenger_adapter=challenger_adapter,
        k8s_adapter=k8s_adapter,
    )

    investigation_graph = Graph(
        nodes=(
            ClassifyAlert,
            InvestigateWithHolmes,
            AnalyseRootCause,
            DetermineConfidence,
            PublishFindings,
        ),
    )

    result = await investigation_graph.run(
        ClassifyAlert(),
        deps=dependencies,
        state=state,
    )
    return result.output
