"""Unit tests for the F2.1/F2.8 ``Envelope`` identity primitive.

Exercises construction, immutability, span-attribute shape, and
the PII redaction rule applied at the ``to_log_context`` boundary.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta, timezone

import attrs
import pytest

from sentinel.data.primitives import envelope as envelope_mod


_FIXED_REQUEST_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
_FIXED_RECEIVED_AT = datetime(2026, 4, 25, 12, 30, 45, tzinfo=UTC)


def _build_envelope(
    *,
    pii_class: envelope_mod.PIIClass = "internal",
    tenant_id: str = "pm-alpha",
) -> envelope_mod.Envelope:
    return envelope_mod.Envelope(
        request_id=_FIXED_REQUEST_ID,
        tenant_id=tenant_id,
        cluster_id="dev-eu-west-1",
        region="eu-west-1",
        pii_class=pii_class,
        received_at=_FIXED_RECEIVED_AT,
    )


class TestEnvelopeConstruction:
    """Tests for constructing an ``Envelope`` per RFC §3.1."""

    def test_constructs_with_all_required_fields(self) -> None:
        # Given the six RFC §3.1 fields populated with valid values
        # When the envelope is constructed
        built = _build_envelope()

        # Then every field round-trips unchanged
        assert built.request_id == _FIXED_REQUEST_ID
        assert built.tenant_id == "pm-alpha"
        assert built.cluster_id == "dev-eu-west-1"
        assert built.region == "eu-west-1"
        assert built.pii_class == "internal"
        assert built.received_at == _FIXED_RECEIVED_AT

    def test_is_immutable_when_frozen(self) -> None:
        # Given a constructed envelope
        built = _build_envelope()

        # When a field is reassigned
        # Then attrs raises FrozenInstanceError
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            built.tenant_id = "another-tenant"  # type: ignore[misc]

    def test_uses_kw_only_construction(self) -> None:
        # Given a positional argument
        # When constructed positionally
        # Then attrs raises TypeError because kw_only=True
        with pytest.raises(TypeError):
            envelope_mod.Envelope(_FIXED_REQUEST_ID)  # type: ignore[misc]

    def test_received_at_must_be_timezone_aware(self) -> None:
        # Given a naive datetime
        naive = datetime(2026, 4, 25, 12, 30, 45)  # noqa: DTZ001

        # When the envelope is constructed with the naive datetime
        # Then attrs raises ValueError because tz-aware UTC is required
        with pytest.raises(ValueError, match="tz-aware"):
            envelope_mod.Envelope(
                request_id=_FIXED_REQUEST_ID,
                tenant_id="pm-alpha",
                cluster_id="dev-eu-west-1",
                region="eu-west-1",
                pii_class="internal",
                received_at=naive,
            )

    def test_received_at_must_be_utc_not_just_tz_aware(self) -> None:
        # Given a tz-aware datetime in a non-UTC zone
        non_utc = datetime(2026, 4, 25, 12, 30, 45, tzinfo=timezone(timedelta(hours=-8)))

        # When the envelope is constructed with the non-UTC datetime
        # Then attrs raises ValueError because UTC offset must be zero
        with pytest.raises(ValueError, match="tz-aware UTC"):
            envelope_mod.Envelope(
                request_id=_FIXED_REQUEST_ID,
                tenant_id="pm-alpha",
                cluster_id="dev-eu-west-1",
                region="eu-west-1",
                pii_class="internal",
                received_at=non_utc,
            )


class TestEnvelopeToSpanAttributes:
    """Tests for the OTel span-attribute mapping per RFC §13.2."""

    def test_returns_six_envelope_owned_mandatory_keys(self) -> None:
        # Given a constructed envelope
        built = _build_envelope()

        # When to_span_attributes is called
        attributes = built.to_span_attributes()

        # Then the 6 envelope-owned mandatory keys are present alongside
        # the Langfuse-namespaced session/user attributes that promote
        # spans into the Langfuse Sessions/Users tabs
        assert set(attributes) == {
            "request_id",
            "tenant_id",
            "cluster_id",
            "region",
            "pii_class",
            "received_at",
            "langfuse.session.id",
            "langfuse.user.id",
        }
        assert attributes["langfuse.session.id"] == str(built.request_id)
        assert attributes["langfuse.user.id"] == built.tenant_id

    def test_received_at_is_iso_8601_string(self) -> None:
        # Given a constructed envelope with a known received_at
        built = _build_envelope()

        # When to_span_attributes is called
        attributes = built.to_span_attributes()

        # Then received_at is the ISO 8601 string of the datetime
        assert attributes["received_at"] == _FIXED_RECEIVED_AT.isoformat()

    def test_request_id_is_string_form_of_uuid(self) -> None:
        # Given a constructed envelope with a known request_id
        built = _build_envelope()

        # When to_span_attributes is called
        attributes = built.to_span_attributes()

        # Then request_id is the canonical str form of the UUID
        assert attributes["request_id"] == str(_FIXED_REQUEST_ID)

    def test_carries_tenant_id_unredacted(self) -> None:
        # Given a confidential-class envelope
        built = _build_envelope(pii_class="confidential", tenant_id="pm-alpha")

        # When to_span_attributes is called
        attributes = built.to_span_attributes()

        # Then tenant_id is the raw value — span attributes are not the
        # log-redaction boundary; the pii_class is recorded so the
        # exporter can decide what to do downstream
        assert attributes["tenant_id"] == "pm-alpha"
        assert attributes["pii_class"] == "confidential"


class TestEnvelopeToLogContext:
    """Tests for structlog binding context with F2.8 PII redaction."""

    def test_includes_raw_tenant_id_for_public_class(self) -> None:
        # Given a public-class envelope
        built = _build_envelope(pii_class="public", tenant_id="pm-alpha")

        # When to_log_context is called
        bound = built.to_log_context()

        # Then the raw tenant_id is bound and no tenant_hash is present
        assert bound["tenant_id"] == "pm-alpha"
        assert "tenant_hash" not in bound

    def test_includes_raw_tenant_id_for_internal_class(self) -> None:
        # Given an internal-class envelope
        built = _build_envelope(pii_class="internal", tenant_id="pm-alpha")

        # When to_log_context is called
        bound = built.to_log_context()

        # Then the raw tenant_id is bound and no tenant_hash is present
        assert bound["tenant_id"] == "pm-alpha"
        assert "tenant_hash" not in bound

    def test_redacts_tenant_id_for_confidential_class(self) -> None:
        # Given a confidential-class envelope
        built = _build_envelope(pii_class="confidential", tenant_id="pm-alpha")

        # When to_log_context is called
        bound = built.to_log_context()

        # Then tenant_id is removed and tenant_hash is present
        assert "tenant_id" not in bound
        assert "tenant_hash" in bound
        assert len(bound["tenant_hash"]) == 12

    def test_redacts_tenant_id_for_mnpi_class(self) -> None:
        # Given an MNPI-class envelope
        built = _build_envelope(pii_class="mnpi", tenant_id="pm-alpha")

        # When to_log_context is called
        bound = built.to_log_context()

        # Then tenant_id is removed and tenant_hash is present
        assert "tenant_id" not in bound
        assert "tenant_hash" in bound
        assert len(bound["tenant_hash"]) == 12

    def test_tenant_hash_is_first_12_chars_of_sha256(self) -> None:
        # Given a confidential-class envelope with a known tenant_id
        built = _build_envelope(pii_class="confidential", tenant_id="pm-alpha")
        expected_hash = hashlib.sha256(b"pm-alpha").hexdigest()[:12]

        # When to_log_context is called
        bound = built.to_log_context()

        # Then tenant_hash matches the truncated sha256 of the raw id
        assert bound["tenant_hash"] == expected_hash

    def test_tenant_hash_is_deterministic_across_calls(self) -> None:
        # Given two redacted-class envelopes (confidential and mnpi) sharing a tenant_id
        confidential_built = _build_envelope(pii_class="confidential", tenant_id="pm-alpha")
        mnpi_built = _build_envelope(pii_class="mnpi", tenant_id="pm-alpha")

        # When to_log_context is called on both
        confidential_bound = confidential_built.to_log_context()
        mnpi_bound = mnpi_built.to_log_context()

        # Then the hash is identical regardless of pii_class or call site
        assert confidential_bound["tenant_hash"] == mnpi_bound["tenant_hash"]

    def test_request_id_is_always_present(self) -> None:
        # Given envelopes spanning every pii_class
        envelopes = [
            _build_envelope(pii_class="public"),
            _build_envelope(pii_class="internal"),
            _build_envelope(pii_class="confidential"),
            _build_envelope(pii_class="mnpi"),
        ]

        # When to_log_context is called on each
        bound_contexts = [built.to_log_context() for built in envelopes]

        # Then request_id is bound on every context (UUIDs are not PII)
        for bound in bound_contexts:
            assert bound["request_id"] == str(_FIXED_REQUEST_ID)

    def test_returns_string_only_values(self) -> None:
        # Given a constructed envelope
        built = _build_envelope()

        # When to_log_context is called
        bound = built.to_log_context()

        # Then every value is a string suitable for structlog binding
        for key, value in bound.items():
            assert isinstance(value, str), f"{key} must be str, got {type(value)}"


class TestIsRedactedPIIClass:
    """Tests for the public ``is_redacted_pii_class`` predicate."""

    def test_returns_true_for_confidential(self) -> None:
        # Given the confidential PII class
        # When is_redacted_pii_class is called
        # Then the predicate returns True
        assert envelope_mod.is_redacted_pii_class("confidential") is True

    def test_returns_true_for_mnpi(self) -> None:
        # Given the mnpi PII class
        # When is_redacted_pii_class is called
        # Then the predicate returns True
        assert envelope_mod.is_redacted_pii_class("mnpi") is True

    def test_returns_false_for_internal(self) -> None:
        # Given the internal PII class
        # When is_redacted_pii_class is called
        # Then the predicate returns False
        assert envelope_mod.is_redacted_pii_class("internal") is False

    def test_returns_false_for_public(self) -> None:
        # Given the public PII class
        # When is_redacted_pii_class is called
        # Then the predicate returns False
        assert envelope_mod.is_redacted_pii_class("public") is False

    def test_redacted_set_matches_predicate(self) -> None:
        # Given the public REDACTED_PII_CLASSES set
        redacted = envelope_mod.REDACTED_PII_CLASSES

        # When is_redacted_pii_class is called for every member
        # Then the predicate agrees with the set on each PII class
        for pii_class in ("public", "internal", "confidential", "mnpi"):
            assert envelope_mod.is_redacted_pii_class(pii_class) == (pii_class in redacted)
