"""
Runbook match + feedback persistence (F6.F.1 / F6.M).

Domain-layer queries and writes for the F6 runbook audit trail. Lives in
``domain/runbooks/`` (not ``data/``) because the row-shape decisions
(``candidates_json`` always populated, ``runbook_id`` nullable on no-match,
``content_sha`` carried alongside the immutable id) are domain contracts —
the SQLModel table is the storage shape, but the policy of *what* to write
is owned here.

Three operations:

* :func:`write_runbook_match` — invoked by the ``MatchRunbook`` pipeline node
  on every match attempt, including ``no_match``. Always populates
  ``candidates_json`` so the regulator-audit answer to "why this runbook and
  not another?" is recoverable from a single row (RFC §3.3).
* :func:`write_runbook_feedback` — invoked by the F8 approval gate when a
  human overrides the matcher's choice; closes the runbook-owner feedback
  loop (F6 spec §6.4).
* :func:`list_no_match_request_ids` — invoked by the F6.M weekly
  fingerprint-clustering flywheel to enumerate recent no-match envelopes.

Queries use SQLAlchemy Core expressions (per the project's DB-architecture
rule: queries belong in the domain layer; no raw SQL).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.data.sql import runbooks as runbook_tables
from sentinel.data.sql import tasks as task_tables
from sentinel.domain.runbooks import models
from sentinel.utils import logs


FeedbackSentiment = Literal["positive", "negative", "wrong_runbook"]


def _serialise_candidates(
    candidates: Sequence[models.RunbookCandidate],
) -> list[dict[str, object]]:
    """
    Return the ``candidates_json`` payload for a :class:`RunbookMatchRecord`.

    Always called — Stage 1 winners ship the top-k pre-filter candidates,
    Stage 2A ties ship the disambiguator's top-k, Stage 2B and ``no_match``
    rows ship the LLM rescue eligible set. The shape is documented in the
    F6 spec §5.5 and consumed by the F6.L drift-detection sweep + F6.M
    flywheel without re-executing the matcher.
    """
    return [
        {
            "runbook_id": candidate.runbook_id,
            "content_sha": candidate.content_sha,
            "tag_score": candidate.score,
            "matched_via": candidate.matched_via,
        }
        for candidate in candidates
    ]


async def write_runbook_match(
    *,
    session: AsyncSession,
    envelope: envelope_mod.Envelope,
    match: models.RunbookMatch,
) -> uuid.UUID:
    """
    Insert a :class:`runbook_tables.RunbookMatchRecord` row for ``match``.

    Always writes — including ``no_match`` outcomes, in which case
    ``runbook_id`` and ``runbook_content_sha`` are ``NULL`` per the F6.D
    migration (014). ``candidates_json`` is always populated so the
    regulator-audit answer to "why this runbook and not another?" is
    recoverable from the row alone (RFC §3.3 / F6 spec §5.5).

    :param session: Async SQLAlchemy session bound to the application DB.
    :param envelope: Identity envelope minted at ingress; ``request_id``
        becomes the FK to ``alert_request``.
    :param match: The :class:`models.RunbookMatch` produced by the matcher
        orchestrator.
    :returns: The inserted ``match_id`` UUID. Callers stash this on the
        pipeline state so downstream nodes (e.g. the F8 approval gate) can
        cross-reference the audit row.
    """
    match_id = uuid.uuid4()
    payload: dict[str, object | None] = {
        "match_id": match_id,
        "request_id": envelope.request_id,
        "runbook_id": match.matched_runbook_id,
        "runbook_content_sha": match.content_sha,
        "match_method": match.match_method,
        "match_confidence": match.confidence,
        "tag_score": match.tag_score,
        "llm_choice": match.llm_choice,
        "llm_justification": match.llm_justification,
        "candidates_json": _serialise_candidates(match.candidates),
    }
    statement = insert(runbook_tables.RunbookMatchRecord).values(**payload)
    await session.execute(statement)
    logs.log_event(
        "runbook_match_persisted",
        params={
            "match_id": str(match_id),
            "request_id": str(envelope.request_id),
            "runbook_id": match.matched_runbook_id,
            "match_method": match.match_method,
            "tag_score": match.tag_score,
            "candidate_count": len(match.candidates),
        },
    )
    return match_id


async def write_runbook_feedback(
    *,
    session: AsyncSession,
    request_id: uuid.UUID,
    runbook_id: str,
    runbook_content_sha: str,
    sentiment: FeedbackSentiment,
    reason: str | None,
    submitted_by: str | None,
) -> uuid.UUID:
    """
    Insert a :class:`runbook_tables.RunbookFeedbackRecord` row.

    Invoked from the F8 approval gate when a human marks a runbook choice
    as ``negative`` or ``wrong_runbook``. The weekly digest (deferred follow-on)
    queries this table by ``runbook_id`` to page owners.

    ``runbook_content_sha`` is the on-disk SHA at the time of feedback so
    post-edit drift can be detected by comparing against the current SHA
    in :func:`sentinel.domain.runbooks.loader.discover_runbooks`.

    :param session: Async SQLAlchemy session bound to the application DB.
    :param request_id: FK to the ``alert_request`` envelope row.
    :param runbook_id: Immutable runbook id the feedback is attached to.
    :param runbook_content_sha: Truncated sha256[:32] of the runbook quartet
        at the time the feedback was given.
    :param sentiment: One of ``positive`` / ``negative`` / ``wrong_runbook``.
    :param reason: Optional ≤500-char free-text rationale from the human.
    :param submitted_by: Optional actor identifier (Slack user id, email,
        ``None`` for system-generated feedback).
    :returns: The inserted ``feedback_id`` UUID.
    """
    feedback_id = uuid.uuid4()
    payload: dict[str, object | None] = {
        "feedback_id": feedback_id,
        "request_id": request_id,
        "runbook_id": runbook_id,
        "runbook_content_sha": runbook_content_sha,
        "sentiment": sentiment,
        "reason": reason,
        "submitted_by": submitted_by,
    }
    statement = insert(runbook_tables.RunbookFeedbackRecord).values(**payload)
    await session.execute(statement)
    logs.log_event(
        "runbook_feedback_persisted",
        params={
            "feedback_id": str(feedback_id),
            "request_id": str(request_id),
            "runbook_id": runbook_id,
            "sentiment": sentiment,
        },
    )
    return feedback_id


async def list_no_match_request_ids(
    *,
    session: AsyncSession,
    since: datetime,
) -> list[uuid.UUID]:
    """
    Return the request_ids of every ``runbook_match`` row with ``match_method='no_match'`` since ``since``.

    Used by the F6.M weekly fingerprint-clustering flywheel to enumerate
    recent no-match envelopes for clustering by alert-label fingerprint.

    :param session: Async SQLAlchemy session bound to the application DB.
    :param since: Inclusive lower-bound on ``matched_at`` (tz-aware UTC).
    :returns: List of ``request_id`` UUIDs ordered by ``matched_at``
        ascending so the flywheel can stream them in chronological order.
    """
    statement = (
        select(col(runbook_tables.RunbookMatchRecord.request_id))
        .where(col(runbook_tables.RunbookMatchRecord.match_method) == "no_match")
        .where(col(runbook_tables.RunbookMatchRecord.matched_at) >= since)
        .order_by(col(runbook_tables.RunbookMatchRecord.matched_at).asc())
    )
    result = await session.execute(statement)
    return [row for (row,) in result.all()]


async def write_prescribed_check_tasks(
    *,
    session: AsyncSession,
    investigation_id: uuid.UUID,
    runbook: models.Runbook,
) -> list[uuid.UUID]:
    """
    Pre-populate :class:`task_tables.InvestigationTaskRecord` rows from a runbook (F6.F.1).

    Called by the ``MatchRunbook`` pipeline node immediately after a
    successful match: every ``checks.yaml.prescribed_checks`` entry on the
    matched runbook becomes one ``investigation_task`` row, FK'd to the
    investigation that the pipeline is currently executing. This lets the
    F8 quality gate cross-check that "every required check ran" without
    re-reading the runbook from disk.

    The task text is rendered as ``"<id>: <description>"`` so the audit row
    carries both the stable check id (matchable against the runbook on
    disk) and the human-readable description in one column. Returns the
    inserted task ids in declaration order so callers can correlate them
    with the runbook's ``prescribed_checks`` list. Empty
    ``prescribed_checks`` is a no-op.

    :param session: Async SQLAlchemy session bound to the application DB.
    :param investigation_id: FK to the parent ``investigation_records`` row.
    :param runbook: The matched runbook whose ``checks.prescribed_checks``
        list seeds the task rows.
    :returns: The inserted ``task_id`` UUIDs in declaration order.
    """
    task_ids: list[uuid.UUID] = []
    for check in runbook.checks.prescribed_checks:
        task_id = uuid.uuid4()
        record = task_tables.InvestigationTaskRecord(
            task_id=task_id,
            investigation_id=investigation_id,
            task_text=f"{check.id}: {check.description}",
        )
        session.add(record)
        task_ids.append(task_id)
    if task_ids:
        await session.flush()
        logs.log_event(
            "runbook_prescribed_checks_seeded",
            params={
                "investigation_id": str(investigation_id),
                "runbook_id": runbook.metadata.runbook_id,
                "task_count": len(task_ids),
            },
        )
    return task_ids
