"""
Frozen identity envelope carried through every pipeline span and DB row.

Lives in ``data/`` (not ``domain/``) because the envelope is composed by
``config``, FastAPI middleware, and webhook handlers — all of which sit
above ``data/`` in the import-linter layer order, so any layer can
import it without an upward dependency. RFC §3.1 / R-IN-3.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

import attrs
from opentelemetry.util import types as otel_types


PIIClass = Literal["public", "internal", "confidential", "mnpi"]
REDACTED_PII_CLASSES: frozenset[PIIClass] = frozenset({"confidential", "mnpi"})
_TENANT_HASH_LENGTH = 12


def is_redacted_pii_class(pii_class: PIIClass) -> bool:
    """Return True when the PII class requires tenant-id redaction."""
    return pii_class in REDACTED_PII_CLASSES


def _validate_tz_aware(
    _instance: object,
    attribute: attrs.Attribute[datetime],
    value: datetime,
) -> None:
    offset = value.utcoffset()
    if offset is None:
        raise ValueError(
            f"{attribute.name} must be tz-aware UTC; got naive datetime {value!r}",
        )
    if offset != timedelta(0):
        raise ValueError(
            f"{attribute.name} must be tz-aware UTC; got tz={value.tzinfo} ({value!r})",
        )


@attrs.frozen(kw_only=True, slots=True)
class Envelope:
    """
    Identity envelope minted at FastAPI ingress and threaded through every
    downstream span and DB row.

    The six fields match RFC §3.1. ``request_id`` is the canonical
    correlation key; ``tenant_id`` / ``cluster_id`` / ``region`` carry
    multi-tenant scoping; ``pii_class`` controls log redaction at the
    ``to_log_context`` boundary.
    """

    request_id: UUID
    tenant_id: str
    cluster_id: str
    region: str
    pii_class: PIIClass
    received_at: datetime = attrs.field(validator=_validate_tz_aware)

    def to_span_attributes(self) -> dict[str, otel_types.AttributeValue]:
        """
        Return the six envelope-owned mandatory OTel attributes (RFC §13.2)
        plus Langfuse-namespaced session/user attributes that promote spans
        into Langfuse's Sessions and Users tabs.

        ``langfuse.session.id`` ← ``request_id`` (one Langfuse session per
        ingress request, scoping every downstream graph node / agent / tool
        span emitted under it). ``langfuse.user.id`` ← ``tenant_id`` so the
        Users tab groups by tenant.
        """
        return {
            "request_id": str(self.request_id),
            "tenant_id": self.tenant_id,
            "cluster_id": self.cluster_id,
            "region": self.region,
            "pii_class": self.pii_class,
            "received_at": self.received_at.isoformat(),
            "langfuse.session.id": str(self.request_id),
            "langfuse.user.id": self.tenant_id,
        }

    def to_log_context(self) -> dict[str, str]:
        """Return a structlog binding context with F2.8 redaction applied."""
        context: dict[str, str] = {
            "request_id": str(self.request_id),
            "cluster_id": self.cluster_id,
            "region": self.region,
            "pii_class": self.pii_class,
            "received_at": self.received_at.isoformat(),
        }
        if is_redacted_pii_class(self.pii_class):
            context["tenant_hash"] = hashlib.sha256(
                self.tenant_id.encode(),
            ).hexdigest()[:_TENANT_HASH_LENGTH]
        else:
            context["tenant_id"] = self.tenant_id
        return context
