"""
Unit tests for canonical_input_hash — deterministic SHA-256 hashing
of pipeline input payloads for replay matching.
"""

from __future__ import annotations

import datetime

from sentinel.domain.pipeline import queries


class TestCanonicalInputHash:
    def test_stable_across_dict_ordering(self) -> None:
        # Given two payloads with identical keys in different order
        payload_ab = {"a": 1, "b": 2}
        payload_ba = {"b": 2, "a": 1}

        # When hashing both payloads
        hash_ab = queries.canonical_input_hash(payload=payload_ab)
        hash_ba = queries.canonical_input_hash(payload=payload_ba)

        # Then both produce the same hash
        assert hash_ab == hash_ba

    def test_excludes_timestamp_keys(self) -> None:
        # Given a base payload and a copy with excluded timestamp/trace keys added
        base = {"alert_id": "abc-123", "severity": "high"}
        with_excluded = {
            **base,
            "timestamp": "2026-04-12T00:00:00Z",
            "received_at": "2026-04-12T00:01:00Z",
            "now": "2026-04-12T00:02:00Z",
            "run_id": "run-999",
            "trace_id": "trace-888",
            "pipeline_run_id": "pr-777",
            "created_at": "2026-04-12T00:03:00Z",
            "updated_at": "2026-04-12T00:04:00Z",
        }

        # When hashing both payloads
        hash_base = queries.canonical_input_hash(payload=base)
        hash_with_excluded = queries.canonical_input_hash(payload=with_excluded)

        # Then they produce the same hash because excluded keys are stripped
        assert hash_base == hash_with_excluded

    def test_datetime_values_serialized_to_string(self) -> None:
        # Given a payload containing a datetime value
        dt = datetime.datetime(2026, 4, 12, 10, 30, 0, tzinfo=datetime.UTC)
        payload = {"event_time": dt, "name": "test"}

        # When hashing the payload
        result = queries.canonical_input_hash(payload=payload)

        # Then a valid hex hash is returned (datetime did not cause an error)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_shape_is_64_char_hex(self) -> None:
        # Given a simple payload
        payload = {"key": "value"}

        # When hashing
        result = queries.canonical_input_hash(payload=payload)

        # Then the result is a 64-character lowercase hex string (SHA-256)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_empty_payload_produces_valid_hash(self) -> None:
        # Given an empty payload
        payload: dict[str, object] = {}

        # When hashing
        result = queries.canonical_input_hash(payload=payload)

        # Then a valid 64-character hex hash is returned
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_nested_data_produces_stable_hash(self) -> None:
        # Given a payload with nested dicts and lists
        payload = {
            "alert": {"id": "abc", "tags": ["critical", "prod"]},
            "metadata": {"source": "pagerduty"},
        }

        # When hashing the same payload twice
        first_hash = queries.canonical_input_hash(payload=payload)
        second_hash = queries.canonical_input_hash(payload=payload)

        # Then both calls produce the same hash
        assert first_hash == second_hash

    def test_different_payloads_produce_different_hashes(self) -> None:
        # Given two payloads with different content
        payload_a = {"alert_id": "abc"}
        payload_b = {"alert_id": "xyz"}

        # When hashing both
        hash_a = queries.canonical_input_hash(payload=payload_a)
        hash_b = queries.canonical_input_hash(payload=payload_b)

        # Then the hashes differ
        assert hash_a != hash_b
