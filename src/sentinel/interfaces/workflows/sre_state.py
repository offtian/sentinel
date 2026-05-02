"""
TypedDict shape for the LangGraph SRE investigation workflow state.

The dict is the boundary between LangGraph's runtime (which expects
TypedDict states) and the existing pure-Python primitives that flow
through the pipeline. Domain types (``Envelope``, ``Alert``,
``Investigation``, ``Runbook``, ``RunbookMatch``, ``ConfidenceScore``,
``ApprovalDecision``) keep their existing ``attrs.frozen`` / Pydantic
representations -- only the dict shape changes from Pydantic-Graph state
classes to a TypedDict.

Single-writer per field at this stage: each key is written by exactly
one node, so no ``Annotated[..., reducer]`` is required. Reducers can be
introduced later if a node accumulates (e.g. a fan-out investigation step).

**State lifecycle:**

- ``envelope`` and ``alert`` are set at entry by the worker / entrypoint
  before ``graph.ainvoke`` is called.
- ``classification_category``, ``requires_approval``, and ``investigation``
  are initialised by ``classify_alert``.
- ``runbook``, ``runbook_match``, ``runbook_match_id``, ``requires_approval``
  are updated by ``match_runbook`` (may override ``requires_approval`` set
  by ``classify_alert``).
- ``investigation`` is updated in-place by both ``investigate`` and
  ``analyse_root_cause`` via ``model_copy``.
- ``confidence`` and ``needs_approval`` are written by
  ``determine_confidence``.
- ``approval_decision`` is written by ``wait_for_human`` from the
  ``Command(resume=...)`` payload.
- ``findings_published`` is set to ``True`` by ``publish_findings``.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.approval import entities as approval_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.investigations import entities as investigation_entities
from sentinel.domain.quality import groundedness as groundedness_mod
from sentinel.domain.runbooks import models as runbook_models


class InvestigationState(TypedDict, total=False):
    """
    State carried through every node of the SRE investigation LangGraph.

    Required at entry (set by the worker / entrypoint before ``ainvoke``):

    - ``envelope``: identity envelope minted at FastAPI ingress.
    - ``alert``: the inbound alert payload (PagerDuty / Datadog).

    Progressively written by nodes:

    - ``classification_category``: filled by ``classify_alert``.
    - ``runbook``: filled by ``match_runbook`` on success; ``None`` on
      no-match.
    - ``runbook_match``: full ``RunbookMatch`` for audit / approval
      cross-reference; written by ``match_runbook``.
    - ``runbook_match_id``: UUID of the persisted runbook-match audit row;
      written by ``match_runbook`` when DB is wired.
    - ``requires_approval``: ``True`` iff the runbook matcher returned a
      no-match result. Written by ``match_runbook`` (and initialised to
      ``False`` by ``classify_alert``). The ``determine_confidence`` node
      ANDs this with the confidence threshold so generic-playbook
      fallbacks always land in the approval queue.
    - ``investigation``: created by ``classify_alert``, updated with
      analysis by ``investigate``, updated with root cause by
      ``analyse_root_cause``.
    - ``_investigation_context``: internal side-channel written by
      ``investigate`` and read/extended by ``analyse_root_cause`` and
      ``determine_confidence``. Carries investigation status, tool call
      counts, and the raw LLM-reported confidence used for the evidence
      floor. Must be declared in the TypedDict so LangGraph persists it
      between node checkpoints — otherwise it is silently dropped and
      ``determine_confidence`` scores against empty evidence.
    - ``confidence``: ``ConfidenceScore`` written by
      ``determine_confidence``.
    - ``needs_approval``: routing flag set by ``determine_confidence``;
      ``True`` iff ``confidence.total < threshold`` or
      ``requires_approval``.
    - ``approval_decision``: written by ``wait_for_human`` from the
      ``Command(resume=...)`` payload when an approval is required.
    - ``findings_published``: set to ``True`` by ``publish_findings``
      after a successful Slack + PagerDuty post.
    """

    envelope: envelope_mod.Envelope
    alert: alert_entities.Alert
    classification_category: str
    runbook: runbook_models.Runbook | None
    runbook_match: runbook_models.RunbookMatch | None
    runbook_match_id: uuid.UUID | None
    requires_approval: bool
    investigation: investigation_entities.Investigation | None
    # Internal side-channel: persisted between investigate → analyse_root_cause
    # → determine_confidence so the evidence floor and confidence scoring have
    # access to tool call counts and raw LLM confidence across checkpoints.
    _investigation_context: dict[str, Any]
    quality_verdict: groundedness_mod.GroundednessVerdict | None
    confidence: confidence_entities.ConfidenceScore | None
    needs_approval: bool
    approval_decision: approval_entities.ApprovalDecision | None
    findings_published: bool
