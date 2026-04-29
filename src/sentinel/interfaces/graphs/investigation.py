from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic_ai.toolsets import AbstractToolset
from pydantic_graph import BaseNode, End, Graph, GraphRunContext

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.evaluation import comparison
from sentinel.domain.investigations import adapters
from sentinel.domain.investigations import entities as investigation_entities
from sentinel.domain.runbooks import alert_view as runbook_alert_view
from sentinel.domain.runbooks import matcher as runbook_matcher
from sentinel.domain.runbooks import models as runbook_models
from sentinel.domain.runbooks import persistence as runbook_persistence
from sentinel.domain.runbooks import rag as runbook_rag
from sentinel.domain.vendor_adapters.pagerduty import PagerDutyClient
from sentinel.interfaces.graphs import _node_helpers, common
from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    investigator,
    root_cause_analyser,
    runbook_disambiguator,
)
from sentinel.interfaces.graphs.agents import utils as agent_utils
from sentinel.utils import logs, metrics
from sentinel.vendors import slack


# Type alias for the async session factory callable supplied by the worker.
# Returns an async context manager yielding an ``AsyncSession``. Defined as
# Any here because the actual session type lives in SQLAlchemy and threading
# the type would force every interface caller to import it.
SessionFactory = Callable[[], Any]


@dataclasses.dataclass
class Dependencies:
    status_update_client: common.StatusUpdateClient
    agent_for: Callable[[str], Any]
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
    # F7: investigator owns logs/metrics/traces tools (formerly on
    # analyser_toolsets via the HolmesGPT-shaped pipeline). The split
    # mirrors the gather/synthesise division of labour between the
    # investigator and root-cause analyser agents.
    investigator_toolsets: Sequence[AbstractToolset[object]] = ()
    # Analyser-side toolsets are now reserved for synthesis-time helpers
    # (citations, doc lookups) — not raw observability queries. Empty by
    # default; populated by team-specific configs that want extras.
    analyser_toolsets: Sequence[AbstractToolset[object]] = ()
    # Optional challenger adapter for comparison mode.
    challenger_adapter: adapters.BaseInvestigationAdapter | None = None
    # Optional K8s investigation adapter for cluster state queries.
    k8s_adapter: adapters.K8sInvestigationAdapter | None = None
    # F6.F.1: runbook catalog (already-loaded mapping) consumed by the
    # MatchRunbook node. The worker is the single owner of disk I/O —
    # callers pass a pre-loaded mapping so the node never re-reads disk
    # per alert. ``None`` short-circuits the matcher entirely (e.g. unit
    # tests that don't exercise runbook matching).
    runbooks: Mapping[str, runbook_models.Runbook] | None = None
    # F6.F.1: callable returning an async context manager that yields an
    # ``AsyncSession`` for runbook-match audit-row persistence. ``None``
    # disables persistence (e.g. unit tests that mock the matcher).
    db_session_factory: SessionFactory | None = None
    # F6.F.1: F6.J Stage 3 RAG fallback knobs surfaced from BaseConfiguration.
    # Threaded as scalars so the node can build the RagFallback inline against
    # the per-call session — the embedder is built once by the worker.
    rag_embedder: runbook_rag.Embedder | None = None
    rag_enabled: bool = False
    rag_top_k: int = 5
    rag_min_similarity: float = 0.78
    # F6.F.1: model id for the disambiguator agent (Stage 2A/2B). When None,
    # falls back to the agent's "test" placeholder which is overridden at
    # runtime by config wiring.
    disambiguator_model: str | None = None


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
    # F6.F.1: matched runbook from the MatchRunbook node. ``None`` on
    # no-match — downstream agents fall back to the generic-exploration
    # frame and ``requires_approval`` is set to True.
    runbook: runbook_models.Runbook | None = None
    # F6.F.1: result of the matcher orchestrator, kept on the state so
    # downstream nodes (e.g. F8 approval gate) and worker bootstrap can
    # cross-reference it with the audit row without re-running the matcher.
    runbook_match: runbook_models.RunbookMatch | None = None
    # F6.F.1: id of the runbook_match audit row, returned by
    # ``write_runbook_match``. Stashed so the F8 feedback writer can FK to
    # it without re-querying.
    runbook_match_id: uuid.UUID | None = None
    # F6.F.1: True iff the matcher returned no_match — downstream nodes
    # use this to enforce human approval per F6 spec §5.4 generic-playbook
    # contract (low-confidence outputs always require review).
    requires_approval: bool = False


