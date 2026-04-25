"""
Unit tests for ``interfaces.webhooks.envelope_factory`` (F2.4).

Cover envelope construction from PagerDuty, Datadog, and Jira webhook
payloads, including:

- ``tenant_id`` derivation precedence (namespace > service tag > "unknown").
- Soft-fail mode (default) emits a warning and falls back to "unknown".
- Strict mode raises ``EnvelopeIngressError`` when tenant_id cannot be
  derived.
- ``request_id`` is the middleware-minted UUID, never re-minted here.
- ``cluster_id`` / ``region`` come from settings with safe defaults.
- ``received_at`` is tz-aware UTC sourced from a clock-injection seam.
- ``pii_class`` defaults to "internal" for foundations
  (CONFIDENTIAL/MNPI elevation is policy-driven later).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest import mock

import pytest

from sentinel.data import envelope as envelope_mod
from sentinel.interfaces.webhooks import envelope_factory


_FROZEN_REQUEST_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
_FROZEN_RECEIVED_AT = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)


def _build_settings(
    *,
    cluster_name: str = "prod-eu-west-1",
    region: str = "eu-west-1",
) -> mock.MagicMock:
    """Return a Settings stub carrying the cluster_id / region inputs."""
    settings_stub = mock.MagicMock()
    settings_stub.k8s_cluster_name = cluster_name
    settings_stub.region = region
    return settings_stub


# ---------------------------------------------------------------------------
# PagerDuty envelope extraction
# ---------------------------------------------------------------------------


class TestEnvelopeFromPagerDuty:
    """Tests for ``envelope_from_pagerduty``."""

    def test_returns_envelope_with_tenant_from_k8s_namespace(self):
        # Given a PagerDuty payload that carries a k8s namespace under body.details
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "P123",
                    "title": "Pod crashloop",
                    "service": {"summary": "payments-api"},
                    "body": {"details": {"namespace": "payments-prod"}},
                },
            },
        }
        settings_stub = _build_settings()

        # When envelope_from_pagerduty is invoked
        envelope = envelope_factory.envelope_from_pagerduty(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then tenant_id is the namespace value
        assert envelope.tenant_id == "payments-prod"

    def test_returns_envelope_with_tenant_from_service_when_no_namespace(self):
        # Given a PagerDuty payload with service tag but no namespace
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "P200",
                    "title": "API latency",
                    "service": {"summary": "Production API"},
                    "body": {"details": "CPU saturated"},
                },
            },
        }
        settings_stub = _build_settings()

        # When envelope_from_pagerduty is invoked
        envelope = envelope_factory.envelope_from_pagerduty(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then tenant_id derives from the service summary, sanitised for use
        # as a tenant slug (lowercased, whitespace replaced with '-')
        assert envelope.tenant_id == "production-api"

    def test_returns_envelope_with_unknown_tenant_when_neither_present(self):
        # Given a PagerDuty payload with neither namespace nor service tag
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "P300",
                    "title": "Mystery incident",
                    "body": {"details": "no metadata"},
                },
            },
        }
        settings_stub = _build_settings()

        # When envelope_from_pagerduty is invoked in soft-fail mode (default)
        envelope = envelope_factory.envelope_from_pagerduty(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then tenant_id falls back to the "unknown" sentinel
        assert envelope.tenant_id == "unknown"

    def test_emits_warning_log_when_tenant_is_unknown(self):
        # Given a PagerDuty payload with no tenant identifiers
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {"id": "P301", "title": "Mystery"},
            },
        }
        settings_stub = _build_settings()

        # When envelope_from_pagerduty is invoked with the log_event hook patched
        with mock.patch.object(envelope_factory.logs, "log_event") as log_event_mock:
            envelope_factory.envelope_from_pagerduty(
                payload=payload,
                request_id=_FROZEN_REQUEST_ID,
                settings=settings_stub,
                received_at=_FROZEN_RECEIVED_AT,
            )

        # Then a structured warning event is emitted with source / request_id context
        log_event_mock.assert_any_call(
            "envelope_tenant_unknown",
            params={
                "source": "pagerduty",
                "request_id": str(_FROZEN_REQUEST_ID),
                "fallback_used": True,
            },
        )

    def test_raises_when_strict_mode_and_tenant_unknown(self):
        # Given a PagerDuty payload with no tenant and strict mode enabled
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {"id": "P302", "title": "Mystery"},
            },
        }
        settings_stub = _build_settings()

        # When envelope_from_pagerduty is invoked with strict=True
        # Then EnvelopeIngressError is raised carrying source + request_id
        with pytest.raises(envelope_factory.EnvelopeIngressError) as raised:
            envelope_factory.envelope_from_pagerduty(
                payload=payload,
                request_id=_FROZEN_REQUEST_ID,
                settings=settings_stub,
                strict=True,
                received_at=_FROZEN_RECEIVED_AT,
            )
        assert raised.value.source == "pagerduty"
        assert raised.value.request_id == _FROZEN_REQUEST_ID

    def test_uses_request_id_from_middleware(self):
        # Given a PagerDuty payload and a known request_id from the middleware
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "P400",
                    "title": "X",
                    "service": {"summary": "svc"},
                },
            },
        }
        settings_stub = _build_settings()

        # When envelope_from_pagerduty is invoked
        envelope = envelope_factory.envelope_from_pagerduty(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then the envelope's request_id matches the injected one (not minted)
        assert envelope.request_id == _FROZEN_REQUEST_ID

    def test_uses_cluster_and_region_from_settings(self):
        # Given a PagerDuty payload and settings carrying cluster + region
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "P500",
                    "title": "X",
                    "service": {"summary": "svc"},
                },
            },
        }
        settings_stub = _build_settings(cluster_name="prod-us-east-1", region="us-east-1")

        # When envelope_from_pagerduty is invoked
        envelope = envelope_factory.envelope_from_pagerduty(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then cluster_id and region come from settings
        assert envelope.cluster_id == "prod-us-east-1"
        assert envelope.region == "us-east-1"

    def test_uses_unknown_cluster_and_region_when_settings_unset(self):
        # Given settings with empty cluster_name and region attributes
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "P600",
                    "title": "X",
                    "service": {"summary": "svc"},
                },
            },
        }
        settings_stub = _build_settings(cluster_name="", region="")

        # When envelope_from_pagerduty is invoked
        envelope = envelope_factory.envelope_from_pagerduty(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then both fields fall back to the "unknown" sentinel
        assert envelope.cluster_id == "unknown"
        assert envelope.region == "unknown"

    def test_default_pii_class_is_internal(self):
        # Given a PagerDuty payload with no explicit PII signal
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "P700",
                    "title": "X",
                    "service": {"summary": "svc"},
                },
            },
        }
        settings_stub = _build_settings()

        # When envelope_from_pagerduty is invoked
        envelope = envelope_factory.envelope_from_pagerduty(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then pii_class is the firm-wide default of "internal"
        assert envelope.pii_class == "internal"

    def test_received_at_is_tz_aware_utc(self):
        # Given a PagerDuty payload and a known received_at
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "P800",
                    "title": "X",
                    "service": {"summary": "svc"},
                },
            },
        }
        settings_stub = _build_settings()

        # When envelope_from_pagerduty is invoked
        envelope = envelope_factory.envelope_from_pagerduty(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then the envelope received_at matches and is tz-aware UTC
        assert envelope.received_at == _FROZEN_RECEIVED_AT
        assert envelope.received_at.utcoffset() is not None


# ---------------------------------------------------------------------------
# Datadog envelope extraction
# ---------------------------------------------------------------------------


class TestEnvelopeFromDatadog:
    """Tests for ``envelope_from_datadog``."""

    def test_returns_envelope_with_tenant_from_namespace_tag(self):
        # Given a Datadog payload with a k8s_namespace tag
        payload = {
            "id": "1",
            "title": "Latency spike",
            "tags": "service:checkout,k8s_namespace:checkout-prod,env:prod",
            "alert_transition": "Triggered",
        }
        settings_stub = _build_settings()

        # When envelope_from_datadog is invoked
        envelope = envelope_factory.envelope_from_datadog(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then tenant_id derives from the k8s_namespace tag (highest priority)
        assert envelope.tenant_id == "checkout-prod"

    def test_returns_envelope_with_tenant_from_service_tag_when_no_namespace(self):
        # Given a Datadog payload with only a service tag
        payload = {
            "id": "2",
            "title": "Errors",
            "tags": "service:payments-api,env:prod",
            "alert_transition": "Triggered",
        }
        settings_stub = _build_settings()

        # When envelope_from_datadog is invoked
        envelope = envelope_factory.envelope_from_datadog(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then tenant_id derives from the service tag
        assert envelope.tenant_id == "payments-api"

    def test_returns_envelope_with_unknown_tenant_when_no_tags(self):
        # Given a Datadog payload with no service / namespace tags
        payload = {
            "id": "3",
            "title": "Unknown alert",
            "tags": "env:prod",
            "alert_transition": "Triggered",
        }
        settings_stub = _build_settings()

        # When envelope_from_datadog is invoked
        envelope = envelope_factory.envelope_from_datadog(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then tenant_id falls back to the "unknown" sentinel
        assert envelope.tenant_id == "unknown"

    def test_emits_warning_log_when_tenant_is_unknown(self):
        # Given a Datadog payload with no tenant identifiers
        payload = {
            "id": "4",
            "title": "Anonymous",
            "alert_transition": "Triggered",
        }
        settings_stub = _build_settings()

        # When envelope_from_datadog is invoked with the log_event hook patched
        with mock.patch.object(envelope_factory.logs, "log_event") as log_event_mock:
            envelope_factory.envelope_from_datadog(
                payload=payload,
                request_id=_FROZEN_REQUEST_ID,
                settings=settings_stub,
                received_at=_FROZEN_RECEIVED_AT,
            )

        # Then a structured warning event is emitted with the datadog source
        log_event_mock.assert_any_call(
            "envelope_tenant_unknown",
            params={
                "source": "datadog",
                "request_id": str(_FROZEN_REQUEST_ID),
                "fallback_used": True,
            },
        )

    def test_raises_when_strict_mode_and_tenant_unknown(self):
        # Given a Datadog payload with no tenant tag and strict mode enabled
        payload = {
            "id": "5",
            "title": "Anonymous",
            "alert_transition": "Triggered",
        }
        settings_stub = _build_settings()

        # When envelope_from_datadog is invoked with strict=True
        # Then EnvelopeIngressError is raised
        with pytest.raises(envelope_factory.EnvelopeIngressError) as raised:
            envelope_factory.envelope_from_datadog(
                payload=payload,
                request_id=_FROZEN_REQUEST_ID,
                settings=settings_stub,
                strict=True,
                received_at=_FROZEN_RECEIVED_AT,
            )
        assert raised.value.source == "datadog"


# ---------------------------------------------------------------------------
# Jira envelope extraction
# ---------------------------------------------------------------------------


class TestEnvelopeFromJira:
    """Tests for ``envelope_from_jira``."""

    def test_returns_envelope_with_tenant_from_project_key(self):
        # Given a Jira webhook payload with a project key on the issue
        payload = {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "id": "100",
                "key": "SUPPORT-1",
                "fields": {
                    "summary": "Login broken",
                    "project": {"key": "SUPPORT"},
                },
            },
        }
        settings_stub = _build_settings()

        # When envelope_from_jira is invoked
        envelope = envelope_factory.envelope_from_jira(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then tenant_id is the lowercased project key
        assert envelope.tenant_id == "support"

    def test_returns_envelope_with_unknown_tenant_when_no_project(self):
        # Given a Jira payload with no project field
        payload = {
            "webhookEvent": "jira:issue_created",
            "issue": {"id": "101", "key": "X-1", "fields": {"summary": "x"}},
        }
        settings_stub = _build_settings()

        # When envelope_from_jira is invoked
        envelope = envelope_factory.envelope_from_jira(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then tenant_id falls back to the "unknown" sentinel
        assert envelope.tenant_id == "unknown"

    def test_emits_warning_log_when_tenant_is_unknown(self):
        # Given a Jira payload with no project field
        payload = {
            "webhookEvent": "jira:issue_created",
            "issue": {"id": "102", "key": "X-1", "fields": {}},
        }
        settings_stub = _build_settings()

        # When envelope_from_jira is invoked with the log_event hook patched
        with mock.patch.object(envelope_factory.logs, "log_event") as log_event_mock:
            envelope_factory.envelope_from_jira(
                payload=payload,
                request_id=_FROZEN_REQUEST_ID,
                settings=settings_stub,
                received_at=_FROZEN_RECEIVED_AT,
            )

        # Then a structured warning event is emitted with the jira source
        log_event_mock.assert_any_call(
            "envelope_tenant_unknown",
            params={
                "source": "jira",
                "request_id": str(_FROZEN_REQUEST_ID),
                "fallback_used": True,
            },
        )

    def test_raises_when_strict_mode_and_tenant_unknown(self):
        # Given a Jira payload with no project field and strict mode enabled
        payload = {
            "webhookEvent": "jira:issue_created",
            "issue": {"id": "103", "key": "X-1", "fields": {}},
        }
        settings_stub = _build_settings()

        # When envelope_from_jira is invoked with strict=True
        # Then EnvelopeIngressError is raised
        with pytest.raises(envelope_factory.EnvelopeIngressError) as raised:
            envelope_factory.envelope_from_jira(
                payload=payload,
                request_id=_FROZEN_REQUEST_ID,
                settings=settings_stub,
                strict=True,
                received_at=_FROZEN_RECEIVED_AT,
            )
        assert raised.value.source == "jira"


# ---------------------------------------------------------------------------
# EnvelopeIngressError
# ---------------------------------------------------------------------------


class TestEnvelopeIngressError:
    """Tests for ``EnvelopeIngressError`` shape."""

    def test_carries_source_and_request_id(self):
        # Given the exception is raised with source + request_id
        error = envelope_factory.EnvelopeIngressError(
            source="pagerduty",
            request_id=_FROZEN_REQUEST_ID,
        )

        # When the attributes are read
        # Then they round-trip
        assert error.source == "pagerduty"
        assert error.request_id == _FROZEN_REQUEST_ID

    def test_str_contains_source_and_request_id(self):
        # Given an error instance
        error = envelope_factory.EnvelopeIngressError(
            source="datadog",
            request_id=_FROZEN_REQUEST_ID,
        )

        # When the error is rendered as a string
        rendered = str(error)

        # Then both fields appear so operators see them in logs
        assert "datadog" in rendered
        assert str(_FROZEN_REQUEST_ID) in rendered

    def test_missing_tenant_id_attribute_is_true(self):
        # Given an envelope ingress error
        error = envelope_factory.EnvelopeIngressError(
            source="pagerduty",
            request_id=_FROZEN_REQUEST_ID,
        )

        # When the missing_tenant_id attribute is read
        # Then it reads True so callers can branch without parsing the message
        assert error.missing_tenant_id is True


# ---------------------------------------------------------------------------
# Tenant slug sanitisation
# ---------------------------------------------------------------------------


class TestSanitizeTenantSlug:
    """Tests for the private slug sanitiser bounding tenant_id length."""

    def test_truncates_slug_to_63_characters(self):
        # Given a sanitised slug longer than the k8s namespace limit
        long_slug = "a" * 200

        # When the sanitiser runs
        sanitised = envelope_factory._sanitize_tenant_slug(long_slug)

        # Then the result fits within the 63-char k8s namespace cap
        assert len(sanitised) == 63
        assert sanitised == "a" * 63

    def test_short_slug_passes_through_unchanged(self):
        # Given a slug well below the limit
        short_slug = "pm-alpha"

        # When the sanitiser runs
        sanitised = envelope_factory._sanitize_tenant_slug(short_slug)

        # Then the slug is returned untouched
        assert sanitised == "pm-alpha"


# ---------------------------------------------------------------------------
# Returned envelope is a real, validated Envelope
# ---------------------------------------------------------------------------


class TestEnvelopeFactoryReturnsRealEnvelope:
    """Sanity tests confirming the factory returns a valid Envelope."""

    def test_pagerduty_returns_envelope_instance(self):
        # Given a populated payload
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "P1",
                    "title": "X",
                    "service": {"summary": "svc"},
                },
            },
        }
        settings_stub = _build_settings()

        # When the factory builds an envelope
        envelope = envelope_factory.envelope_from_pagerduty(
            payload=payload,
            request_id=_FROZEN_REQUEST_ID,
            settings=settings_stub,
            received_at=_FROZEN_RECEIVED_AT,
        )

        # Then the result is an Envelope instance (so to_log_context etc. work)
        assert isinstance(envelope, envelope_mod.Envelope)
