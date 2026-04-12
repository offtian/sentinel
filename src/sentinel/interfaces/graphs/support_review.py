from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Sequence
from typing import Any

from pydantic_ai.toolsets import AbstractToolset
from pydantic_graph import BaseNode, End, Graph, GraphRunContext

from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.search import searcher
from sentinel.domain.support import entities as support_entities
from sentinel.interfaces.graphs import common
from sentinel.interfaces.graphs._node_helpers import instrumented_node_run
from sentinel.interfaces.graphs.agents import response_drafter, ticket_reviewer
from sentinel.interfaces.graphs.agents import utils as agent_utils
from sentinel.utils import logs, metrics


@dataclasses.dataclass
class Dependencies:
    status_update_client: common.StatusUpdateClient
    agent_for: Callable[[str], Any]
    document_searcher: searcher.BaseDocumentSearcher | None = None
    ticket_searcher: searcher.BasePastTicketSearcher | None = None
    persist_fn: common.PersistTicketReviewFn | None = None
    trace_collector: common.TraceCollector | None = None
    # Toolsets injected at agent.run() time.  Built by config.py.
    reviewer_toolsets: Sequence[AbstractToolset[object]] = ()
    drafter_toolsets: Sequence[AbstractToolset[object]] = ()


@dataclasses.dataclass
class State:
    ticket: support_entities.Ticket


