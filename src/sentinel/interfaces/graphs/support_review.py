from __future__ import annotations

import asyncio
import dataclasses

from pydantic_graph import BaseNode, End, Graph, GraphRunContext

from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.search import searcher
from sentinel.domain.support import entities as support_entities
from sentinel.interfaces.graphs import common
from sentinel.interfaces.graphs.agents import response_drafter, ticket_reviewer, utils
from sentinel.utils import logs


@dataclasses.dataclass
class Dependencies:
    status_update_client: common.StatusUpdateClient
    reviewer_model: str
    drafter_model: str
    document_searcher: searcher.BaseDocumentSearcher | None = None
    ticket_searcher: searcher.BasePastTicketSearcher | None = None


@dataclasses.dataclass
class State:
    ticket: support_entities.Ticket


@dataclasses.dataclass
class ClassifyTicket(BaseNode[State, Dependencies, common.SupportReply]):
    """Classify the incoming ticket using a PydanticAI agent."""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> SearchDocumentation:
        await ctx.deps.status_update_client.update_status("Reviewing ticket...")

        result = await ticket_reviewer.agent.run(
            user_prompt=f"Ticket: {ctx.state.ticket.summary}\n\n{ctx.state.ticket.description}",
            model=utils.get_model_with_gateway(ctx.deps.reviewer_model),
            deps=ticket_reviewer.Dependencies(
                ticket_summary=ctx.state.ticket.summary,
                ticket_description=ctx.state.ticket.description,
                ticket_priority=ctx.state.ticket.priority,
                ticket_labels=ctx.state.ticket.labels,
            ),
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


@dataclasses.dataclass
class SearchDocumentation(BaseNode[State, Dependencies, common.SupportReply]):
    """Search documentation sources in parallel."""

    category: str = ""
    key_questions: list[str] = dataclasses.field(default_factory=list)
    search_queries: list[str] = dataclasses.field(default_factory=list)

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> DraftResponse | End[common.SupportReply]:
        await ctx.deps.status_update_client.update_status("Searching documentation...")

        combined_query = " ".join(self.search_queries[:3])

        doc_results: list[searcher.DocumentSearchResult] = []
        ticket_results: list[searcher.TicketSearchResult] = []

        tasks: list[asyncio.Task[object]] = []
        doc_task = None
        ticket_task = None

        async with asyncio.TaskGroup() as tg:
            if ctx.deps.document_searcher:
                doc_task = tg.create_task(
                    ctx.deps.document_searcher.search(query=combined_query, limit=10)
                )
                tasks.append(doc_task)

            if ctx.deps.ticket_searcher:
                ticket_task = tg.create_task(
                    ctx.deps.ticket_searcher.search(query=combined_query, limit=5)
                )
                tasks.append(ticket_task)

        if doc_task:
            doc_results = doc_task.result()
        if ticket_task:
            ticket_results = ticket_task.result()

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
                    "No relevant documentation found for this ticket. "
                    "Manual review recommended."
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


@dataclasses.dataclass
class DraftResponse(BaseNode[State, Dependencies, common.SupportReply]):
    """Draft a response using the gathered documentation."""

    category: str = ""
    key_questions: list[str] = dataclasses.field(default_factory=list)
    document_results: list[searcher.DocumentSearchResult] = dataclasses.field(
        default_factory=list
    )
    ticket_results: list[searcher.TicketSearchResult] = dataclasses.field(
        default_factory=list
    )

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> DetermineConfidence:
        await ctx.deps.status_update_client.update_status("Drafting response...")

        result = await response_drafter.agent.run(
            user_prompt=f"Draft a response for: {ctx.state.ticket.summary}",
            model=utils.get_model_with_gateway(ctx.deps.drafter_model),
            deps=response_drafter.Dependencies(
                ticket_summary=ctx.state.ticket.summary,
                ticket_description=ctx.state.ticket.description,
                ticket_category=self.category,
                key_questions=self.key_questions,
                document_search_results=self.document_results,
                ticket_search_results=self.ticket_results,
            ),
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


@dataclasses.dataclass
class DetermineConfidence(BaseNode[State, Dependencies, common.SupportReply]):
    """Calculate confidence score for the response suggestion."""

    drafted_response: str = ""
    sources_used: list[response_drafter.SourceReference] = dataclasses.field(
        default_factory=list
    )
    raw_confidence: float = 0.0
    category: str = ""
    notes: str = ""

    async def run(
        self, ctx: GraphRunContext[State, Dependencies]
    ) -> End[common.SupportReply]:
        confidence = confidence_entities.ConfidenceScore.from_total(self.raw_confidence)

        sources = [
            {"title": s.title, "url": s.url}
            for s in self.sources_used
        ]

        reply = common.SupportReply(
            ticket_id=ctx.state.ticket.id,
            ticket_key=ctx.state.ticket.key,
            suggested_response=self.drafted_response,
            sources=sources,
            confidence=confidence,
            category=self.category,
        )

        logs.log_event(
            "support_review_completed",
            params={
                "ticket_key": ctx.state.ticket.key,
                "confidence_label": confidence.label.value,
                "confidence_total": confidence.total,
            },
        )

        return End(reply)


async def review_ticket(
    ticket: support_entities.Ticket,
    *,
    document_searcher: searcher.BaseDocumentSearcher | None = None,
    ticket_searcher: searcher.BasePastTicketSearcher | None = None,
    status_update_client: common.StatusUpdateClient | None = None,
    reviewer_model: str = "",
    drafter_model: str = "",
) -> common.SupportReply:
    """
    Run the full support ticket review pipeline.

    This is the main entry point for the support review graph.
    """
    from sentinel import _config

    state = State(ticket=ticket)
    dependencies = Dependencies(
        status_update_client=status_update_client or common.NoOpStatusUpdateClient(),
        reviewer_model=reviewer_model or _config.TICKET_REVIEWER_LLM,
        drafter_model=drafter_model or _config.RESPONSE_DRAFTER_LLM,
        document_searcher=document_searcher,
        ticket_searcher=ticket_searcher,
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
