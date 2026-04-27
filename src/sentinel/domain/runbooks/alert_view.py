"""
Adapter that projects an :class:`Alert` onto the :class:`MatchableAlert` Protocol (F6.F.1).

The matcher consumes a small structural Protocol — five fields:
``alertname``, ``severity`` (P1..P5), ``resource_kind``, ``labels``,
``pii_class``. The pipeline's :class:`Alert` entity carries title,
description, ``severity`` (firm string scale), service, and a free-form
``raw_payload``; ``pii_class`` is held by the request envelope.

Rather than push matcher-shape concerns onto :class:`Alert` (which is a
shared API/webhook boundary type), this module wraps an alert + envelope
into a frozen, immutable view that fulfils the Protocol with no further
heap allocation per matcher call.

Severity translation:

* ``CRITICAL`` -> ``P1``
* ``HIGH``     -> ``P2``
* ``MEDIUM``   -> ``P3``
* ``LOW``      -> ``P4``

The ``alertname`` defaults to the alert title when ``raw_payload`` does
not carry an explicit ``alertname`` key (Datadog/PagerDuty webhooks both
ship the canonical alertname via ``raw_payload``). ``resource_kind``
defaults to ``"Pod"`` since the SRE catalog's K8s runbooks all target
Pod-scoped alerts; alerts that do not apply will simply fall to no-match.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import attrs

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.alerts import entities as alert_entities


# Map firm-internal severity labels to the F6 spec's P1..P5 scale.
# Mirror of the matcher's ``_SEVERITY_RANK`` ordering: CRITICAL is the
# most-severe end of the scale, LOW the least. Unknown severities surface
# as ``P5`` so the matcher falls into the "compatible only with severity_min=P5"
# bucket, which is the safest pre-filter side-effect.
_SEVERITY_TO_P_SCALE: dict[alert_entities.AlertSeverity, str] = {
    alert_entities.AlertSeverity.CRITICAL: "P1",
    alert_entities.AlertSeverity.HIGH: "P2",
    alert_entities.AlertSeverity.MEDIUM: "P3",
    alert_entities.AlertSeverity.LOW: "P4",
}


_DEFAULT_RESOURCE_KIND: str = "Pod"


@attrs.frozen(kw_only=True, slots=True)
class MatchableAlertView:
    """
    Frozen view of an :class:`Alert` + envelope that fulfils :class:`MatchableAlert`.

    Construction is via :meth:`from_alert`; the constructor is hidden so
    callers always go through the canonical mapping rather than passing
    raw scalars directly. The view holds no reference to the upstream
    :class:`Alert` so a downstream mutation cannot be observed through it.
    """

    alertname: str
    severity: str
    resource_kind: str
    labels: Mapping[str, str]
    pii_class: str

    @classmethod
    def from_alert(
        cls,
        *,
        alert: alert_entities.Alert,
        envelope: envelope_mod.Envelope,
    ) -> MatchableAlertView:
        """
        Build a :class:`MatchableAlertView` from an :class:`Alert` + :class:`Envelope`.

        Reads ``alertname``, ``resource_kind``, ``labels`` from
        ``alert.raw_payload`` with safe defaults so a webhook that drops a
        field does not blow up the matcher. ``pii_class`` is sourced from
        the envelope, which is the canonical owner per RFC §3.1.
        """
        raw: dict[str, Any] = alert.raw_payload or {}
        alertname_value = raw.get("alertname")
        alertname: str = str(alertname_value) if alertname_value else alert.title
        resource_kind_value = raw.get("resource_kind", _DEFAULT_RESOURCE_KIND)
        resource_kind: str = (
            str(resource_kind_value) if resource_kind_value else _DEFAULT_RESOURCE_KIND
        )
        labels_value = raw.get("labels") or {}
        labels: dict[str, str] = (
            {str(key): str(value) for key, value in labels_value.items()}
            if isinstance(labels_value, dict)
            else {}
        )
        severity: str = _SEVERITY_TO_P_SCALE.get(alert.severity, "P5")
        return cls(
            alertname=alertname,
            severity=severity,
            resource_kind=resource_kind,
            labels=labels,
            pii_class=str(envelope.pii_class),
        )
