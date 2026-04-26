"""
LangGraph support-review workflow body.

Five async node functions plus a routing helper compose the support
review pipeline. The graph is the LangGraph-native counterpart to the
legacy Pydantic Graph implementation in
``interfaces/graphs/support_review.py``; only the orchestration glue
changes -- PydanticAI agent factories, the F2 envelope, and the domain
entities cross over unchanged.

Differences from the legacy harness (intentional):

- ``Dependencies`` injection is replaced by ``get_config()`` at the top
  of each node body. Nodes pull agents, searchers, and toolsets directly
  from the singleton.
- ``status_update_client``, ``persist_fn`` and ``trace_collector`` are
  dropped from the in-graph plumbing; LangGraph's runtime instrumentation
  and the entrypoint layer (T15+) own those concerns.
- ``classify_ticket`` and ``draft_response`` re-raise agent failures so
  LangGraph captures the failed step in its checkpoint -- the legacy
  pattern of degrading to ``End(reply)`` conflated pipeline failure with
  happy-path output.
- ``search_documentation`` retains partial degradation: an individual
  searcher failure logs and contributes an empty result list, but the
  node still returns so ``draft_response`` runs with whatever evidence
  the surviving searcher produced.
- A new ``wait_for_human`` node uses ``interrupt()`` to pause the run
  when ``determine_confidence`` flags low confidence; the resume
  payload from ``Command(resume=...)`` is mapped to an
  ``ApprovalDecision`` enum.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from langgraph import graph as lg_graph
from langgraph import types as lg_types
from langgraph.graph import state as lg_state

from sentinel import config as config_mod
from sentinel.domain.approval import entities as approval_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.search import searcher as search_mod
from sentinel.domain.support import entities as support_entities
from sentinel.interfaces.graphs.agents import response_drafter, ticket_reviewer
from sentinel.interfaces.graphs.agents import utils as agent_utils
from sentinel.interfaces.workflows import _envelope as envelope_mod
from sentinel.interfaces.workflows import support_state as support_state_mod
from sentinel.utils import logs, metrics


# Module-local aliases for test patchability per python.md's mocking
# guidance. ``mock.patch.object(support_review_mod, "<name>")`` requires
# the symbol live as a module attribute; rebinding the function here
# keeps the import-modules-not-objects rule at the import site while
# letting tests inject doubles.
get_config = config_mod.get_config
interrupt = lg_types.interrupt


def _render_review_user_prompt(ticket: support_entities.Ticket) -> str:
    """
    Return the user-prompt string for the ticket reviewer agent.

    Mirrors the legacy ``ClassifyTicket`` node prompt construction so
    cached prompt prefixes remain valid across the harness migration.
    """
    return f"Ticket: {ticket.summary}\n\n{ticket.description}"


def _drafter_user_prompt(ticket: support_entities.Ticket) -> str:
    """
    Return the user-prompt string for the response drafter agent.

    Mirrors the legacy ``DraftResponse`` node prompt construction.
    """
    return f"Draft a response for: {ticket.summary}"


def _doc_source_from_reference(
    reference: response_drafter.SourceReference,
) -> support_entities.DocSource:
    """
    Map a drafter ``SourceReference`` onto a domain ``DocSource``.

    The agent only emits ``title`` and ``url``; the additional
    ``DocSource`` fields are filled with neutral defaults so the
    response suggestion is still valid against the Pydantic schema.
    ``source_type`` defaults to ``"confluence"`` because that is the
    most common surface in production today; downstream UI displays
    the title/url first regardless.
    """
    return support_entities.DocSource(
        title=reference.title,
        url=reference.url,
        source_type="confluence",
        excerpt="",
        relevance=0.0,
    )


# ---------------------------------------------------------------------------
# T8 -- classify_ticket
# ---------------------------------------------------------------------------


async def classify_ticket(state: support_state_mod.SupportReviewState) -> dict[str, Any]:
    """
    Run the ticket reviewer agent and return the classification.

    Reads ``state["ticket"]``, invokes the configured PydanticAI
    ``ticket_reviewer`` agent, and returns a partial-state dict
    keyed by ``classification``. Agent failures propagate so
    LangGraph's runtime records the failed step.
    """
    config = get_config()
    ticket = state["ticket"]

    reviewer_agent = config.agent_for("ticket_reviewer")
    triage_toolset = config.build_ticket_triage_toolset()
    try:
        result = await reviewer_agent.run(
            user_prompt=_render_review_user_prompt(ticket),
            deps=ticket_reviewer.Dependencies(
                ticket_summary=ticket.summary,
                ticket_description=ticket.description,
                ticket_priority=ticket.priority,
                ticket_labels=ticket.labels,
            ),
            toolsets=[triage_toolset] if triage_toolset is not None else None,
            model_settings=agent_utils.build_cache_settings(
                model_name=agent_utils.get_model_name(reviewer_agent),
                prompt_sha256=ticket_reviewer.PROMPT_SHA256,
            ),
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"ticket_key": ticket.key, "node": "classify_ticket"},
        )
        raise

    classification: ticket_reviewer.TicketClassification = result.output
    logs.log_event(
        "ticket_classified",
        params={
            "ticket_key": ticket.key,
            "category": classification.category,
            "urgency": classification.urgency,
        },
    )
    return {"classification": classification}


# ---------------------------------------------------------------------------
# T9 -- search_documentation
# ---------------------------------------------------------------------------


async def search_documentation(
    state: support_state_mod.SupportReviewState,
) -> dict[str, Any]:
    """
    Search documentation and past-ticket sources in parallel.

    Reads ``state["classification"].search_queries``, runs the
    configured document and ticket searchers concurrently, and returns
    a partial-state dict keyed by ``doc_results`` and
    ``ticket_results``. Individual searcher failures degrade to empty
    result lists so ``draft_response`` can still proceed with whatever
    evidence the surviving searcher produced.
    """
    config = get_config()
    ticket = state["ticket"]
    classification = state["classification"]
    queries = classification.search_queries if classification is not None else []
    combined_query = " ".join(queries[:3])

    document_searcher = config.build_document_searcher()
    ticket_searcher = config.build_ticket_searcher()

    # Run both searchers concurrently. The per-helper try/except handles
    # individual searcher failures so TaskGroup never sees an exception
    # propagate -- partial degradation is the design intent here so
    # ``draft_response`` still has whichever evidence survived.
    async with asyncio.TaskGroup() as task_group:
        doc_task = task_group.create_task(
            _run_doc_search(
                searcher=document_searcher,
                query=combined_query,
                ticket_key=ticket.key,
            ),
        )
        ticket_task = task_group.create_task(
            _run_ticket_search(
                searcher=ticket_searcher,
                query=combined_query,
                ticket_key=ticket.key,
            ),
        )
    doc_results = doc_task.result()
    ticket_results = ticket_task.result()

    logs.log_event(
        "documentation_searched",
        params={
            "ticket_key": ticket.key,
            "doc_results_count": len(doc_results),
            "ticket_results_count": len(ticket_results),
        },
    )
    return {
        "doc_results": tuple(doc_results),
        "ticket_results": tuple(ticket_results),
    }


async def _run_doc_search(
    *,
    searcher: search_mod.BaseDocumentSearcher | None,
    query: str,
    ticket_key: str,
) -> list[search_mod.DocumentSearchResult]:
    """Run a document search guarded against searcher exceptions."""
    if searcher is None:
        return []
    try:
        return await searcher.search(query=query, limit=10)
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"ticket_key": ticket_key, "searcher": "document"},
        )
        return []


async def _run_ticket_search(
    *,
    searcher: search_mod.BasePastTicketSearcher | None,
    query: str,
    ticket_key: str,
) -> list[search_mod.TicketSearchResult]:
    """Run a past-ticket search guarded against searcher exceptions."""
    if searcher is None:
        return []
    try:
        return await searcher.search(query=query, limit=5)
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"ticket_key": ticket_key, "searcher": "ticket"},
        )
        return []


# ---------------------------------------------------------------------------
# T10 -- draft_response
# ---------------------------------------------------------------------------


async def draft_response(state: support_state_mod.SupportReviewState) -> dict[str, Any]:
    """
    Draft a response suggestion using the configured drafter agent.

    Reads ``state["ticket"]``, ``state["classification"]``,
    ``state["doc_results"]`` and ``state["ticket_results"]``; invokes
    the PydanticAI ``response_drafter`` agent; converts its
    ``DraftedResponse`` into a domain ``ResponseSuggestion`` carrying
    the agent's raw confidence on the suggestion's
    ``confidence_score`` field.
    """
    config = get_config()
    ticket = state["ticket"]
    classification = state["classification"]
    doc_results = state.get("doc_results", ())
    ticket_results = state.get("ticket_results", ())

    drafter_agent = config.agent_for("response_drafter")
    support_search_toolset = config.build_support_search_toolset()
    try:
        result = await drafter_agent.run(
            user_prompt=_drafter_user_prompt(ticket),
            deps=response_drafter.Dependencies(
                ticket_summary=ticket.summary,
                ticket_description=ticket.description,
                ticket_category=classification.category if classification else "",
                key_questions=list(classification.key_questions) if classification else [],
                document_search_results=list(doc_results),
                ticket_search_results=list(ticket_results),
            ),
            toolsets=[support_search_toolset] if support_search_toolset is not None else None,
            model_settings=agent_utils.build_cache_settings(
                model_name=agent_utils.get_model_name(drafter_agent),
                prompt_sha256=response_drafter.PROMPT_SHA256,
            ),
        )
    except Exception as exc:
        logs.log_exception(
            exc,
            params={"ticket_key": ticket.key, "node": "draft_response"},
        )
        raise

    drafted: response_drafter.DraftedResponse = result.output
    suggestion = support_entities.ResponseSuggestion(
        ticket_id=ticket.id,
        suggested_response=drafted.response,
        sources=[_doc_source_from_reference(reference) for reference in drafted.sources_used],
        confidence_score=drafted.confidence,
        category=classification.category if classification else None,
    )

    logs.log_event(
        "response_drafted",
        params={
            "ticket_key": ticket.key,
            "confidence": drafted.confidence,
            "sources_count": len(drafted.sources_used),
        },
    )
    return {"response_suggestion": suggestion}


# ---------------------------------------------------------------------------
# T11 -- determine_confidence
# ---------------------------------------------------------------------------


async def determine_confidence(
    state: support_state_mod.SupportReviewState,
) -> dict[str, Any]:
    """
    Compute the confidence score and the approval-gate routing flag.

    Combines the drafter's raw confidence with source count and a
    fixed recency factor through ``ConfidenceScore.from_factors``,
    then sets ``needs_approval`` against the
    ``require_approval_below_confidence`` threshold from config.
    """
    config = get_config()
    ticket = state["ticket"]
    suggestion = state["response_suggestion"]
    if suggestion is None:
        msg = "determine_confidence requires response_suggestion in state"
        raise ValueError(msg)

    relevance = suggestion.confidence_score if suggestion.confidence_score is not None else 0.0
    confidence = confidence_entities.ConfidenceScore.from_factors(
        source_count=len(suggestion.sources),
        max_expected_sources=5,
        relevance=relevance,
        recency=0.7,
    )
    threshold = config.require_approval_below_confidence
    needs_approval = confidence.total < threshold

    logs.log_event(
        "support_review_completed",
        params={
            "ticket_key": ticket.key,
            "confidence_label": confidence.label.value,
            "confidence_total": confidence.total,
            "needs_approval": needs_approval,
        },
    )
    metrics.record_confidence_score(pipeline="support", score=confidence.total)
    metrics.record_review_completed(
        confidence_label=confidence.label.value,
        outcome="completed",
    )

    return {"confidence": confidence, "needs_approval": needs_approval}


# ---------------------------------------------------------------------------
# T12 -- wait_for_human
# ---------------------------------------------------------------------------


async def wait_for_human(state: support_state_mod.SupportReviewState) -> dict[str, Any]:
    """
    Pause the workflow at the approval gate via ``interrupt()``.

    LangGraph's ``interrupt`` raises ``GraphInterrupt`` on first
    invocation; on resume the entire node body re-executes and
    ``interrupt`` returns the ``Command(resume=...)`` payload. This
    node maps the resume payload onto an ``ApprovalDecision`` enum so
    the rest of the application sees a typed value rather than a
    transport-shaped dict.
    """
    suggestion = state["response_suggestion"]
    confidence = state["confidence"]
    if suggestion is None or confidence is None:
        msg = "wait_for_human requires response_suggestion and confidence in state"
        raise ValueError(msg)

    payload = {
        "action": "approve_response_suggestion",
        "request_id": str(state["envelope"].request_id),
        "suggestion_id": str(suggestion.id),
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
    transport contract is intentionally narrow: approval endpoints
    in T18 own the JSON body shape and validate it before resuming
    the graph.
    """
    if isinstance(resume_payload, dict) and resume_payload.get("approved"):
        return approval_entities.ApprovalDecision.APPROVED
    return approval_entities.ApprovalDecision.REJECTED


