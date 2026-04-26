"""
TypedDict shape for the LangGraph support-review workflow state.

The dict is the boundary between LangGraph's runtime (which expects
TypedDict states) and the existing pure-Python primitives that flow
through the pipeline. Domain types (``Envelope``, ``Ticket``,
``ResponseSuggestion``, ``ConfidenceScore``, ``DocumentSearchResult``,
``ApprovalDecision``) keep their existing ``attrs.frozen`` / Pydantic
representations -- only the dict shape changes from Pydantic-Graph state
classes to a TypedDict.

Single-writer per field at this stage: each key is written by exactly
one node, so no ``Annotated[..., reducer]`` is required. Reducers can be
introduced later if a node accumulates.

**Design-spec deltas observed during implementation (Phase 2 / T7):**

- ``TicketClassification`` lives in ``interfaces.graphs.agents.ticket_reviewer``
  (the PydanticAI agent factory module that produces it), not in
  ``domain.support`` as the spec example imported it. Workflows are
  permitted to import from ``interfaces.graphs.agents`` per the design.
- The doc-search primitive is ``DocumentSearchResult`` in
  ``domain.search.searcher``, not ``DocSearchResult`` in
  ``domain.support`` as the spec example named it.

Both are name/location corrections only -- the underlying types match
the spec's intent.
"""

from __future__ import annotations

from typing import TypedDict

from sentinel.data.primitives import envelope as envelope_module
from sentinel.domain.approval import entities as approval_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.search import searcher as search_module
from sentinel.domain.support import entities as support_entities
from sentinel.interfaces.graphs.agents import ticket_reviewer


class SupportReviewState(TypedDict):
    """
    State carried through every node of the support-review LangGraph.

    Required at entry (set by the webhook handler before ``ainvoke``):

    - ``envelope``: identity envelope minted at FastAPI ingress.
    - ``ticket``: the inbound Jira ticket payload.

    Progressively written by nodes:

    - ``classification``: filled by the ``classify_ticket`` node.
    - ``doc_results``: filled by ``search_documentation``; an empty
      tuple before that node runs.
    - ``response_suggestion``: filled by ``draft_response``.
    - ``confidence``: filled by ``determine_confidence``.
    - ``needs_approval``: routing flag set by ``determine_confidence``.
    - ``approval_decision``: written by ``wait_for_human`` from the
      ``Command(resume=...)`` payload when an approval is needed.
    """

    envelope: envelope_module.Envelope
    ticket: support_entities.Ticket
    classification: ticket_reviewer.TicketClassification | None
    doc_results: tuple[search_module.DocumentSearchResult, ...]
    response_suggestion: support_entities.ResponseSuggestion | None
    confidence: confidence_entities.ConfidenceScore | None
    needs_approval: bool
    approval_decision: approval_entities.ApprovalDecision | None
