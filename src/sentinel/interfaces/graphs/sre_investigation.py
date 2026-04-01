from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable
from datetime import UTC, datetime

from pydantic_graph import BaseNode, End, Graph, GraphRunContext

from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.sre import entities as sre_entities
from sentinel.domain.sre import holmes_adapter
from sentinel.domain.vendor_adapters.pagerduty import PagerDutyClient
from sentinel.interfaces.graphs import common
from sentinel.interfaces.graphs.agents import alert_classifier, root_cause_analyser, utils
from sentinel.settings import get_settings
from sentinel.utils import logs
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


@dataclasses.dataclass
class State:
    alert: sre_entities.Alert
    investigation: sre_entities.Investigation | None = None


@dataclasses.dataclass
class ClassifyAlert(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Classify the incoming alert using a PydanticAI agent."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> InvestigateWithHolmes | End[common.InvestigationReply]:
        await ctx.deps.status_update_client.update_status("Classifying alert...")

        result = await alert_classifier.agent.run(
            user_prompt=f"Alert: {ctx.state.alert.title}\n\n{ctx.state.alert.description}",
            model=utils.get_model_with_gateway(ctx.deps.classifier_model),
            deps=alert_classifier.Dependencies(
                alert_title=ctx.state.alert.title,
                alert_description=ctx.state.alert.description,
                alert_source=ctx.state.alert.source,
            ),
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


@dataclasses.dataclass
class InvestigateWithHolmes(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Run HolmesGPT investigation to gather context from observability systems."""

    async def run(self, ctx: GraphRunContext[State, Dependencies]) -> AnalyseRootCause:
        await ctx.deps.status_update_client.update_status(
            "Investigating with observability tools..."
        )

        holmes_result = await ctx.deps.holmes.investigate(alert=ctx.state.alert)

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


@dataclasses.dataclass
class AnalyseRootCause(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Synthesise HolmesGPT findings into a root cause analysis using PydanticAI."""

    holmes_analysis: str = ""
    holmes_tool_calls: list[dict[str, object]] = dataclasses.field(default_factory=list)
    holmes_sources: list[str] = dataclasses.field(default_factory=list)

    async def run(self, ctx: GraphRunContext[State, Dependencies]) -> DetermineConfidence:
        await ctx.deps.status_update_client.update_status("Analysing root cause...")

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
        )

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
            for source, evidence in zip(self.holmes_sources, result.output.evidence, strict=False)
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


@dataclasses.dataclass
class DetermineConfidence(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Calculate confidence score using multi-factor analysis."""

    raw_confidence: float = 0.0

    async def run(self, ctx: GraphRunContext[State, Dependencies]) -> PublishFindings:
        findings_count = (
            len(ctx.state.investigation.findings) if ctx.state.investigation else 0
        )
        confidence = confidence_entities.ConfidenceScore.from_factors(
            source_count=findings_count,
            max_expected_sources=5,
            relevance=self.raw_confidence,
            recency=0.8,
        )

        if ctx.state.investigation:
            ctx.state.investigation = ctx.state.investigation.model_copy(
                update={"confidence_score": confidence.total}
            )

        return PublishFindings(confidence=confidence)


@dataclasses.dataclass
class PublishFindings(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Format and publish the investigation results to Slack, PagerDuty, and database."""

    confidence: confidence_entities.ConfidenceScore | None = None

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> End[common.InvestigationReply]:
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
            sources_queried=([f.source for f in investigation.findings] if investigation else []),
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
            await asyncio.gather(*publish_tasks)

        logs.log_event(
            "investigation_completed",
            params={
                "alert_id": ctx.state.alert.id,
                "root_cause": reply.root_cause,
                "confidence": self.confidence.total if self.confidence else None,
            },
        )

        return End(reply)


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
