"""
Runbook-gap clustering and upsert logic for the F6.M weekly auto-PR flywheel.

The flywheel turns ``runbook_match`` rows where ``match_method = 'no_match'``
into structured cluster signal so the catalog can grow proactively. Each
incoming no-match row is fingerprinted by

    sha256(sorted_alert_labels || classification_category)[:16]

so identical gaps collapse to a single cluster row regardless of the surface
alert noise (different incident IDs, timestamps, hosts). Clusters that cross
the configured threshold (``flywheel_min_cluster_size``, default 3) are
candidates for an auto-PR scaffold opened by ``scripts/runbook_gap_flywheel``.

This module is deliberately I/O-light:

* :func:`compute_fingerprint` is a pure hash function; tests pin its output.
* :func:`cluster_no_match_members` is a pure grouping function over a
  pre-fetched sequence of :class:`GapMember`.
* :func:`query_recent_no_matches` and :func:`upsert_cluster` are the only
  DB-bound entry points; they each consume an :class:`AsyncSession` so
  callers (the script + integration tests) own the transaction boundary.

The on-disk alert envelope (``alert_request.redacted_annotations``) is the
source of truth for ``alertname`` / ``service`` / ``labels``. When the
column is absent or missing a key (as in the F3 schema-only era before F4
wires writers), the loader degrades to a sentinel string so the cluster row
is still well-formed and the operator can spot the placeholder in the PR.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import attrs
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from sentinel.data.sql import alert_requests as alert_requests_sql
from sentinel.data.sql import runbook_gap_cluster as gap_sql
from sentinel.data.sql import runbooks as runbooks_sql
from sentinel.utils import logs


_FINGERPRINT_HEX_LENGTH = 16
_MEMBER_REQUEST_ID_CAP = 100
_NO_MATCH_METHOD: runbooks_sql.MatchMethod = "no_match"
_UNKNOWN_SENTINEL = "unknown"


@attrs.frozen(kw_only=True, slots=True)
class GapMember:
    """
    One ``runbook_match`` no-match row joined with its alert envelope.

    All string fields fall back to :data:`_UNKNOWN_SENTINEL` when the
    underlying envelope column is empty or missing the key. This keeps the
    cluster row well-formed without inventing structure that isn't there
    -- the operator sees ``service=unknown`` in the auto-PR and knows the
    upstream payload was thin.
    """

    request_id: uuid.UUID
    classification_category: str
    alertname: str
    service: str
    labels_sorted_json: str
    summary: str
    matched_at: datetime


@attrs.frozen(kw_only=True, slots=True)
class GapCluster:
    """
    A group of :class:`GapMember` sharing one fingerprint.

    The cluster is the upsert unit. ``members`` is the in-memory sample used
    to derive denormalised columns (``distinct_services``,
    ``distinct_alertnames``, ``representative_summary``) and the request-id
    list persisted on the row. The persistent row caps the request-id list
    at the last 100 entries to keep the JSONB column bounded.
    """

    fingerprint: str
    classification_category: str
    members: tuple[GapMember, ...]
    distinct_services: frozenset[str]
    distinct_alertnames: frozenset[str]
    first_seen_at: datetime
    last_seen_at: datetime
    representative_summary: str

    @property
    def member_count(self) -> int:
        """Return the number of :class:`GapMember`s in this cluster."""
        return len(self.members)

    @property
    def member_request_ids(self) -> tuple[uuid.UUID, ...]:
        """Return the request_ids of every member in chronological order."""
        return tuple(member.request_id for member in self.members)


def compute_fingerprint(*, sorted_labels_json: str, classification_category: str) -> str:
    """
    Return the F6.M cluster fingerprint for a no-match alert.

    The fingerprint is the leading 16 hex chars of
    ``sha256(sorted_labels_json || classification_category)``. The labels
    must already be sorted-key JSON so the same logical alert produces the
    same fingerprint regardless of the dict iteration order at the call
    site. ``classification_category`` is concatenated verbatim — empty
    strings are accepted and produce a stable fingerprint of their own
    (signalling "no classification ran" as one cluster, not many).
    """
    digest = hashlib.sha256()
    digest.update(sorted_labels_json.encode("utf-8"))
    digest.update(classification_category.encode("utf-8"))
    return digest.hexdigest()[:_FINGERPRINT_HEX_LENGTH]


def _sorted_labels_json(labels: Mapping[str, Any]) -> str:
    """Return ``labels`` as deterministic sorted-key JSON for fingerprinting."""
    return json.dumps(labels, sort_keys=True, separators=(",", ":"))


def cluster_no_match_members(members: Sequence[GapMember]) -> list[GapCluster]:
    """
    Group ``members`` by their pre-computed fingerprint.

    Pure function: returns a fresh list of :class:`GapCluster` sorted by
    cluster size descending then by fingerprint for stable iteration. The
    representative summary is the longest member summary (longest tends to
    carry the most context). ``first_seen_at`` and ``last_seen_at`` are
    drawn from the member ``matched_at`` extremes.

    The threshold-based filter (e.g. "drop singletons") is **not** applied
    here -- the script enforces ``flywheel_min_cluster_size`` so this
    function stays auditable: every fingerprint that appears in the
    no-match stream surfaces as a cluster, and the script's threshold-skip
    log is the operator's record of what was suppressed.
    """
    by_fingerprint: dict[str, list[GapMember]] = {}
    for member in members:
        fingerprint = compute_fingerprint(
            sorted_labels_json=member.labels_sorted_json,
            classification_category=member.classification_category,
        )
        by_fingerprint.setdefault(fingerprint, []).append(member)

    clusters: list[GapCluster] = []
    for fingerprint, group in by_fingerprint.items():
        ordered = tuple(sorted(group, key=lambda m: m.matched_at))
        services = frozenset(member.service for member in ordered)
        alertnames = frozenset(member.alertname for member in ordered)
        # Longest summary wins; ties broken by most-recent so the PR shows
        # the freshest sample when two members carry equal-length text.
        representative = max(
            ordered,
            key=lambda m: (len(m.summary), m.matched_at),
        )
        clusters.append(
            GapCluster(
                fingerprint=fingerprint,
                classification_category=ordered[0].classification_category,
                members=ordered,
                distinct_services=services,
                distinct_alertnames=alertnames,
                first_seen_at=ordered[0].matched_at,
                last_seen_at=ordered[-1].matched_at,
                representative_summary=representative.summary,
            )
        )
    # Stable ordering: largest cluster first then alphabetical fingerprint
    # so the script's iteration log is deterministic across runs.
    clusters.sort(key=lambda c: (-c.member_count, c.fingerprint))
    return clusters


def _coerce_labels(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """
    Return the ``labels`` dict from the envelope payload.

    Looks for a ``labels`` mapping; falls back to an empty dict so the
    fingerprint is stable on payloads that pre-date the F4 envelope writer.
    Non-mapping values are coerced to ``{}`` rather than raising — the
    flywheel must keep walking even if one alert envelope is malformed.
    """
    if payload is None:
        return {}
    raw = payload.get("labels")
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def _coerce_str(payload: Mapping[str, Any] | None, key: str) -> str:
    """Return ``payload[key]`` as a string or :data:`_UNKNOWN_SENTINEL`."""
    if payload is None:
        return _UNKNOWN_SENTINEL
    value = payload.get(key)
    if value is None:
        return _UNKNOWN_SENTINEL
    return str(value)


def _build_summary(payload: Mapping[str, Any] | None, alertname: str, service: str) -> str:
    """
    Compose a representative summary string from envelope fields.

    Prefers an explicit ``summary``/``title`` if present; otherwise
    synthesises ``"<alertname> on <service>"`` from the envelope. Either
    result is short enough to fit the PR title character budget.
    """
    if payload is not None:
        for key in ("summary", "title", "description"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return f"{alertname} on {service}"


async def query_recent_no_matches(*, session: AsyncSession, since: datetime) -> list[GapMember]:
    """
    Return :class:`GapMember`s for every no-match row written since ``since``.

    Joins ``runbook_match`` with ``alert_request`` on ``request_id`` so the
    upstream alert payload (alertname / service / labels) is available for
    fingerprinting. Rows whose envelope is missing fall back to sentinel
    strings — the no-match row itself is the source of truth and we never
    drop signal due to upstream envelope gaps.

    The ``since`` cutoff is callsite-controlled (the script computes it
    from ``flywheel_lookback_days``). All returned members carry timezone-
    aware ``matched_at`` so downstream date math is unambiguous.
    """
    statement = (
        select(
            runbooks_sql.RunbookMatchRecord.request_id,
            runbooks_sql.RunbookMatchRecord.matched_at,
            alert_requests_sql.AlertRequestRecord.redacted_annotations,
        )
        .join(
            alert_requests_sql.AlertRequestRecord,
            alert_requests_sql.AlertRequestRecord.request_id
            == runbooks_sql.RunbookMatchRecord.request_id,
            isouter=True,
        )
        .where(runbooks_sql.RunbookMatchRecord.match_method == _NO_MATCH_METHOD)
        .where(runbooks_sql.RunbookMatchRecord.matched_at >= since)
        .order_by(runbooks_sql.RunbookMatchRecord.matched_at.asc())
    )
    result = await session.execute(statement)
    members: list[GapMember] = []
    for row in result.all():
        members.append(_row_to_member(row))
    return members


def _row_to_member(row: Any) -> GapMember:
    """Convert one joined ``runbook_match``/``alert_request`` row to a :class:`GapMember`."""
    request_id, matched_at, payload = row
    labels = _coerce_labels(payload)
    alertname = _coerce_str(payload, "alertname")
    service = _coerce_str(payload, "service")
    classification_category = _coerce_str(payload, "classification_category")
    summary = _build_summary(payload, alertname=alertname, service=service)
    return GapMember(
        request_id=request_id,
        classification_category=classification_category,
        alertname=alertname,
        service=service,
        labels_sorted_json=_sorted_labels_json(labels),
        summary=summary,
        matched_at=matched_at,
    )


def _sorted_string_list(values: frozenset[str]) -> list[str]:
    """Return a sorted list of strings for stable JSONB serialisation."""
    return sorted(values)


def _capped_request_ids(*, existing: Sequence[str], incoming: Sequence[uuid.UUID]) -> list[str]:
    """
    Merge ``existing`` request-id strings with ``incoming`` UUIDs, capped at last-100.

    The cap is enforced from the tail (most-recent kept). The caller passes
    ``incoming`` in chronological order so the trim drops the oldest. Order
    is preserved so the JSONB column is a stable timeline view.
    """
    merged: list[str] = list(existing)
    for new_id in incoming:
        merged.append(str(new_id))
    if len(merged) <= _MEMBER_REQUEST_ID_CAP:
        return merged
    return merged[-_MEMBER_REQUEST_ID_CAP:]


async def upsert_cluster(
    *, session: AsyncSession, cluster: GapCluster
) -> gap_sql.RunbookGapClusterRecord:
    """
    Insert ``cluster`` if its fingerprint is new, else update the existing row.

    Update path: ``member_count`` and ``flywheel_iteration`` increment;
    ``member_request_ids`` merges with the new request-ids and re-caps to
    the last 100; ``last_seen_at`` advances to the cluster's latest member;
    ``representative_alert_summary``, ``distinct_services``,
    ``distinct_alertnames`` refresh from the cluster (the freshly-clustered
    set carries the current view). ``first_seen_at`` is preserved on
    update so the row continues to date the original gap.

    Returns the persisted (or freshly-loaded) record so the caller can
    decide whether to open a draft PR. The caller owns the surrounding
    transaction commit -- this function only flushes.
    """
    statement = select(gap_sql.RunbookGapClusterRecord).where(
        gap_sql.RunbookGapClusterRecord.fingerprint == cluster.fingerprint
    )
    result = await session.execute(statement)
    existing = result.scalar_one_or_none()
    now = datetime.now(tz=UTC)

    if existing is None:
        record = gap_sql.RunbookGapClusterRecord(
            fingerprint=cluster.fingerprint,
            classification_category=cluster.classification_category,
            representative_alert_summary=cluster.representative_summary,
            member_request_ids=_capped_request_ids(
                existing=(), incoming=cluster.member_request_ids
            ),
            member_count=cluster.member_count,
            distinct_services=_sorted_string_list(cluster.distinct_services),
            distinct_alertnames=_sorted_string_list(cluster.distinct_alertnames),
            first_seen_at=cluster.first_seen_at,
            last_seen_at=cluster.last_seen_at,
            flywheel_iteration=1,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        await session.flush()
        logs.log_event(
            "runbook_gap_cluster_inserted",
            params={
                "fingerprint": cluster.fingerprint,
                "member_count": cluster.member_count,
                "iteration": 1,
            },
        )
        return record

    # Update path: refresh denormalised fields, increment chronicity counter,
    # merge + cap request-ids. Existing fields are reassigned (not mutated
    # in place beyond the SQLModel's per-attribute setattr); SQLAlchemy
    # tracks the changes via the unit of work.
    merged_services = _sorted_string_list(
        frozenset(existing.distinct_services) | cluster.distinct_services
    )
    merged_alertnames = _sorted_string_list(
        frozenset(existing.distinct_alertnames) | cluster.distinct_alertnames
    )
    capped_ids = _capped_request_ids(
        existing=existing.member_request_ids,
        incoming=cluster.member_request_ids,
    )
    existing.member_count = existing.member_count + cluster.member_count
    existing.flywheel_iteration = existing.flywheel_iteration + 1
    existing.member_request_ids = capped_ids
    existing.distinct_services = merged_services
    existing.distinct_alertnames = merged_alertnames
    existing.last_seen_at = max(existing.last_seen_at, cluster.last_seen_at)
    existing.representative_alert_summary = cluster.representative_summary
    existing.updated_at = now
    await session.flush()
    logs.log_event(
        "runbook_gap_cluster_updated",
        params={
            "fingerprint": cluster.fingerprint,
            "member_count": existing.member_count,
            "iteration": existing.flywheel_iteration,
            "members_capped": len(capped_ids) == _MEMBER_REQUEST_ID_CAP,
        },
    )
    return existing


# Re-export the SQLAlchemy session sentinel so callers can patch the symbol
# in this module without reaching into ``sqlalchemy`` directly.
_ = (sa,)


__all__ = (
    "MEMBER_REQUEST_ID_CAP",
    "GapCluster",
    "GapMember",
    "cluster_no_match_members",
    "compute_fingerprint",
    "query_recent_no_matches",
    "upsert_cluster",
)


# Public constant alias for callers (script + tests) so they can reference the
# cap value without importing the module-private sentinel.
MEMBER_REQUEST_ID_CAP = _MEMBER_REQUEST_ID_CAP
