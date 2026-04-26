"""
RFC §3.8 ReplayBundle — immutable per-run reproducibility snapshot.

A ``ReplayBundle`` is everything F4.7 (the replay CLI) and F4.8 (30-run
determinism CI) need to re-execute a pipeline bit-for-bit:

- ``envelope`` — the request identity envelope minted at ingress.
- ``alert_payload`` — the raw alert/ticket dict the pipeline ran against.
- ``runbook_id`` / ``runbook_version_sha`` — which runbook (if any) drove
  the run, pinned to its content SHA so a runbook edit invalidates replay.
- ``tool_io`` — every tool invocation in invocation order (F4.6 capture).
- ``llm_io`` — every LLM agent invocation in invocation order (F4.6 capture).
- ``final_outputs`` — the pipeline's final reply payload.
- ``bundle_sha`` — sha256 over the canonical JSON of all of the above; the
  same inputs always produce the same bundle, the same bundle always
  produces the same SHA. Drift in any field changes the SHA.

This module is the F4.5 RFC contract. The legacy
``sentinel.domain.pipeline.types.ReplayBundle`` is kept side-by-side until
F4.7 reroutes the CLI and persistence dual-tracks the new shape (see plan
``docs/plans/sentinel-foundations-f4-replay-bundle.md``).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

import attrs

from sentinel.data.primitives import envelope as envelope_mod


@attrs.frozen(kw_only=True, slots=True)
class ToolIOEntry:
    """
    One captured tool invocation (RFC §3.8 ``tool_io[i]``).

    ``inputs`` is the kwargs dict the tool was called with; ``outputs`` is
    the JSON-serialisable return value. ``evidence_object_id`` is the
    optional S3/blob pointer where large outputs are pinned (small outputs
    can be stored inline). ``at`` is the wall-clock timestamp at call time.
    """

    tool_name: str
    inputs: dict[str, Any]
    outputs: Any
    evidence_object_id: str | None = None
    at: datetime


@attrs.frozen(kw_only=True, slots=True)
class LLMIOEntry:
    """
    One captured LLM agent invocation (RFC §3.8 ``llm_io[i]``).

    ``inputs`` carries the prompt / message bundle handed to the agent;
    ``outputs`` carries the structured response (model output coerced to
    a JSON-safe representation by the caller). ``token_usage`` is the
    optional usage dict (``input_tokens`` / ``output_tokens`` / ...) lifted
    from the PydanticAI ``AgentRunResult.usage()``.
    """

    agent_name: str
    model_id: str
    inputs: dict[str, Any]
    outputs: Any
    token_usage: dict[str, Any] | None = None
    at: datetime


def _json_default(value: Any) -> Any:
    """Coerce non-JSON-native values to deterministic strings for canonicalisation."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if attrs.has(type(value)):
        return attrs.asdict(value, recurse=True)
    return str(value)


def _envelope_to_jsonable(env: envelope_mod.Envelope) -> dict[str, str]:
    """Render an Envelope into a deterministic JSON-safe dict."""
    return {
        "request_id": str(env.request_id),
        "tenant_id": env.tenant_id,
        "cluster_id": env.cluster_id,
        "region": env.region,
        "pii_class": env.pii_class,
        "received_at": env.received_at.isoformat(),
    }


def _tool_entry_to_jsonable(entry: ToolIOEntry) -> dict[str, Any]:
    return {
        "tool_name": entry.tool_name,
        "inputs": entry.inputs,
        "outputs": entry.outputs,
        "evidence_object_id": entry.evidence_object_id,
        "at": entry.at.isoformat(),
    }


def _llm_entry_to_jsonable(entry: LLMIOEntry) -> dict[str, Any]:
    return {
        "agent_name": entry.agent_name,
        "model_id": entry.model_id,
        "inputs": entry.inputs,
        "outputs": entry.outputs,
        "token_usage": entry.token_usage,
        "at": entry.at.isoformat(),
    }


