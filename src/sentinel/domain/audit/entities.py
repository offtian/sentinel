from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

import attrs


@attrs.frozen
class AuditEntry:
    """
    Immutable audit trail entry for regulatory traceability.

    Every agent decision is recorded with enough context to replay the decision:
    the input hash, model ID, and prompt version allow deterministic verification.

    These entries are append-only in the database -- no UPDATE/DELETE permitted.
    """

    id: uuid.UUID
    timestamp: datetime
    actor: str  # "system:worker-xyz", "webhook:pagerduty", "user:john@co.com"
    action: str  # "alert.classified", "investigation.started", "remediation.suggested"
    resource_type: str  # "alert", "investigation", "job", "ticket"
    resource_id: str
    details_json: str  # Serialised context for this specific action
    input_hash: str  # SHA-256 of the input that triggered this action
    model_id: str = ""  # Which LLM model was used (empty for non-LLM actions)
    prompt_version: str = ""  # Version tag of the prompt (empty for non-LLM actions)


def compute_input_hash(*, payload: str) -> str:
    """Return SHA-256 hex digest of the given payload string."""
    return hashlib.sha256(payload.encode()).hexdigest()
