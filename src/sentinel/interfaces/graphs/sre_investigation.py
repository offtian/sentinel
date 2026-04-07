from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Sequence
from datetime import UTC, datetime

from pydantic_ai.toolsets import AbstractToolset
from pydantic_graph import BaseNode, End, Graph, GraphRunContext

from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.evaluation import comparison
from sentinel.domain.sre import entities as sre_entities
from sentinel.domain.sre import holmes_adapter, investigation
from sentinel.domain.vendor_adapters.pagerduty import PagerDutyClient
from sentinel.interfaces.graphs import common
from sentinel.interfaces.graphs._node_helpers import instrumented_node_run
from sentinel.interfaces.graphs.agents import alert_classifier, root_cause_analyser, utils
from sentinel.settings import get_settings
from sentinel.utils import logs, metrics
from sentinel.vendors import slack


@dataclasses.dataclass
class Dependencies:
    status_update_client: common.StatusUpdateClient
    classifier_model: str
    analyser_model: str
    holmes: holmes_adapter.BaseHolmesAdapter
    pagerduty_client: PagerDutyClient | None = None
    post_to_slack: bool = True
    persist_fn: common.PersistInvestigationFn | None = None
    trace_collector: common.TraceCollector | None = None
    require_approval_below: float = 0.0  # 0 = never require approval
    request_approval_fn: common.RequestApprovalFn | None = None
    # Toolsets injected at agent.run() time.  Built by config.py.
    analyser_toolsets: Sequence[AbstractToolset[object]] = ()
    # Optional challenger adapter for comparison mode.
    challenger_adapter: investigation.BaseInvestigationAdapter | None = None


@dataclasses.dataclass
class State:
    alert: sre_entities.Alert
    investigation: sre_entities.Investigation | None = None
    comparison_result: comparison.ComparisonResult | None = None


@dataclasses.dataclass
class ClassifyAlert(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Classify the incoming alert using a PydanticAI agent."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> InvestigateWithHolmes | End[common.InvestigationReply]:
        async def _impl() -> InvestigateWithHolmes | End[common.InvestigationReply]:
            await ctx.deps.status_update_client.update_status("Classifying alert...")

            try:
                result = await alert_classifier.agent.run(
                    user_prompt=f"Alert: {ctx.state.alert.title}\n\n{ctx.state.alert.description}",
                    model=utils.get_model_with_gateway(ctx.deps.classifier_model),
                    deps=alert_classifier.Dependencies(
                        alert_title=ctx.state.alert.title,
                        alert_description=ctx.state.alert.description,
                        alert_source=ctx.state.alert.source,
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

            if ctx.deps.trace_collector:
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
                    "severity": sre_entities.AlertSeverity(result.output.severity),
                    "service": result.output.affected_service,
                }
            )

            # Initialise the investigation
            ctx.state.investigation = sre_entities.Investigation(
                alert=ctx.state.alert,
                status=sre_entities.InvestigationStatus.INVESTIGATING,
                started_at=datetime.now(tz=UTC),
            )

            return InvestigateWithHolmes()

        return await instrumented_node_run(
            pipeline="sre",
            node="classify_alert",
            fn=_impl,
        )()


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

            # Run challenger adapter concurrently if configured (comparison mode)
            if ctx.deps.challenger_adapter is not None:
                try:
                    challenger_result = await ctx.deps.challenger_adapter.investigate(
                        alert=ctx.state.alert,
                    )
                    # Build a baseline InvestigationResult from Holmes output
                    baseline_result = investigation.InvestigationResult(
                        findings=tuple(
                            sre_entities.Finding(source=s, summary="", relevance=0.5)
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

        return await instrumented_node_run(
            pipeline="sre",
            node="investigate_with_holmes",
            fn=_impl,
        )()


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
                result = await root_cause_analyser.agent.run(
                    user_prompt=f"Analyse this alert: {ctx.state.alert.title}",
                    model=utils.get_model_with_gateway(ctx.deps.analyser_model),
                    deps=root_cause_analyser.Dependencies(
                        alert_title=ctx.state.alert.title,
                        alert_description=ctx.state.alert.description,
                        alert_severity=ctx.state.alert.severity.value,
                        holmes_analysis=self.holmes_analysis,
                        holmes_tool_calls=self.holmes_tool_calls,
                        holmes_sources=self.holmes_sources,
                    ),
                    toolsets=list(ctx.deps.analyser_toolsets) or None,
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

            if ctx.deps.trace_collector:
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
                sre_entities.Finding(
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

        return await instrumented_node_run(
            pipeline="sre",
            node="analyse_root_cause",
            fn=_impl,
        )()


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
                pipeline="sre",
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

        return await instrumented_node_run(
            pipeline="sre",
            node="determine_confidence",
            fn=_impl,
        )()


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
                        "status": sre_entities.InvestigationStatus.COMPLETED,
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

        return await instrumented_node_run(
            pipeline="sre",
            node="publish_findings",
            fn=_impl,
        )()


async def investigate_alert(
    alert: sre_entities.Alert,
    *,
    holmes: holmes_adapter.BaseHolmesAdapter,
    status_update_client: common.StatusUpdateClient | None = None,
    classifier_model: str = "",
    analyser_model: str = "",
    pagerduty_client: PagerDutyClient | None = None,
    post_to_slack: bool = True,
    persist_fn: common.PersistInvestigationFn | None = None,
    trace_collector: common.TraceCollector | None = None,
    require_approval_below: float = 0.0,
    request_approval_fn: common.RequestApprovalFn | None = None,
    analyser_toolsets: Sequence[AbstractToolset[object]] = (),
    challenger_adapter: investigation.BaseInvestigationAdapter | None = None,
) -> common.InvestigationReply:
    """
    Run the full SRE investigation pipeline for an alert.

    This is the main entry point for the investigation graph.
    """
    state = State(alert=alert)
    dependencies = Dependencies(
        status_update_client=status_update_client or common.NoOpStatusUpdateClient(),
        classifier_model=classifier_model or get_settings().alert_classifier_llm,
        analyser_model=analyser_model or get_settings().root_cause_llm,
        holmes=holmes,
        pagerduty_client=pagerduty_client,
        post_to_slack=post_to_slack,
        persist_fn=persist_fn,
        trace_collector=trace_collector,
        require_approval_below=require_approval_below,
        request_approval_fn=request_approval_fn,
        analyser_toolsets=analyser_toolsets,
        challenger_adapter=challenger_adapter,
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
