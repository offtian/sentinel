from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sentinel.domain.audit import entities


class TestAuditEntry:
    def test_immutable(self):
        # Given an audit entry
        entry = entities.AuditEntry(
            id=uuid.uuid4(),
            timestamp=datetime.now(tz=UTC),
            actor="system:worker-1",
            action="alert.classified",
            resource_type="alert",
            resource_id="alert-123",
            details_json='{"severity": "high"}',
            input_hash="abc123",
        )

        # Then it cannot be mutated
        try:
            entry.action = "changed"  # type: ignore[misc]
            mutated = True
        except AttributeError:
            mutated = False

        assert not mutated

    def test_optional_llm_fields_default_to_empty(self):
        # Given an audit entry for a non-LLM action
        entry = entities.AuditEntry(
            id=uuid.uuid4(),
            timestamp=datetime.now(tz=UTC),
            actor="user:admin",
            action="approval.granted",
            resource_type="investigation",
            resource_id="inv-456",
            details_json="{}",
            input_hash="def456",
        )

        # Then model_id and prompt_version are empty strings
        assert entry.model_id == ""
        assert entry.prompt_version == ""


class TestComputeInputHash:
    def test_deterministic(self):
        # Given the same payload string
        payload = '{"alert": "test-123"}'

        # When computing the hash twice
        hash_a = entities.compute_input_hash(payload=payload)
        hash_b = entities.compute_input_hash(payload=payload)

        # Then the hashes are identical
        assert hash_a == hash_b

    def test_different_payloads_produce_different_hashes(self):
        # Given two different payloads
        hash_a = entities.compute_input_hash(payload="payload-a")
        hash_b = entities.compute_input_hash(payload="payload-b")

        # Then their hashes differ
        assert hash_a != hash_b

    def test_returns_hex_string(self):
        # Given any payload
        result = entities.compute_input_hash(payload="test")

        # Then the result is a 64-character hex string (SHA-256)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)
