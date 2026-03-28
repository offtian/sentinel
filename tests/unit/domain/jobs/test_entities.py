from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sentinel.domain.jobs import entities


class TestJobRequest:
    def test_immutable(self):
        # Given a job request
        job = entities.JobRequest(
            id=uuid.uuid4(),
            job_type=entities.JobType.SRE_INVESTIGATION,
            payload_json='{"alert": "test"}',
            created_at=datetime.now(tz=UTC),
            requested_by="webhook:pagerduty",
        )

        # Then it cannot be mutated
        try:
            job.priority = 0  # type: ignore[misc]
            mutated = True
        except AttributeError:
            mutated = False

        assert not mutated

    def test_payload_hash_is_deterministic(self):
        # Given two job requests with the same payload
        payload = '{"alert": "test-123"}'
        now = datetime.now(tz=UTC)
        job_a = entities.JobRequest(
            id=uuid.uuid4(),
            job_type=entities.JobType.SRE_INVESTIGATION,
            payload_json=payload,
            created_at=now,
            requested_by="test",
        )
        job_b = entities.JobRequest(
            id=uuid.uuid4(),
            job_type=entities.JobType.SRE_INVESTIGATION,
            payload_json=payload,
            created_at=now,
            requested_by="test",
        )

        # Then their payload hashes match
        assert job_a.payload_hash == job_b.payload_hash

    def test_payload_hash_differs_for_different_payloads(self):
        # Given two job requests with different payloads
        now = datetime.now(tz=UTC)
        job_a = entities.JobRequest(
            id=uuid.uuid4(),
            job_type=entities.JobType.SRE_INVESTIGATION,
            payload_json='{"alert": "a"}',
            created_at=now,
            requested_by="test",
        )
        job_b = entities.JobRequest(
            id=uuid.uuid4(),
            job_type=entities.JobType.SRE_INVESTIGATION,
            payload_json='{"alert": "b"}',
            created_at=now,
            requested_by="test",
        )

        # Then their payload hashes differ
        assert job_a.payload_hash != job_b.payload_hash

    def test_default_priority(self):
        # Given a job request created without explicit priority
        job = entities.JobRequest(
            id=uuid.uuid4(),
            job_type=entities.JobType.SUPPORT_REVIEW,
            payload_json="{}",
            created_at=datetime.now(tz=UTC),
            requested_by="test",
        )

        # Then the default priority is 1 (high)
        assert job.priority == 1


class TestJobResult:
    def test_immutable(self):
        # Given a job result
        result = entities.JobResult(
            id=uuid.uuid4(),
            job_request_id=uuid.uuid4(),
            status=entities.JobStatus.COMPLETED,
        )

        # Then it cannot be mutated
        try:
            result.status = entities.JobStatus.FAILED  # type: ignore[misc]
            mutated = True
        except AttributeError:
            mutated = False

        assert not mutated


class TestMakeIdempotencyKey:
    def test_deterministic(self):
        # Given the same type and source ID
        key_a = entities.make_idempotency_key(
            job_type=entities.JobType.SRE_INVESTIGATION,
            source_id="alert-123",
        )
        key_b = entities.make_idempotency_key(
            job_type=entities.JobType.SRE_INVESTIGATION,
            source_id="alert-123",
        )

        # Then the keys are identical
        assert key_a == key_b

    def test_different_for_different_types(self):
        # Given the same source ID but different job types
        key_sre = entities.make_idempotency_key(
            job_type=entities.JobType.SRE_INVESTIGATION,
            source_id="id-123",
        )
        key_support = entities.make_idempotency_key(
            job_type=entities.JobType.SUPPORT_REVIEW,
            source_id="id-123",
        )

        # Then the keys differ
        assert key_sre != key_support

    def test_different_for_different_source_ids(self):
        # Given the same job type but different source IDs
        key_a = entities.make_idempotency_key(
            job_type=entities.JobType.SRE_INVESTIGATION,
            source_id="alert-111",
        )
        key_b = entities.make_idempotency_key(
            job_type=entities.JobType.SRE_INVESTIGATION,
            source_id="alert-222",
        )

        # Then the keys differ
        assert key_a != key_b
