"""
Read operations for pipeline tracing records.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

import databases
from sqlalchemy import select
from sqlmodel import col

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.data.sql import tracing
from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.domain.pipeline import types as pipeline_types
from sentinel.utils import replay_bundle as replay_bundle_mod


_EXCLUDED_KEYS: frozenset[str] = frozenset(
    {
        "timestamp",
        "received_at",
        "now",
        "run_id",
        "trace_id",
        "pipeline_run_id",
        "created_at",
        "updated_at",
    }
)


def canonical_input_hash(*, payload: dict[str, Any]) -> str:
    """
    Return a deterministic SHA-256 hex digest of *payload*.

    Keys in ``_EXCLUDED_KEYS`` (timestamps, trace IDs) are stripped so that
    two runs of the same alert at different times produce the same hash.
    """
    filtered = {k: v for k, v in payload.items() if k not in _EXCLUDED_KEYS}
    canonical = json.dumps(filtered, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def fetch_pipeline_run(
    *,
    db: databases.Database,
    trace_id: uuid.UUID,
) -> dict[str, Any] | None:
    """
    Fetch a single pipeline run record by trace_id.

    :param db: The async database connection.
    :param trace_id: Correlation UUID used to look up the run.
    :returns: Row dict if found, or None.
    """
    query = select(tracing.PipelineRunRecord).where(
        col(tracing.PipelineRunRecord.trace_id) == trace_id
    )
    row = await db.fetch_one(query)
    if row is None:
        return None
    return dict(row._mapping)  # noqa: SLF001


async def fetch_node_executions(
    *,
    db: databases.Database,
    pipeline_run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """
    Fetch all node execution records for a given pipeline run.

    :param db: The async database connection.
    :param pipeline_run_id: UUID of the parent pipeline run.
    :returns: List of row dicts ordered by node_order ascending.
    """
    query = (
        select(tracing.NodeExecutionRecord)
        .where(col(tracing.NodeExecutionRecord.pipeline_run_id) == pipeline_run_id)
        .order_by(col(tracing.NodeExecutionRecord.node_order).asc())
    )
    rows = await db.fetch_all(query)
    return [dict(row._mapping) for row in rows]  # noqa: SLF001


async def fetch_agent_calls(
    *,
    db: databases.Database,
    node_execution_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """
    Fetch all agent call records for a given node execution.

    :param db: The async database connection.
    :param node_execution_id: UUID of the parent node execution.
    :returns: List of row dicts ordered by started_at ascending.
    """
    query = (
        select(tracing.AgentCallRecord)
        .where(col(tracing.AgentCallRecord.node_execution_id) == node_execution_id)
        .order_by(col(tracing.AgentCallRecord.started_at).asc())
    )
    rows = await db.fetch_all(query)
    return [dict(row._mapping) for row in rows]  # noqa: SLF001


async def fetch_replay_bundle(
    *,
    db: databases.Database,
    run_id: uuid.UUID,
) -> pipeline_types.ReplayBundle:
    """
    Return an immutable snapshot of a pipeline run for reproducibility.

    :param db: The async database connection.
    :param run_id: Primary key of the pipeline run record.
    :returns: A fully-populated ``ReplayBundle``.
    :raises pipeline_errors.ReplayBundleNotFoundError: if no row matches *run_id*.
    """
    query = select(tracing.PipelineRunRecord).where(col(tracing.PipelineRunRecord.id) == run_id)
    row = await db.fetch_one(query)
    if row is None:
        raise pipeline_errors.ReplayBundleNotFoundError(run_id)
    mapping = dict(row._mapping)  # noqa: SLF001
    return pipeline_types.ReplayBundle(
        run_id=mapping["id"],
        pipeline_type=mapping["pipeline_type"],
        started_at=mapping["started_at"],
        completed_at=mapping.get("completed_at"),
        prompt_version=mapping.get("prompt_version"),
        prompt_sha256=mapping.get("prompt_sha256"),
        prompt_text=mapping.get("prompt_text"),
        input_hash=mapping.get("input_hash"),
        model_ids=tuple(mapping.get("model_ids_json") or []),
        mcp_endpoints=tuple(mapping.get("mcp_endpoints_json") or []),
        skill_activations=tuple(mapping.get("skill_activations_json") or []),
        final_reply=mapping.get("final_reply"),
        input_payload=mapping.get("input_json"),
        agent_prompts=tuple(mapping.get("agent_prompts_json") or []),
    )


def _envelope_from_dict(payload: dict[str, Any]) -> envelope_mod.Envelope:
    """Reconstruct a frozen Envelope from its canonical-JSON dict form."""
    return envelope_mod.Envelope(
        request_id=uuid.UUID(payload["request_id"]),
        tenant_id=payload["tenant_id"],
        cluster_id=payload["cluster_id"],
        region=payload["region"],
        pii_class=payload["pii_class"],
        received_at=datetime.fromisoformat(payload["received_at"]),
    )


def _tool_entry_from_dict(payload: dict[str, Any]) -> replay_bundle_mod.ToolIOEntry:
    return replay_bundle_mod.ToolIOEntry(
        tool_name=payload["tool_name"],
        inputs=payload["inputs"],
        outputs=payload["outputs"],
        evidence_object_id=payload.get("evidence_object_id"),
        at=datetime.fromisoformat(payload["at"]),
    )


def _llm_entry_from_dict(payload: dict[str, Any]) -> replay_bundle_mod.LLMIOEntry:
    return replay_bundle_mod.LLMIOEntry(
        agent_name=payload["agent_name"],
        model_id=payload["model_id"],
        inputs=payload["inputs"],
        outputs=payload["outputs"],
        token_usage=payload.get("token_usage"),
        at=datetime.fromisoformat(payload["at"]),
    )


async def fetch_recorded_replay_bundle(
    *,
    db: databases.Database,
    run_id: uuid.UUID,
) -> replay_bundle_mod.ReplayBundle:
    """
    Return the persisted RFC §3.8 :class:`ReplayBundle` for *run_id*.

    Loads ``replay_bundle_json`` and ``replay_bundle_sha`` from the
    pipeline run row (written on the capture path by F4.7 slice A's
    tracer integration), reconstructs the frozen
    :class:`~sentinel.utils.replay_bundle.ReplayBundle`, and asserts the
    recomputed canonical sha matches the stored one. Sha drift surfaces
    canonicalisation regressions or DB corruption.

    :param db: The async database connection.
    :param run_id: Primary key of the pipeline run record.
    :raises pipeline_errors.ReplayBundleNotFoundError: if no row matches
        *run_id* or the row's ``replay_bundle_json`` column is null
        (a pre-F4.7 run, or one that did not opt into capture).
    :raises pipeline_errors.ReplayBundleSHAMismatchError: if the stored
        sha does not match the bundle's recomputed canonical sha.
    """
    query = select(tracing.PipelineRunRecord).where(
        col(tracing.PipelineRunRecord.id) == run_id,
    )
    row = await db.fetch_one(query)
    if row is None:
        raise pipeline_errors.ReplayBundleNotFoundError(run_id)
    mapping = dict(row._mapping)  # noqa: SLF001
    bundle_payload = mapping.get("replay_bundle_json")
    stored_sha = mapping.get("replay_bundle_sha")
    if bundle_payload is None or stored_sha is None:
        raise pipeline_errors.ReplayBundleNotFoundError(run_id)
    bundle = replay_bundle_mod.ReplayBundle(
        envelope=_envelope_from_dict(bundle_payload["envelope"]),
        alert_payload=bundle_payload["alert_payload"],
        runbook_id=bundle_payload.get("runbook_id"),
        runbook_version_sha=bundle_payload.get("runbook_version_sha"),
        tool_io=tuple(_tool_entry_from_dict(e) for e in bundle_payload.get("tool_io", [])),
        llm_io=tuple(_llm_entry_from_dict(e) for e in bundle_payload.get("llm_io", [])),
        final_outputs=bundle_payload["final_outputs"],
    )
    recomputed_sha = bundle.bundle_sha
    if recomputed_sha != stored_sha:
        raise pipeline_errors.ReplayBundleSHAMismatchError(
            run_id,
            stored_sha,
            recomputed_sha,
        )
    return bundle