@dataclasses.dataclass
class ClassifyAlert(BaseNode[State, Dependencies, common.InvestigationReply]):
    """Classify the incoming alert using a PydanticAI agent."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> MatchRunbook | End[common.InvestigationReply]:
        async def _impl() -> MatchRunbook | End[common.InvestigationReply]:
            await ctx.deps.status_update_client.update_status("Classifying alert...")

            try:
                classifier_agent = ctx.deps.agent_for("alert_classifier")
                agent_utils.set_agent_span_attributes(
                    prompt_sha256=alert_classifier.PROMPT_SHA256,
                    model_name=agent_utils.get_model_name(classifier_agent),
                    agent_name="alert_classifier",
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

            return MatchRunbook()

        return await _node_helpers.run_node_with_envelope(
            pipeline="investigation",
            node="classify_alert",
            envelope=ctx.state.envelope,
            fn=_impl,
        )


@dataclasses.dataclass
class MatchRunbook(BaseNode[State, Dependencies, common.InvestigationReply]):
    """
    Match the classified alert against the runbook catalog (F6.F.1).

    Calls :func:`sentinel.domain.runbooks.matcher.match_runbook` against the
    pre-loaded catalog supplied via :attr:`Dependencies.runbooks`. Always
    writes a ``runbook_match`` audit row (including ``no_match`` outcomes).
    On a successful match, pre-populates ``investigation_task`` rows from
    ``runbook.checks.prescribed_checks`` so the F8 quality gate can verify
    "every required check ran" against the row IDs without re-reading the
    runbook from disk.

    State touched:

    * ``state.runbook`` — set to the matched runbook on success, ``None``
      on no-match (the K8sInvestigator and RootCauseAnalyser agents read
      this off their Dependencies and inject the body into a
      ``<runbook>`` quarantine frame; F6 spec §7.2).
    * ``state.runbook_match`` — full :class:`RunbookMatch` for downstream
      audit / approval-gate cross-reference.
    * ``state.runbook_match_id`` — UUID of the persisted audit row.
    * ``state.requires_approval`` — True iff no-match. The
      ``DetermineConfidence`` gate ANDs this with the confidence threshold,
      so the generic-playbook fallback always lands in the approval queue.

    Replay contract (F6.F.4): the matcher is deterministic under fixed
    LLM I/O. Stage 1 is purely structural; Stage 2A/2B disambiguator I/O
    is captured by the F4 replay bundle (PydanticAI agent runs are
    instrumented end-to-end). Stage 3 RAG embedding I/O is captured by the
    LiteLLM SDK path (``runbook_embedder`` tool name). On replay, this
    node re-runs the matcher and the bundle's recorded LLM responses
    drive Stage 2 / Stage 3 to the same outcome — no per-node replay
    rehydration hook is required.

    Soft-degrade contract: ``Dependencies.runbooks=None`` short-circuits
    the matcher (yields a no-match result without writes) so unit tests
    that don't exercise the catalog can omit it.
    """

    async def run(self, ctx: GraphRunContext[State, Dependencies]) -> Investigate:
        async def _impl() -> Investigate:
            await ctx.deps.status_update_client.update_status("Matching runbook...")

            if ctx.deps.runbooks is None:
                # No catalog wired — degrade to no-match without writes.
                logs.log_event(
                    "runbook_match_skipped",
                    params={
                        "alert_id": ctx.state.alert.id,
                        "reason": "no_catalog_configured",
                    },
                )
                ctx.state.runbook = None
                ctx.state.requires_approval = True
                return Investigate()

            matchable_alert = runbook_alert_view.MatchableAlertView.from_alert(
                alert=ctx.state.alert,
                envelope=ctx.state.envelope,
            )

            try:
                match = await _run_matcher_and_persist(
                    ctx=ctx,
                    matchable_alert=matchable_alert,
                )
            except Exception as exc:
                # Matcher / persistence failure must not crash the pipeline.
                # Soft-degrade to no-match so investigation continues with the
                # generic frame and the alert is flagged for review.
                logs.log_exception(
                    exc,
                    params={
                        "alert_id": ctx.state.alert.id,
                        "node": "MatchRunbook",
                    },
                )
                ctx.state.runbook = None
                ctx.state.requires_approval = True
                return Investigate()

            ctx.state.runbook_match = match
            matched = (
                ctx.deps.runbooks.get(match.matched_runbook_id)
                if match.matched_runbook_id is not None
                else None
            )
            ctx.state.runbook = matched
            ctx.state.requires_approval = matched is None

            logs.log_event(
                "runbook_matched",
                params={
                    "alert_id": ctx.state.alert.id,
                    "runbook_id": match.matched_runbook_id,
                    "match_method": match.match_method,
                    "tag_score": match.tag_score,
                    "requires_approval": ctx.state.requires_approval,
                },
            )

            return Investigate()

        return await _node_helpers.run_node_with_envelope(
            pipeline="investigation",
            node="match_runbook",
            envelope=ctx.state.envelope,
            fn=_impl,
        )


async def _run_matcher_and_persist(
    *,
    ctx: GraphRunContext[State, Dependencies],
    matchable_alert: runbook_alert_view.MatchableAlertView,
) -> runbook_models.RunbookMatch:
    """
    Run :func:`runbook_matcher.match_runbook` and persist the audit row.

    Helper extracted so :meth:`MatchRunbook.run` stays under the project's
    50-line cap (PLR0915). Always writes the ``runbook_match`` row,
    including ``no_match`` outcomes; pre-populates
    ``investigation_task`` rows from ``prescribed_checks`` on success.
    """
    runbooks = ctx.deps.runbooks
    if runbooks is None:
        msg = "ctx.deps.runbooks is None inside _run_matcher_and_persist"
        raise RuntimeError(msg)

    async def _disambiguator(
        summary: str,
        candidates: tuple[tuple[str, str], ...],
    ) -> runbook_models.DisambiguatorChoice:
        return await runbook_disambiguator.disambiguate(
            alert_summary=summary,
            candidates=candidates,
            model=ctx.deps.disambiguator_model,
        )

    if ctx.deps.db_session_factory is None:
        # No DB wired — run matcher with RAG disabled and skip persistence.
        return await runbook_matcher.match_runbook(
            alert=matchable_alert,
            envelope=ctx.state.envelope,
            runbooks=runbooks,
            disambiguator=_disambiguator,
            rag_fallback=None,
        )

    async with ctx.deps.db_session_factory() as session:
        rag_fallback: runbook_rag.RagFallback | None = None
        if ctx.deps.rag_embedder is not None:
            rag_fallback = runbook_rag.RagFallback(
                embedder=ctx.deps.rag_embedder,
                session=session,
                enabled=ctx.deps.rag_enabled,
                top_k=ctx.deps.rag_top_k,
                min_similarity=ctx.deps.rag_min_similarity,
            )
        match = await runbook_matcher.match_runbook(
            alert=matchable_alert,
            envelope=ctx.state.envelope,
            runbooks=runbooks,
            disambiguator=_disambiguator,
            rag_fallback=rag_fallback,
        )
        match_id = await runbook_persistence.write_runbook_match(
            session=session,
            envelope=ctx.state.envelope,
            match=match,
        )
        ctx.state.runbook_match_id = match_id
        if match.matched_runbook_id is not None:
            matched_runbook = runbooks.get(match.matched_runbook_id)
            if matched_runbook is not None and ctx.state.investigation is not None:
                await runbook_persistence.write_prescribed_check_tasks(
                    session=session,
                    investigation_id=ctx.state.investigation.id,
                    runbook=matched_runbook,
                )
        await session.commit()
    return match


@dataclasses.dataclass
class _InvestigatorOutcome:
    """
    Internal bundle of investigator-agent run results, threaded through
    the ``Investigate`` node and merged with K8s-adapter findings before
    being forwarded to ``AnalyseRootCause``.
    """

    analysis: str = ""
    sources: list[str] = dataclasses.field(default_factory=list)
    tool_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    status: str = "skipped"
    calls_with_data: int = 0
    calls_total: int = 0


async def _run_investigator_agent(
    *, ctx: GraphRunContext[State, Dependencies]
) -> _InvestigatorOutcome:
    """
    Run the F7 investigator agent against the alert and shape its output.

    Extracted from :class:`Investigate.run` so the node body stays under
    the project's complexity caps (PLR0915 / C901). Status semantics are
    documented on :class:`Investigate`.
    """
    try:
        investigator_agent = ctx.deps.agent_for("investigator")
    except Exception as exc:
        logs.log_event(
            "investigator_skipped",
            params={
                "alert_id": ctx.state.alert.id,
                "reason": f"agent_not_configured: {type(exc).__name__}",
            },
        )
        return _InvestigatorOutcome(status="skipped")

    try:
        agent_utils.set_agent_span_attributes(
            prompt_sha256=investigator.PROMPT_SHA256,
            model_name=agent_utils.get_model_name(investigator_agent),
            agent_name="investigator",
        )
        result = await investigator_agent.run(
            user_prompt=f"Investigate alert: {ctx.state.alert.title}",
            deps=investigator.Dependencies(
                alert_title=ctx.state.alert.title,
                alert_description=ctx.state.alert.description,
                alert_severity=ctx.state.alert.severity.value,
                service=ctx.state.alert.service or "",
                cluster_name=str(ctx.state.alert.raw_payload.get("cluster", "")),
                namespace=str(ctx.state.alert.raw_payload.get("namespace", "")),
                runbook=ctx.state.runbook,
            ),
            toolsets=list(ctx.deps.investigator_toolsets) or None,
            model_settings=agent_utils.build_cache_settings(
                model_name=agent_utils.get_model_name(investigator_agent),
                prompt_sha256=investigator.PROMPT_SHA256,
            ),
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"alert_id": ctx.state.alert.id, "node": "Investigate"},
        )
        return _InvestigatorOutcome(
            status="failed",
            analysis=(
                "Investigation engine raised an exception — proceeding with alert context only."
            ),
        )

    analysis = result.output.summary
    sources = list(result.output.sources_queried)
    tool_calls = [
        {"tool": tc.tool, "query": tc.query, "result_kind": tc.result_kind}
        for tc in result.output.tool_calls
    ]
    calls_total = len(tool_calls)
    calls_with_data = sum(1 for tc in result.output.tool_calls if tc.result_kind == "data")
    status = "ran" if (analysis or sources or calls_with_data > 0) else "empty"

    if ctx.deps.trace_collector and hasattr(ctx.deps.trace_collector, "record_agent_result"):
        await ctx.deps.trace_collector.record_agent_result(
            node_id=uuid.uuid4(),
            agent_name="Investigator",
            model_id=agent_utils.get_model_name(investigator_agent),
            result=result,
        )
    elif ctx.deps.trace_collector:
        ctx.deps.trace_collector.record(
            agent_name="Investigator",
            messages=result.all_messages(),
        )

    return _InvestigatorOutcome(
        analysis=analysis,
        sources=sources,
        tool_calls=tool_calls,
        status=status,
        calls_with_data=calls_with_data,
        calls_total=calls_total,
    )


@dataclasses.dataclass
class Investigate(BaseNode[State, Dependencies, common.InvestigationReply]):
    """
    Run the Sentinel-native investigator agent to gather evidence (F7).

    Replaces the prior ``InvestigateWithHolmes`` node. The investigator
    is a PydanticAI agent that owns the observability toolset (logs,
    metrics, traces) and reports a structured ``InvestigationFindings`` —
    summary plus per-tool-call ``result_kind``
    (``data`` / ``empty`` / ``error``). The K8s adapter and challenger
    comparison adapter still run as separate paths because they are
    deterministic and Sentinel-owned — they are independent of the
    LLM-driven evidence loop.

    Status semantics passed downstream:

    * ``ran`` — investigator returned analysis OR sources OR data
      tool-calls (or K8s adapter merged real findings).
    * ``empty`` — investigator ran but every tool returned the
      documented empty-result pattern.
    * ``failed`` — investigator raised an exception.
    * ``skipped`` — investigator agent was not registered with
      ``agent_for`` (e.g. config layer hasn't wired it yet).

    ``DetermineConfidence`` reads ``investigation_status`` plus
    ``tool_calls_with_data`` to apply the evidence floor: when the
    investigation produced no real evidence, the analyser's
    self-reported ``relevance`` is capped so confident hallucinations
    cannot promote a no-evidence diagnosis past the approval gate.
    """

    async def run(self, ctx: GraphRunContext[State, Dependencies]) -> AnalyseRootCause:
        async def _impl() -> AnalyseRootCause:
            await ctx.deps.status_update_client.update_status(
                "Investigating with observability tools..."
            )

            outcome = await _run_investigator_agent(ctx=ctx)
            investigation_analysis = outcome.analysis
            investigation_sources = outcome.sources
            investigation_tool_calls = outcome.tool_calls
            investigation_status = outcome.status
            tool_calls_with_data = outcome.calls_with_data
            tool_calls_total = outcome.calls_total

            # 2. Run K8s adapter if configured — merges cluster state.
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
                    k8s_analysis = "\n".join(f.summary for f in k8s_result.findings)
                    if k8s_analysis:
                        investigation_analysis = (
                            (
                                investigation_analysis
                                + "\n\n--- Kubernetes cluster state ---\n"
                                + k8s_analysis
                            )
                            if investigation_analysis
                            else f"--- Kubernetes cluster state ---\n{k8s_analysis}"
                        )
                        investigation_sources.extend(k8s_result.sources_queried)
                        # Each K8s finding counts as a tool-call that returned data.
                        tool_calls_with_data += len(k8s_result.findings)
                        tool_calls_total += len(k8s_result.findings)
                        if investigation_status in ("skipped", "empty"):
                            investigation_status = "ran"
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
                            "node": "Investigate",
                            "k8s_adapter": "failed",
                        },
                    )

            # 3. Run challenger adapter if configured (comparison mode).
            if ctx.deps.challenger_adapter is not None:
                try:
                    challenger_result = await ctx.deps.challenger_adapter.investigate(
                        alert=ctx.state.alert,
                    )
                    baseline_result = adapters.InvestigationResult(
                        findings=tuple(
                            investigation_entities.Finding(source=s, summary="", relevance=0.5)
                            for s in investigation_sources
                        ),
                        sources_queried=tuple(investigation_sources),
                        duration_ms=0,
                        adapter_name="investigator",
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
                            "node": "Investigate",
                            "comparison": "challenger_failed",
                        },
                    )

            logs.log_event(
                "investigator_completed",
                params={
                    "alert_id": ctx.state.alert.id,
                    "sources_queried": investigation_sources,
                    "tool_calls_count": tool_calls_total,
                    "tool_calls_with_data": tool_calls_with_data,
                    "investigation_status": investigation_status,
                },
            )

            return AnalyseRootCause(
                investigation_analysis=investigation_analysis,
                investigation_tool_calls=investigation_tool_calls,
                investigation_sources=investigation_sources,
                investigation_status=investigation_status,
                tool_calls_with_data=tool_calls_with_data,
                tool_calls_total=tool_calls_total,
            )

        return await _node_helpers.run_node_with_envelope(
            pipeline="investigation",
            node="investigate",
            envelope=ctx.state.envelope,
            fn=_impl,
        )


@dataclasses.dataclass
class AnalyseRootCause(BaseNode[State, Dependencies, common.InvestigationReply]):
    """
    Synthesise the investigator's findings into a root cause analysis.

    Pure synthesis — does not gather new evidence. The upstream
    ``Investigate`` node owns the observability tools; the analyser
    receives ``investigation_*`` fields plus ``investigation_status`` and
    tool-call counters that ``DetermineConfidence`` reads to apply the
    evidence floor (no fabricated diagnoses past the approval gate when
    no real investigation occurred).
    """

    investigation_analysis: str = ""
    investigation_tool_calls: list[dict[str, object]] = dataclasses.field(default_factory=list)
    investigation_sources: list[str] = dataclasses.field(default_factory=list)
    # F7: status from the upstream Investigate node — one of ``ran``,
    # ``empty``, ``failed``, ``skipped``. Threaded into the analyser
    # prompt so it knows when the investigation produced no data, and
    # forwarded to DetermineConfidence as the first evidence-floor input.
    investigation_status: str = "ran"
    # F7: count of tool-calls the investigator made and how many returned
    # actual content. ``DetermineConfidence`` caps relevance when both
    # ``investigation_status != "ran"`` and ``tool_calls_with_data == 0``.
    tool_calls_with_data: int = 0
    tool_calls_total: int = 0

    async def run(self, ctx: GraphRunContext[State, Dependencies]) -> DetermineConfidence:
        async def _impl() -> DetermineConfidence:
            await ctx.deps.status_update_client.update_status("Analysing root cause...")

            try:
                analyser_agent = ctx.deps.agent_for("root_cause_analyser")
                agent_utils.set_agent_span_attributes(
                    prompt_sha256=root_cause_analyser.PROMPT_SHA256,
                    model_name=agent_utils.get_model_name(analyser_agent),
                    agent_name="root_cause_analyser",
                )
                result = await analyser_agent.run(
                    user_prompt=f"Analyse this alert: {ctx.state.alert.title}",
                    deps=root_cause_analyser.Dependencies(
                        alert_title=ctx.state.alert.title,
                        alert_description=ctx.state.alert.description,
                        alert_severity=ctx.state.alert.severity.value,
                        investigation_analysis=self.investigation_analysis,
                        investigation_tool_calls=self.investigation_tool_calls,
                        investigation_sources=self.investigation_sources,
                        category=ctx.state.classification_category,
                        runbook=ctx.state.runbook,
                        investigation_status=self.investigation_status,
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
                return DetermineConfidence(
                    raw_confidence=0.0,
                    investigation_status=self.investigation_status,
                    tool_calls_with_data=self.tool_calls_with_data,
                    tool_calls_total=self.tool_calls_total,
                )

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
                    self.investigation_sources, result.output.evidence, strict=False
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

            return DetermineConfidence(
                raw_confidence=result.output.confidence,
                investigation_status=self.investigation_status,
                tool_calls_with_data=self.tool_calls_with_data,
                tool_calls_total=self.tool_calls_total,
            )

        return await _node_helpers.run_node_with_envelope(
            pipeline="investigation",
            node="analyse_root_cause",
            envelope=ctx.state.envelope,
            fn=_impl,
        )


# F7: when no investigation occurred AND the agent made no data-returning
# tool calls, cap the analyser's self-reported relevance at this ceiling.
# Defends against confident hallucinations: an LLM that diagnoses on the
# alert title alone cannot push past Medium confidence and therefore
# cannot bypass the approval gate.
_EVIDENCE_FLOOR_RELEVANCE_CAP = 0.3


@dataclasses.dataclass
class DetermineConfidence(BaseNode[State, Dependencies, common.InvestigationReply]):
    """
    Calculate confidence score using multi-factor analysis.

    Applies the F7 evidence floor before scoring: when the upstream
    investigation produced no real evidence (``investigation_status !=
    "ran"`` and ``tool_calls_with_data == 0``), the analyser's
    self-reported ``raw_confidence`` is clipped to
    ``_EVIDENCE_FLOOR_RELEVANCE_CAP`` before being passed as the
    ``relevance`` factor. This stops eloquent-but-ungrounded LLM output
    from earning a high enough composite score to skip the approval
    gate.
    """

    raw_confidence: float = 0.0
    # F7: evidence-floor inputs threaded from AnalyseRootCause via the
    # upstream Investigate node. See class docstring for semantics.
    investigation_status: str = "ran"
    tool_calls_with_data: int = 0
    tool_calls_total: int = 0

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> PublishFindings | End[common.InvestigationReply]:
        async def _impl() -> PublishFindings | End[common.InvestigationReply]:
            # F7 evidence floor: clip relevance when no real investigation
            # data exists. Logged so the gate decision is auditable.
            no_real_investigation = self.investigation_status != "ran"
            no_tool_evidence = self.tool_calls_with_data == 0
            effective_relevance = self.raw_confidence
            if no_real_investigation and no_tool_evidence:
                effective_relevance = min(self.raw_confidence, _EVIDENCE_FLOOR_RELEVANCE_CAP)
                logs.log_event(
                    "evidence_floor_applied",
                    params={
                        "alert_id": ctx.state.alert.id,
                        "raw_confidence": self.raw_confidence,
                        "capped_relevance": effective_relevance,
                        "investigation_status": self.investigation_status,
                        "tool_calls_total": self.tool_calls_total,
                        "tool_calls_with_data": self.tool_calls_with_data,
                    },
                )

            try:
                findings_count = (
                    len(ctx.state.investigation.findings) if ctx.state.investigation else 0
                )
                confidence = confidence_entities.ConfidenceScore.from_factors(
                    source_count=findings_count,
                    max_expected_sources=5,
                    relevance=effective_relevance,
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
    status_update_client: common.StatusUpdateClient | None = None,
    pagerduty_client: PagerDutyClient | None = None,
    post_to_slack: bool = True,
    persist_fn: common.PersistInvestigationFn | None = None,
    trace_collector: common.TraceCollector | None = None,
    require_approval_below: float = 0.0,
    request_approval_fn: common.RequestApprovalFn | None = None,
    classifier_toolsets: Sequence[AbstractToolset[object]] = (),
    investigator_toolsets: Sequence[AbstractToolset[object]] = (),
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
    :param investigator_toolsets: Toolsets exposed to the F7 Sentinel-native
        investigator agent (logs/metrics/traces). Replaces the prior
        HolmesGPT adapter input.
    """
    state = State(envelope=envelope, alert=alert)
    dependencies = Dependencies(
        status_update_client=status_update_client or common.NoOpStatusUpdateClient(),
        agent_for=agent_for,
        pagerduty_client=pagerduty_client,
        post_to_slack=post_to_slack,
        persist_fn=persist_fn,
        trace_collector=trace_collector,
        require_approval_below=require_approval_below,
        request_approval_fn=request_approval_fn,
        classifier_toolsets=classifier_toolsets,
        investigator_toolsets=investigator_toolsets,
        analyser_toolsets=analyser_toolsets,
        challenger_adapter=challenger_adapter,
        k8s_adapter=k8s_adapter,
    )

    investigation_graph = Graph(
        nodes=(
            ClassifyAlert,
            MatchRunbook,
            Investigate,
            AnalyseRootCause,
            DetermineConfidence,
            PublishFindings,
        ),
    )

    async def _run_graph() -> common.InvestigationReply:
        # F4.7: when the tracer hasn't yet been started by the outer caller
        # (worker/replay/chat), open the replay-capture window here so the
        # bundle's envelope + alert payload are attached. When the worker
        # already started the run, we skip — Slice D will reconcile the
        # worker call site to thread envelope + alert_payload through too.
        tracer = trace_collector
        owns_pipeline = (
            tracer is not None
            and hasattr(tracer, "start_pipeline")
            and getattr(tracer, "pipeline_run_id", None) is None
        )
        if owns_pipeline:
            await tracer.start_pipeline(  # type: ignore[union-attr]
                pipeline_type="investigation",
                input_data={"alert_id": alert.id, "title": alert.title},
                envelope=envelope,
                alert_payload=alert.model_dump(),
            )
        try:
            result = await investigation_graph.run(
                ClassifyAlert(),
                deps=dependencies,
                state=state,
            )
        except Exception:
            if owns_pipeline:
                # Best-effort flush so the ContextVar token is released even on
                # failure. Runbook ids are unknown here — see TODO below.
                await tracer.complete_pipeline(  # type: ignore[union-attr]
                    status="failed",
                    runbook_id=None,
                    runbook_version_sha=None,
                )
            raise
        if owns_pipeline:
            # TODO(f6-runbook-pinning): the SRE pipeline does not yet thread the
            # matched runbook id / version SHA through to completion. Pass None
            # for now; F6 (runbook pinning) wires real values into State so the
            # bundle records "which runbook drove the run" for replay drift.
            await tracer.complete_pipeline(  # type: ignore[union-attr]
                status="completed",
                final_reply=result.output.model_dump(),
                runbook_id=None,
                runbook_version_sha=None,
            )
        return result.output

    return await _node_helpers.run_pipeline_with_envelope(
        pipeline="sre",
        envelope=envelope,
        input_payload=alert.model_dump_json(),
        fn=_run_graph,
        serialize_output=lambda reply: reply.model_dump_json(),
    )
