"""
Construct an :class:`~sentinel.data.envelope.Envelope` at webhook ingress (F2.4).

Each ``envelope_from_*`` helper takes the inbound webhook payload, the
middleware-minted ``request_id`` (UUID), the active ``Settings``, and an
optional ``received_at`` clock seam, then derives the six envelope fields
per RFC §3.1.

Tenant-id derivation precedence per source:

- **PagerDuty**: ``event.data.body.details.namespace`` (k8s namespace label)
  > ``event.data.body.namespace`` > ``event.data.body.custom_details.namespace``
  > ``event.data.service.summary`` (sanitised) > ``"unknown"``.
- **Datadog**: ``tags.k8s_namespace`` > ``tags.namespace`` > ``tags.service``
  > ``"unknown"``.
- **Jira**: ``issue.fields.project.key`` (lowercased) > ``"unknown"``.

When the precedence falls all the way through to the ``"unknown"`` sentinel,
soft-fail mode (default) emits a structured warning log and returns the
envelope with ``tenant_id="unknown"``. Strict mode (driven by
``BaseConfiguration.envelope_strict_mode``) instead raises
:class:`EnvelopeIngressError`. R-IN-3 hardens this once tenant derivation
is robust across every ingress; foundations stay soft-fail to keep dev
velocity.

Cluster + region come from ``settings.k8s_cluster_name`` and
``settings.region`` respectively, falling back to ``"unknown"`` when unset.
``pii_class`` defaults to ``"internal"`` — CONFIDENTIAL/MNPI elevation is
policy-driven and lands later in the foundations roadmap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sentinel.data import envelope as envelope_mod
from sentinel.settings import Settings
from sentinel.utils import logs


_UNKNOWN_TENANT = "unknown"
_DEFAULT_PII_CLASS: envelope_mod.PIIClass = "internal"
_TENANT_ID_MAX_LENGTH = 63


# ---------------------------------------------------------------------------
# EnvelopeIngressError
# ---------------------------------------------------------------------------


class EnvelopeIngressError(Exception):
    """
    Raised by ``envelope_from_*`` helpers in strict mode when a webhook
    payload lacks the identifiers required to derive ``tenant_id``.

    Carries ``source``, ``request_id``, and ``missing_tenant_id`` so the
    FastAPI handler can surface them back to the caller without parsing
    strings. ``missing_tenant_id`` reads as ``True`` today; future ingress
    failure modes can extend the class with additional reason flags.
    """

    def __init__(self, *, source: str, request_id: UUID) -> None:
        self.source = source
        self.request_id = request_id
        self.missing_tenant_id = True
        super().__init__(
            f"envelope ingress failed: missing tenant_id "
            f"(source={source}, request_id={request_id})",
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sanitize_tenant_slug(raw: str) -> str:
    """
    Return a lowercased, whitespace-collapsed slug suitable for tenant_id.

    Whitespace runs become single hyphens. Internal characters that aren't
    alphanumerics, hyphens, or underscores are dropped. The result is
    truncated to the k8s namespace limit (63 chars) so a hostile payload
    cannot bloat downstream logs and DB rows. Returns the original string
    lowercased and truncated when it is already a clean slug.
    """
    lowered = raw.strip().lower()
    if not lowered:
        return ""
    parts = lowered.split()
    joined = "-".join(parts)
    cleaned = "".join(ch for ch in joined if ch.isalnum() or ch in ("-", "_"))
    return cleaned[:_TENANT_ID_MAX_LENGTH]


def _coerce_received_at(received_at: datetime | None) -> datetime:
    """Return a tz-aware UTC datetime, defaulting to ``datetime.now(UTC)``."""
    if received_at is None:
        return datetime.now(tz=UTC)
    return received_at


def _resolve_cluster_id(*, settings: Settings) -> str:
    """Return ``settings.k8s_cluster_name`` or the unknown sentinel."""
    cluster_name = getattr(settings, "k8s_cluster_name", "") or ""
    return cluster_name or _UNKNOWN_TENANT


def _resolve_region(*, settings: Settings) -> str:
    """Return ``settings.region`` or the unknown sentinel."""
    region = getattr(settings, "region", "") or ""
    return region or _UNKNOWN_TENANT


def _emit_unknown_warning(*, source: str, request_id: UUID) -> None:
    """Emit the canonical envelope_tenant_unknown structured event."""
    logs.log_event(
        "envelope_tenant_unknown",
        params={
            "source": source,
            "request_id": str(request_id),
            "fallback_used": True,
        },
    )


def _finalise_tenant_id(
    *,
    candidate: str,
    source: str,
    request_id: UUID,
    strict: bool,
) -> str:
    """
    Return the resolved ``tenant_id`` after applying soft/strict-mode policy.

    :raises EnvelopeIngressError: when ``strict`` is True and ``candidate``
        is empty.
    """
    if candidate:
        return candidate
    if strict:
        raise EnvelopeIngressError(source=source, request_id=request_id)
    _emit_unknown_warning(source=source, request_id=request_id)
    return _UNKNOWN_TENANT


def _build_envelope(
    *,
    tenant_id: str,
    request_id: UUID,
    settings: Settings,
    received_at: datetime | None,
) -> envelope_mod.Envelope:
    """Compose the validated ``Envelope`` from the derived parts."""
    return envelope_mod.Envelope(
        request_id=request_id,
        tenant_id=tenant_id,
        cluster_id=_resolve_cluster_id(settings=settings),
        region=_resolve_region(settings=settings),
        pii_class=_DEFAULT_PII_CLASS,
        received_at=_coerce_received_at(received_at),
    )


# ---------------------------------------------------------------------------
# PagerDuty
# ---------------------------------------------------------------------------


def _extract_pagerduty_namespace(*, data: dict[str, Any]) -> str:
    """Return a k8s namespace from common PagerDuty payload locations."""
    body = data.get("body") or {}
    if not isinstance(body, dict):
        return ""

    details = body.get("details")
    if isinstance(details, dict):
        namespace = details.get("namespace") or ""
        if isinstance(namespace, str) and namespace:
            return namespace

    direct = body.get("namespace")
    if isinstance(direct, str) and direct:
        return direct

    custom = body.get("custom_details")
    if isinstance(custom, dict):
        namespace = custom.get("namespace") or ""
        if isinstance(namespace, str) and namespace:
            return namespace

    return ""


def _extract_pagerduty_service_slug(*, data: dict[str, Any]) -> str:
    """Return the sanitised PagerDuty service summary as a tenant slug."""
    service_info = data.get("service") or {}
    if not isinstance(service_info, dict):
        return ""
    summary = service_info.get("summary") or ""
    if not isinstance(summary, str):
        return ""
    return _sanitize_tenant_slug(summary)


def envelope_from_pagerduty(
    *,
    payload: dict[str, Any],
    request_id: UUID,
    settings: Settings,
    strict: bool = False,
    received_at: datetime | None = None,
) -> envelope_mod.Envelope:
    """
    Return an ``Envelope`` derived from a PagerDuty V3 webhook payload.

    :param payload: Raw PagerDuty V3 webhook event dict.
    :param request_id: UUID minted by ``RequestIdMiddleware``.
    :param settings: Active ``Settings`` instance.
    :param strict: When True, raise ``EnvelopeIngressError`` on missing
        tenant_id; when False, log a warning and fall back to ``"unknown"``.
    :param received_at: Optional injected wall-clock time. Defaults to
        ``datetime.now(UTC)``.
    :raises EnvelopeIngressError: in strict mode when no tenant identifiers
        are present in the payload.
    """
    event = payload.get("event") or {}
    data = event.get("data") or {} if isinstance(event, dict) else {}
    if not isinstance(data, dict):
        data = {}

    namespace = _extract_pagerduty_namespace(data=data)
    candidate = namespace if namespace else _extract_pagerduty_service_slug(data=data)

    tenant_id = _finalise_tenant_id(
        candidate=candidate,
        source="pagerduty",
        request_id=request_id,
        strict=strict,
    )
    return _build_envelope(
        tenant_id=tenant_id,
        request_id=request_id,
        settings=settings,
        received_at=received_at,
    )


# ---------------------------------------------------------------------------
# Datadog
# ---------------------------------------------------------------------------


def _parse_datadog_tags(*, raw_tags: object) -> dict[str, str]:
    """Return a key->value dict parsed from the comma-separated Datadog tag string."""
    if not isinstance(raw_tags, str):
        return {}
    parsed: dict[str, str] = {}
    for tag in raw_tags.split(","):
        stripped = tag.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        if key and value:
            parsed[key] = value
    return parsed


def envelope_from_datadog(
    *,
    payload: dict[str, Any],
    request_id: UUID,
    settings: Settings,
    strict: bool = False,
    received_at: datetime | None = None,
) -> envelope_mod.Envelope:
    """
    Return an ``Envelope`` derived from a Datadog webhook payload.

    Tenant precedence: ``tags.k8s_namespace`` > ``tags.namespace``
    > ``tags.service`` > ``"unknown"``.

    :raises EnvelopeIngressError: in strict mode when no tenant tag is found.
    """
    tags = _parse_datadog_tags(raw_tags=payload.get("tags"))
    candidate = tags.get("k8s_namespace") or tags.get("namespace") or tags.get("service") or ""

    tenant_id = _finalise_tenant_id(
        candidate=candidate,
        source="datadog",
        request_id=request_id,
        strict=strict,
    )
    return _build_envelope(
        tenant_id=tenant_id,
        request_id=request_id,
        settings=settings,
        received_at=received_at,
    )


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------


def envelope_from_jira(
    *,
    payload: dict[str, Any],
    request_id: UUID,
    settings: Settings,
    strict: bool = False,
    received_at: datetime | None = None,
) -> envelope_mod.Envelope:
    """
    Return an ``Envelope`` derived from a Jira Service Desk webhook payload.

    Tenant precedence: ``issue.fields.project.key`` (lowercased) >
    ``"unknown"``.

    :raises EnvelopeIngressError: in strict mode when the project key is
        missing.
    """
    issue = payload.get("issue") or {}
    fields = issue.get("fields") or {} if isinstance(issue, dict) else {}
    project = fields.get("project") or {} if isinstance(fields, dict) else {}
    project_key = project.get("key") or "" if isinstance(project, dict) else ""
    candidate = project_key.lower() if isinstance(project_key, str) else ""

    tenant_id = _finalise_tenant_id(
        candidate=candidate,
        source="jira",
        request_id=request_id,
        strict=strict,
    )
    return _build_envelope(
        tenant_id=tenant_id,
        request_id=request_id,
        settings=settings,
        received_at=received_at,
    )


# ---------------------------------------------------------------------------
# Manual / API-driven sentinel
# ---------------------------------------------------------------------------


_MANUAL_TENANT = "manual"


def envelope_for_manual(
    *,
    request_id: UUID,
    settings: Settings,
    received_at: datetime | None = None,
) -> envelope_mod.Envelope:
    """
    Return an ``Envelope`` for the manual ``/investigate`` and ``/review``
    endpoints when callers do not supply tenant identifiers.

    Uses the ``"manual"`` sentinel so ops queries can distinguish API-driven
    runs from real ingress. Strict mode does not apply here — manual
    endpoints are always permitted to mint a placeholder envelope.
    """
    return _build_envelope(
        tenant_id=_MANUAL_TENANT,
        request_id=request_id,
        settings=settings,
        received_at=received_at,
    )