@dataclasses.dataclass
class ClassifyTicket(BaseNode[State, Dependencies, common.SupportReply]):
    """Classify the incoming ticket using a PydanticAI agent."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> SearchDocumentation | End[common.SupportReply]:
        async def _impl() -> SearchDocumentation | End[common.SupportReply]:
            await ctx.deps.status_update_client.update_status("Reviewing ticket...")

            try:
                reviewer_agent = ctx.deps.agent_for("ticket_reviewer")
                result = await reviewer_agent.run(
                    user_prompt=f"Ticket: {ctx.state.ticket.summary}\n\n{ctx.state.ticket.description}",
                    deps=ticket_reviewer.Dependencies(
                        ticket_summary=ctx.state.ticket.summary,
                        ticket_description=ctx.state.ticket.description,
                        ticket_priority=ctx.state.ticket.priority,
                        ticket_labels=ctx.state.ticket.labels,
                    ),
                    toolsets=list(ctx.deps.reviewer_toolsets) or None,
                    model_settings=agent_utils.build_cache_settings(
                        model_name=agent_utils.get_model_name(reviewer_agent),
                        prompt_sha256=ticket_reviewer.PROMPT_SHA256,
                    ),
                )
            except Exception as exc:
                logs.log_exception(
                    exc,
                    params={"ticket_key": ctx.state.ticket.key, "node": "ClassifyTicket"},
                )
                return End(
                    common.SupportReply(
                        ticket_id=ctx.state.ticket.id,
                        ticket_key=ctx.state.ticket.key,
                        suggested_response=(
                            f"Classification failed: {type(exc).__name__} — {exc}. "
                            "Manual review required."
                        ),
                    )
                )

            if ctx.deps.trace_collector:
                ctx.deps.trace_collector.record(
                    agent_name="Ticket Reviewer",
                    messages=result.all_messages(),
                )

            logs.log_event(
                "ticket_classified",
                params={
                    "ticket_key": ctx.state.ticket.key,
                    "category": result.output.category,
                    "urgency": result.output.urgency,
                },
            )

            return SearchDocumentation(
                category=result.output.category,
                key_questions=result.output.key_questions,
                search_queries=result.output.search_queries,
            )

        return await instrumented_node_run(
            pipeline="support",
            node="classify_ticket",
            fn=_impl,
        )()


@dataclasses.dataclass
class SearchDocumentation(BaseNode[State, Dependencies, common.SupportReply]):
    """Search documentation sources in parallel."""

    category: str = ""
    key_questions: list[str] = dataclasses.field(default_factory=list)
    search_queries: list[str] = dataclasses.field(default_factory=list)

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> DraftResponse | End[common.SupportReply]:
        async def _impl() -> DraftResponse | End[common.SupportReply]:
            await ctx.deps.status_update_client.update_status("Searching documentation...")

            combined_query = " ".join(self.search_queries[:3])

            doc_results: list[searcher.DocumentSearchResult] = []
            ticket_results: list[searcher.TicketSearchResult] = []

            try:
                doc_task = None
                ticket_task = None

                async with asyncio.TaskGroup() as tg:
                    if ctx.deps.document_searcher:
                        doc_task = tg.create_task(
                            ctx.deps.document_searcher.search(query=combined_query, limit=10)
                        )

                    if ctx.deps.ticket_searcher:
                        ticket_task = tg.create_task(
                            ctx.deps.ticket_searcher.search(query=combined_query, limit=5)
                        )

                if doc_task:
                    doc_results = doc_task.result()
                if ticket_task:
                    ticket_results = ticket_task.result()
            except Exception as exc:
                logs.log_exception(
                    exc,
                    params={"ticket_key": ctx.state.ticket.key, "node": "SearchDocumentation"},
                )

            logs.log_event(
                "documentation_searched",
                params={
                    "ticket_key": ctx.state.ticket.key,
                    "doc_results_count": len(doc_results),
                    "ticket_results_count": len(ticket_results),
                },
            )

            if not doc_results and not ticket_results:
                reply = common.SupportReply(
                    ticket_id=ctx.state.ticket.id,
                    ticket_key=ctx.state.ticket.key,
                    suggested_response=(
                        "No relevant documentation found for this ticket. Manual review recommended."
                    ),
                    category=self.category,
                )
                return End(reply)

            return DraftResponse(
                category=self.category,
                key_questions=self.key_questions,
                document_results=doc_results,
                ticket_results=ticket_results,
            )

        return await instrumented_node_run(
            pipeline="support",
            node="search_documentation",
            fn=_impl,
        )()


@dataclasses.dataclass
class DraftResponse(BaseNode[State, Dependencies, common.SupportReply]):
    """Draft a response using the gathered documentation."""

    category: str = ""
    key_questions: list[str] = dataclasses.field(default_factory=list)
    document_results: list[searcher.DocumentSearchResult] = dataclasses.field(default_factory=list)
    ticket_results: list[searcher.TicketSearchResult] = dataclasses.field(default_factory=list)

    async def run(self, ctx: GraphRunContext[State, Dependencies]) -> DetermineConfidence:
        async def _impl() -> DetermineConfidence:
            await ctx.deps.status_update_client.update_status("Drafting response...")

            try:
                drafter_agent = ctx.deps.agent_for("response_drafter")
                result = await drafter_agent.run(
                    user_prompt=f"Draft a response for: {ctx.state.ticket.summary}",
                    deps=response_drafter.Dependencies(
                        ticket_summary=ctx.state.ticket.summary,
                        ticket_description=ctx.state.ticket.description,
                        ticket_category=self.category,
                        key_questions=self.key_questions,
                        document_search_results=self.document_results,
                        ticket_search_results=self.ticket_results,
                    ),
                    toolsets=list(ctx.deps.drafter_toolsets) or None,
                    model_settings=agent_utils.build_cache_settings(
                        model_name=agent_utils.get_model_name(drafter_agent),
                        prompt_sha256=response_drafter.PROMPT_SHA256,
                    ),
                )
            except Exception as exc:
                logs.log_exception(
                    exc,
                    params={"ticket_key": ctx.state.ticket.key, "node": "DraftResponse"},
                )
                return DetermineConfidence(
                    drafted_response=(
                        "Response drafting failed due to an internal error. "
                        "Please review this ticket manually. "
                        f"Documentation was found for: {', '.join(q[:50] for q in self.key_questions[:3])}"
                    ),
                    sources_used=[],
                    raw_confidence=0.0,
                    category=self.category,
                    notes="Automated drafting failed — manual review required.",
                )

            if ctx.deps.trace_collector:
                ctx.deps.trace_collector.record(
                    agent_name="Response Drafter",
                    messages=result.all_messages(),
                )

            logs.log_event(
                "response_drafted",
                params={
                    "ticket_key": ctx.state.ticket.key,
                    "confidence": result.output.confidence,
                    "sources_count": len(result.output.sources_used),
                },
            )

            return DetermineConfidence(
                drafted_response=result.output.response,
                sources_used=result.output.sources_used,
                raw_confidence=result.output.confidence,
                category=self.category,
                notes=result.output.notes_for_agent,
            )

        return await instrumented_node_run(
            pipeline="support",
            node="draft_response",
            fn=_impl,
        )()


@dataclasses.dataclass
class DetermineConfidence(BaseNode[State, Dependencies, common.SupportReply]):
    """Calculate confidence score for the response suggestion."""

    drafted_response: str = ""
    sources_used: list[response_drafter.SourceReference] = dataclasses.field(default_factory=list)
    raw_confidence: float = 0.0
    category: str = ""
    notes: str = ""

    async def run(self, ctx: GraphRunContext[State, Dependencies]) -> End[common.SupportReply]:
        async def _impl() -> End[common.SupportReply]:
            confidence = confidence_entities.ConfidenceScore.from_factors(
                source_count=len(self.sources_used),
                max_expected_sources=5,
                relevance=self.raw_confidence,
                recency=0.7,
            )

            sources = [{"title": s.title, "url": s.url} for s in self.sources_used]

            reply = common.SupportReply(
                ticket_id=ctx.state.ticket.id,
                ticket_key=ctx.state.ticket.key,
                suggested_response=self.drafted_response,
                sources=sources,
                confidence=confidence,
                category=self.category,
            )

            if ctx.deps.persist_fn:
                await ctx.deps.persist_fn(reply)

            logs.log_event(
                "support_review_completed",
                params={
                    "ticket_key": ctx.state.ticket.key,
                    "confidence_label": confidence.label.value,
                    "confidence_total": confidence.total,
                },
            )

            metrics.record_confidence_score(
                pipeline="support",
                score=confidence.total,
            )
            metrics.record_review_completed(
                confidence_label=confidence.label.value
                if hasattr(confidence.label, "value")
                else str(confidence.label),
                outcome="completed",
            )

            return End(reply)

        return await instrumented_node_run(
            pipeline="support",
            node="determine_confidence",
            fn=_impl,
        )()


async def review_ticket(
    ticket: support_entities.Ticket,
    *,
    agent_for: Callable[[str], Any],
    document_searcher: searcher.BaseDocumentSearcher | None = None,
    ticket_searcher: searcher.BasePastTicketSearcher | None = None,
    status_update_client: common.StatusUpdateClient | None = None,
    persist_fn: common.PersistTicketReviewFn | None = None,
    trace_collector: common.TraceCollector | None = None,
    reviewer_toolsets: Sequence[AbstractToolset[object]] = (),
    drafter_toolsets: Sequence[AbstractToolset[object]] = (),
) -> common.SupportReply:
    """
    Run the full support ticket review pipeline.

    This is the main entry point for the support review graph.
    """
    state = State(ticket=ticket)
    dependencies = Dependencies(
        status_update_client=status_update_client or common.NoOpStatusUpdateClient(),
        agent_for=agent_for,
        document_searcher=document_searcher,
        ticket_searcher=ticket_searcher,
        persist_fn=persist_fn,
        trace_collector=trace_collector,
        reviewer_toolsets=reviewer_toolsets,
        drafter_toolsets=drafter_toolsets,
    )

    review_graph = Graph(
        nodes=(ClassifyTicket, SearchDocumentation, DraftResponse, DetermineConfidence),
    )

    result = await review_graph.run(
        ClassifyTicket(),
        deps=dependencies,
        state=state,
    )
    return result.output