# ---------------------------------------------------------------------------
# T13 -- routing
# ---------------------------------------------------------------------------


def _route_after_confidence(state: support_state_mod.SupportReviewState) -> str:
    """
    Route either to the approval gate or straight to ``END``.

    Inspects ``state["needs_approval"]`` (set by ``determine_confidence``)
    and picks the next node accordingly. Returning the LangGraph
    sentinel ``END`` rather than a string literal keeps the routing
    dictionary in lock-step with the graph builder.
    """
    if state["needs_approval"]:
        return "wait_for_human"
    return lg_graph.END


# ---------------------------------------------------------------------------
# T14 -- graph builder
# ---------------------------------------------------------------------------


def build_support_review_graph(
    *,
    checkpointer: Any,
) -> lg_state.CompiledStateGraph[Any, Any, Any, Any]:
    """
    Compose the support-review ``StateGraph`` and return the compiled graph.

    Every node is wrapped in :func:`with_envelope` so the F2 envelope
    is bound to structlog and OTel for the duration of the node body.
    The conditional edge from ``determine_confidence`` either pauses
    at ``wait_for_human`` or terminates at ``END``.

    :param checkpointer: A LangGraph checkpointer (typically the
        ``AsyncPostgresSaver`` returned by
        :func:`sentinel.interfaces.workflows._checkpointer.build_checkpointer`).
        Supplied by the FastAPI lifespan in T15.
    """
    builder: lg_graph.StateGraph[Any, Any, Any, Any] = lg_graph.StateGraph(
        support_state_mod.SupportReviewState,
    )

    # ``add_node`` is overloaded against LangGraph's internal ``_Node``
    # callable union. Mypy cannot narrow our generic ``with_envelope``
    # return type onto any single overload because ``StateT`` resolves
    # to a TypedDict that mypy reports as not assignable to
    # ``TypedDictLikeV1 | DataclassLike | BaseModel`` even though it
    # actually satisfies ``TypedDictLikeV2`` at runtime. Cast each
    # wrapped node to ``Any`` at the boundary -- the call sites are
    # exhaustively covered by ``test_support_graph.py`` so a regression
    # in the wiring would surface as a unit-test failure, not a
    # silent runtime bug.
    builder.add_node("classify_ticket", cast("Any", envelope_mod.with_envelope(classify_ticket)))
    builder.add_node(
        "search_documentation",
        cast("Any", envelope_mod.with_envelope(search_documentation)),
    )
    builder.add_node("draft_response", cast("Any", envelope_mod.with_envelope(draft_response)))
    builder.add_node(
        "determine_confidence",
        cast("Any", envelope_mod.with_envelope(determine_confidence)),
    )
    builder.add_node("wait_for_human", cast("Any", envelope_mod.with_envelope(wait_for_human)))

    builder.add_edge(lg_graph.START, "classify_ticket")
    builder.add_edge("classify_ticket", "search_documentation")
    builder.add_edge("search_documentation", "draft_response")
    builder.add_edge("draft_response", "determine_confidence")
    # ``path_map`` enumerates both possible targets so the static
    # graph view (used by visualisation tooling and our
    # ``test_branches_after_determine_confidence`` shape check)
    # records both edges, not just whichever target the path
    # function happens to return at compile time.
    builder.add_conditional_edges(
        "determine_confidence",
        _route_after_confidence,
        path_map={"wait_for_human": "wait_for_human", lg_graph.END: lg_graph.END},
    )
    builder.add_edge("wait_for_human", lg_graph.END)

    return builder.compile(checkpointer=checkpointer)
