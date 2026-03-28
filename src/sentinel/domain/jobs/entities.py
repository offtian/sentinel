from __future__ import annotations

import enum
import hashlib
import uuid
from datetime import datetime

import attrs


class JobType(enum.Enum):
    SRE_INVESTIGATION = "sre_investigation"
    SUPPORT_REVIEW = "support_review"


class JobStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@attrs.frozen
class JobRequest:
    """
    Immutable representation of a queued job.

    Each job carries a payload hash for tamper detection and an idempotency key
    to prevent duplicate processing -- both regulatory requirements for financial services.
    """

    id: uuid.UUID
    job_type: JobType
    payload_json: str
    created_at: datetime
    requested_by: str
    priority: int = 1  # 0=critical, 1=high, 2=normal
    idempotency_key: str = ""

    @property
    def payload_hash(self) -> str:
        """Return SHA-256 digest of the payload for audit/tamper detection."""
        return hashlib.sha256(self.payload_json.encode()).hexdigest()


@attrs.frozen
class JobResult:
    """
    Immutable representation of a completed (or failed) job execution.

    Records which worker pod executed the job for traceability.
    """

    id: uuid.UUID
    job_request_id: uuid.UUID
    status: JobStatus
    result_json: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    worker_id: str = ""


def make_idempotency_key(*, job_type: JobType, source_id: str) -> str:
    """
    Build a deterministic idempotency key from the job type and source identifier.

    This prevents the same alert/ticket from being enqueued twice.
    """
    raw = f"{job_type.value}:{source_id}"
    return hashlib.sha256(raw.encode()).hexdigest()
