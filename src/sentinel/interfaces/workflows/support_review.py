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
import uuid
from typing import Any, cast

import attrs
from langgraph import graph as lg_graph
from langgraph import types as lg_types
from langgraph.graph import state as lg_state

from sentinel import config as config_mod
from sentinel.data.primitives import envelope as envelope_module
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


# ---------------------------------------------------------------------------
# T16 -- review_ticket / resume_review entrypoints
# ---------------------------------------------------------------------------


@attrs.frozen(kw_only=True)
class ReviewOutcome:
    """
    Result of a single :func:`review_ticket` or :func:`resume_review` run.

    The fields below mirror the LangGraph state slots populated by the
    pipeline. ``response_suggestion`` and ``confidence`` are ``None`` when
    the workflow paused before those nodes executed (it currently cannot,
    but the typing leaves room for future paused-points).
    ``interrupt_payload`` is set to the ``wait_for_human`` interrupt body
    when the run paused at the approval gate, ``None`` once the run has
    completed all the way to ``END``. ``approval_decision`` is populated
    after :func:`resume_review` runs the approval gate to completion.
    """

    request_id: uuid.UUID
    response_suggestion: support_entities.ResponseSuggestion | None
    confidence: confidence_entities.ConfidenceScore | None
    needs_approval: bool
    interrupt_payload: dict[str, Any] | None
    approval_decision: approval_entities.ApprovalDecision | None


def _outcome_from_state(state: dict[str, Any], request_id: uuid.UUID) -> ReviewOutcome:
    """
    Map a LangGraph ``ainvoke`` return value onto a :class:`ReviewOutcome`.

    LangGraph surfaces a paused run by including an ``__interrupt__`` key
    in the returned state whose value is a tuple of ``Interrupt`` objects.
    Each interrupt's ``.value`` carries the JSON-shaped payload the node
    passed to :func:`langgraph.types.interrupt`. We surface the first
    payload because the support graph only ever has one paused node
    (``wait_for_human``).
    """
    interrupts = state.get("__interrupt__")
    interrupt_payload: dict[str, Any] | None = None
    if interrupts:
        interrupt_payload = interrupts[0].value

    return ReviewOutcome(
        request_id=request_id,
        response_suggestion=state.get("response_suggestion"),
        confidence=state.get("confidence"),
        needs_approval=bool(state.get("needs_approval")),
        interrupt_payload=interrupt_payload,
        approval_decision=state.get("approval_decision"),
    )


def _seed_state(
    *,
    ticket: support_entities.Ticket,
    envelope: envelope_module.Envelope,
) -> support_state_mod.SupportReviewState:
    """
    Return the initial ``SupportReviewState`` for a fresh run.

    Every node-output slot starts at its empty value -- ``None`` for
    optional payloads, ``()`` for tuple slots, ``False`` for the
    ``needs_approval`` flag -- so the TypedDict shape matches what
    LangGraph's checkpointer persists between steps.
    """
    return {
        "envelope": envelope,
        "ticket": ticket,
        "classification": None,
        "doc_results": (),
        "ticket_results": (),
        "response_suggestion": None,
        "confidence": None,
        "needs_approval": False,
        "approval_decision": None,
    }


def _thread_config(request_id: uuid.UUID) -> dict[str, Any]:
    """Return the LangGraph runtime config keyed by ``request_id``."""
    return {"configurable": {"thread_id": str(request_id)}}


async def review_ticket(
    *,
    ticket: support_entities.Ticket,
    envelope: envelope_module.Envelope,
    graph: lg_state.CompiledStateGraph[Any, Any, Any, Any],
) -> ReviewOutcome:
    """
    Run the support-review workflow for a single ticket.

    Wraps ``graph.ainvoke`` with the seeded TypedDict state and a config
    keyed by ``thread_id = str(envelope.request_id)`` so a paused run
    can be resumed later via :func:`resume_review` against the same
    request_id.

    :param ticket: Inbound support ticket to review.
    :param envelope: Identity envelope minted at FastAPI ingress.
    :param graph: The compiled support-review graph (typically read off
        ``app.state.support_review_graph`` by the webhook handler).
    """
    # ``ainvoke`` is overloaded against LangGraph's stream-mode literals;
    # mypy cannot match a runtime dict against the ``RunnableConfig``
    # overload arms. The legacy graph builder uses the same ``cast`` to
    # ``Any`` at the boundary -- the call sites are exhaustively covered
    # by ``test_support_entrypoint.py``.
    state = await cast("Any", graph).ainvoke(
        _seed_state(ticket=ticket, envelope=envelope),
        config=_thread_config(envelope.request_id),
    )
    return _outcome_from_state(state, envelope.request_id)


async def resume_review(
    *,
    request_id: uuid.UUID,
    decision: approval_entities.ApprovalDecision,
    graph: lg_state.CompiledStateGraph[Any, Any, Any, Any],
    approver: str | None = None,
    reason: str | None = None,
) -> ReviewOutcome:
    """
    Resume a paused support-review workflow with an approval decision.

    Builds a ``Command(resume=...)`` payload whose ``approved`` flag is
    ``True`` for :attr:`ApprovalDecision.APPROVED` and ``False`` for
    every other value (currently only ``REJECTED`` reaches this path).
    The optional ``approver`` and ``reason`` strings ride along for the
    audit trail when supplied.
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


@attrs.frozen(kw_only=True)
class ReviewStatus:
    """
    Snapshot of a review thread's checkpointed state.

    ``status`` is one of ``"pending"`` (paused at the approval gate),
    ``"approved"`` / ``"rejected"`` (resumed and recorded the
    decision), or ``"completed"`` (ran to ``END`` without ever needing
    approval). ``approval_decision`` mirrors the decision when one has
    been recorded, otherwise ``None``.
    """

    request_id: uuid.UUID
    status: str
    needs_approval: bool
    approval_decision: approval_entities.ApprovalDecision | None


async def get_review_status(
    *,
    request_id: uuid.UUID,
    graph: lg_state.CompiledStateGraph[Any, Any, Any, Any],
) -> ReviewStatus | None:
    """
    Return the current status of a review thread, or ``None`` when no
    checkpoint exists for the supplied ``request_id``.

    Reads the thread state via ``graph.aget_state(...)``; LangGraph's
    saver returns an empty ``values`` dict for a thread it has never
    seen, which the helper maps onto ``None`` so callers can return
    HTTP 404 from a single check.
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
    return ReviewStatus(
        request_id=request_id,
        status=status,
        needs_approval=bool(values.get("needs_approval")),
        approval_decision=decision,
    )