def _bundle_to_jsonable(bundle: ReplayBundle) -> dict[str, Any]:
    """Render a ReplayBundle into a deterministic JSON-safe dict."""
    return {
        "alert_payload": bundle.alert_payload,
        "envelope": _envelope_to_jsonable(bundle.envelope),
        "final_outputs": bundle.final_outputs,
        "llm_io": [_llm_entry_to_jsonable(e) for e in bundle.llm_io],
        "runbook_id": bundle.runbook_id,
        "runbook_version_sha": bundle.runbook_version_sha,
        "tool_io": [_tool_entry_to_jsonable(e) for e in bundle.tool_io],
    }


def to_canonical_json(bundle: ReplayBundle) -> str:
    """
    Return the canonical JSON encoding of *bundle*.

    Canonical means: keys sorted at every level, datetimes/UUIDs serialised
    via ISO/string forms, no whitespace beyond standard separators. The
    same bundle always produces the same string regardless of dict
    insertion order.
    """
    return json.dumps(
        _bundle_to_jsonable(bundle),
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
        ensure_ascii=False,
    )


def _compute_bundle_sha(bundle: ReplayBundle) -> str:
    """Return the sha256 hex digest of a bundle's canonical JSON."""
    return hashlib.sha256(to_canonical_json(bundle).encode("utf-8")).hexdigest()


@attrs.frozen(kw_only=True, slots=True)
class ReplayBundle:
    """
    RFC §3.8 reproducibility snapshot for a single pipeline run.

    All fields are immutable. ``bundle_sha`` is a derived property — it is
    the sha256 of the canonical JSON of every other field, computed lazily.
    Two bundles built from identical inputs produce identical
    ``bundle_sha`` values; any drift in any field changes the SHA.
    """

    envelope: envelope_mod.Envelope
    alert_payload: dict[str, Any]
    runbook_id: str | None
    runbook_version_sha: str | None
    tool_io: tuple[ToolIOEntry, ...] = ()
    llm_io: tuple[LLMIOEntry, ...] = ()
    final_outputs: dict[str, Any]

    @property
    def bundle_sha(self) -> str:
        """Return the sha256 hex digest over the canonical JSON of this bundle."""
        return _compute_bundle_sha(self)


class ReplayBundleBuilder:
    """
    Mutable accumulator that tool/LLM wrappers append to during a run.

    F4.6 binds an instance to a ``ContextVar`` at pipeline start, every
    toolset wrapper appends a ``ToolIOEntry`` per call, and the pipeline
    tracer's ``complete_pipeline()`` calls :meth:`build` to freeze the
    accumulated I/O into an immutable :class:`ReplayBundle`.

    The builder is the *only* mutable surface in this module — everything
    it produces is frozen.
    """

    def __init__(self) -> None:
        self._tool_entries: list[ToolIOEntry] = []
        self._llm_entries: list[LLMIOEntry] = []

    def record_tool_io(self, entry: ToolIOEntry) -> None:
        """Append a tool invocation in invocation order."""
        self._tool_entries.append(entry)

    def record_llm_io(self, entry: LLMIOEntry) -> None:
        """Append an LLM invocation in invocation order."""
        self._llm_entries.append(entry)

    def build(
        self,
        *,
        envelope: envelope_mod.Envelope,
        alert_payload: dict[str, Any],
        runbook_id: str | None,
        runbook_version_sha: str | None,
        final_outputs: dict[str, Any],
    ) -> ReplayBundle:
        """
        Freeze the accumulated tool and LLM I/O into an immutable bundle.

        The caller supplies the envelope / alert payload / runbook
        identifiers / final outputs — those are pipeline-level facts that
        the builder does not track itself.

        :param envelope: The identity envelope minted at ingress.
        :param alert_payload: The raw alert/ticket dict the pipeline ran against.
        :param runbook_id: Matched runbook identifier, or None.
        :param runbook_version_sha: Pinned runbook content SHA, or None.
        :param final_outputs: The pipeline's final reply payload.
        :returns: An immutable :class:`ReplayBundle` snapshot.
        """
        return ReplayBundle(
            envelope=envelope,
            alert_payload=alert_payload,
            runbook_id=runbook_id,
            runbook_version_sha=runbook_version_sha,
            tool_io=tuple(self._tool_entries),
            llm_io=tuple(self._llm_entries),
            final_outputs=final_outputs,
        )
