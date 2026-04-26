"""
Database-backed execution tracer for pipeline runs.

Replaces the in-memory TraceCollector with persistent tracing while
maintaining backward compatibility (exposes the same .record() and
.traces interface).
"""

from __future__ import annotations

import json
import uuid
from contextvars import Token
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ModelMessage

from sentinel import config as config_mod
from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.evaluation import costing
from sentinel.domain.pipeline import operations as pipeline_ops
from sentinel.domain.pipeline import types
from sentinel.plugins.toolsets import _runtime as runtime
from sentinel.utils import logs
from sentinel.utils import replay_bundle as bundle_mod


if TYPE_CHECKING:
    import databases


class ExecutionTracer(types.TraceCollector):
    """
    Record pipeline execution traces to the database.

    Inherits from TraceCollector for backward compatibility with the
    Streamlit chat UI (.record() and .traces).

    When db is None, tracing calls are no-ops but trace_id is still
    generated for correlation.
    """

    def __init__(self, *, db: databases.Database | None) -> None:
        super().__init__()
        self._db = db
        self._trace_id: uuid.UUID | None = None
        self._pipeline_run_id: uuid.UUID | None = None
        self._pipeline_started_at: datetime | None = None
        self._node_order: int = 0
        self._node_started_at: dict[uuid.UUID, datetime] = {}
        self._agent_cost_breakdowns: list[dict[str, Any]] = []
        # -- F4.7 ReplayBundle capture state --
        # Set when ``start_pipeline`` opens a replay capture; cleared by
        # ``complete_pipeline`` once the bundle is flushed back to disk.
        self._replay_token: Token[bundle_mod.ReplayBundleBuilder | None] | None = None
        self._replay_envelope: envelope_mod.Envelope | None = None
        self._replay_alert_payload: dict[str, Any] | None = None

    @property
    def trace_id(self) -> uuid.UUID | None:
        """Return the current trace correlation ID."""
        return self._trace_id

    @property
    def pipeline_run_id(self) -> uuid.UUID | None:
        """Return the current pipeline run ID."""
        return self._pipeline_run_id

    async def start_pipeline(
        self,
        *,
        pipeline_type: str,
        job_request_id: uuid.UUID | None = None,
        input_data: dict[str, Any] | None = None,
        # Replay snapshot fields
        input_hash: str | None = None,
        model_ids_json: list[str] | None = None,
        mcp_endpoints_json: list[str] | None = None,
        skill_activations_json: list[dict[str, str]] | None = None,
        prompt_version: str | None = None,
        prompt_sha256: str | None = None,
        prompt_text: str | None = None,
        agent_prompts_json: list[dict[str, str]] | None = None,
        # F4.7 ReplayBundle context — both must be supplied to enable capture.
        envelope: envelope_mod.Envelope | None = None,
        alert_payload: dict[str, Any] | None = None,
    ) -> None:
        """
        Record the start of a pipeline execution.

        :param pipeline_type: Pipeline name (e.g. "investigation").
        :param job_request_id: Associated job request UUID.
        :param input_data: Pipeline input data.
        :param input_hash: Deterministic SHA-256 of the canonical input.
        :param model_ids_json: LLM model identifiers used during this run.
        :param mcp_endpoints_json: MCP server endpoints available to the pipeline.
        :param skill_activations_json: Skills activated for the pipeline agents.
        :param prompt_version: Git-SHA-prefixed version tag of the system prompt.
        :param prompt_sha256: Content hash of the system prompt text.
        :param prompt_text: Full rendered system prompt text for replay.
        :param agent_prompts_json: Per-agent prompt metadata for multi-agent pipelines.
        :param envelope: Identity envelope for this run; together with
            ``alert_payload`` enables RFC §3.8 ReplayBundle capture when
            ``cfg.enable_replay_bundle`` is True.
        :param alert_payload: Raw alert/ticket dict the pipeline ran against;
            persisted into the bundle.
        """
        self._trace_id = uuid.uuid4()
        self._pipeline_started_at = datetime.now(tz=UTC)
        self._node_order = 0

        # -- F4.7: open a replay capture if both context inputs are present --
        # ``cfg.enable_replay_bundle`` is the single kill-switch; missing either
        # ``envelope`` or ``alert_payload`` keeps capture off (back-compat).
        if envelope is not None and alert_payload is not None:
            cfg = config_mod.get_config()
            if cfg.enable_replay_bundle:
                builder = bundle_mod.ReplayBundleBuilder()
                self._replay_token = runtime.bind_replay_builder(builder)
                self._replay_envelope = envelope
                self._replay_alert_payload = alert_payload
                logs.log_event(
                    "replay_bundle_capture_started",
                    params={
                        "trace_id": str(self._trace_id),
                        "tenant_id": envelope.tenant_id,
                    },
                )

        if self._db is None:
            self._pipeline_run_id = uuid.uuid4()
            return

        self._pipeline_run_id = await pipeline_ops.persist_pipeline_run(
            db=self._db,
            trace_id=self._trace_id,
            pipeline_type=pipeline_type,
            job_request_id=job_request_id,
            started_at=self._pipeline_started_at,
            input_json=input_data,
            input_hash=input_hash,
            model_ids_json=model_ids_json,
            mcp_endpoints_json=mcp_endpoints_json,
            skill_activations_json=skill_activations_json,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_text=prompt_text,
            agent_prompts_json=agent_prompts_json,
        )

    async def complete_pipeline(
        self,
        *,
        status: str,
        output_data: dict[str, Any] | None = None,
        error_message: str | None = None,
        final_reply: dict[str, Any] | None = None,
        runbook_id: str | None = None,
        runbook_version_sha: str | None = None,
    ) -> None:
        """
        Record the completion of a pipeline execution.

        :param status: Final status ("completed" or "failed").
        :param output_data: Pipeline output data.
        :param error_message: Error message if failed.
        :param final_reply: Structured final reply payload for replay.
        :param runbook_id: Matched runbook identifier (if any) for the bundle.
        :param runbook_version_sha: Pinned runbook content SHA for the bundle.
        """
        # Always flush replay capture if it was opened — release the ContextVar
        # token even when persistence fails so concurrent tasks aren't leaked.
        replay_bundle_dict, replay_bundle_sha = self._flush_replay_bundle(
            final_reply=final_reply,
            runbook_id=runbook_id,
            runbook_version_sha=runbook_version_sha,
        )

        if self._db is None or self._pipeline_run_id is None:
            return

        duration_ms = None
        if self._pipeline_started_at:
            delta = datetime.now(tz=UTC) - self._pipeline_started_at
            duration_ms = int(delta.total_seconds() * 1000)

        total_token_usage = None
        if self._agent_cost_breakdowns:
            total_token_usage = {
                "total_input_tokens": sum(
                    b.get("input_tokens") or 0 for b in self._agent_cost_breakdowns
                ),
                "total_output_tokens": sum(
                    b.get("output_tokens") or 0 for b in self._agent_cost_breakdowns
                ),
                "total_tokens": sum(
                    b.get("total_tokens") or 0 for b in self._agent_cost_breakdowns
                ),
                "total_cost_usd": sum(
                    b.get("cost_usd") or 0.0 for b in self._agent_cost_breakdowns
                ),
                "agent_breakdowns": self._agent_cost_breakdowns,
            }

        await pipeline_ops.complete_pipeline_run(
            db=self._db,
            run_id=self._pipeline_run_id,
            status=status,
            output_json=output_data,
            error_message=error_message,
            duration_ms=duration_ms,
            final_reply=final_reply,
            total_token_usage_json=total_token_usage,
            replay_bundle_json=replay_bundle_dict,
            replay_bundle_sha=replay_bundle_sha,
        )

    def _flush_replay_bundle(
        self,
        *,
        final_reply: dict[str, Any] | None,
        runbook_id: str | None,
        runbook_version_sha: str | None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """
        Freeze the active replay builder into a JSON-safe dict + sha pair.

        Returns ``(None, None)`` when no capture was opened in
        ``start_pipeline`` (back-compat path). Always releases the
        ``ContextVar`` token so subsequent tasks aren't leaked, even when
        canonicalisation raises.
        """
        token = self._replay_token
        if token is None:
            return None, None
        envelope = self._replay_envelope
        alert_payload = self._replay_alert_payload
        # Reset the slots up-front so re-entry can't double-flush even if
        # ``flush_replay_capture`` raises below.
        self._replay_token = None
        self._replay_envelope = None
        self._replay_alert_payload = None

        if envelope is None or alert_payload is None:
            # Defensive: token was set without paired context — release and bail.
            runtime.unbind_replay_builder(token)
            return None, None

        try:
            bundle = runtime.flush_replay_capture(
                token=token,
                envelope=envelope,
                alert_payload=alert_payload,
                runbook_id=runbook_id,
                runbook_version_sha=runbook_version_sha,
                final_outputs=final_reply or {},
            )
        except Exception as exc:
            logs.log_exception(
                exc,
                params={
                    "trace_id": str(self._trace_id) if self._trace_id else None,
                    "stage": "flush_replay_capture",
                },
            )
            return None, None

        if bundle is None:
            return None, None

        try:
            # Canonical JSON guarantees deterministic key ordering — round-trip
            # via ``json.loads`` to land on a JSONB-friendly dict whose nested
            # structure matches the bundle's canonical form bit-for-bit.
            bundle_dict = json.loads(bundle_mod.to_canonical_json(bundle))
            bundle_sha = bundle.bundle_sha
        except Exception as exc:
            logs.log_exception(
                exc,
                params={
                    "trace_id": str(self._trace_id) if self._trace_id else None,
                    "stage": "serialize_replay_bundle",
                },
            )
            return None, None

        logs.log_event(
            "replay_bundle_captured",
            params={
                "trace_id": str(self._trace_id) if self._trace_id else None,
                "replay_bundle_sha": bundle_sha,
                "tool_io_count": len(bundle.tool_io),
                "llm_io_count": len(bundle.llm_io),
            },
        )
        return bundle_dict, bundle_sha

    async def start_node(
        self,
        *,
        node_name: str,
        input_data: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        """
        Record the start of a graph node execution.

        :param node_name: Name of the graph node class.
        :param input_data: Node input data.
        :returns: The node execution UUID.
        """
        self._node_order += 1
        now = datetime.now(tz=UTC)

        if self._db is None or self._pipeline_run_id is None:
            node_id = uuid.uuid4()
            self._node_started_at[node_id] = now
            return node_id

        node_id = await pipeline_ops.persist_node_execution(
            db=self._db,
            trace_id=self._trace_id,  # type: ignore[arg-type]
            pipeline_run_id=self._pipeline_run_id,
            node_name=node_name,
            node_order=self._node_order,
            started_at=now,
            input_json=input_data,
        )
        self._node_started_at[node_id] = now
        return node_id

    async def complete_node(
        self,
        *,
        node_id: uuid.UUID,
        status: str,
        output_data: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        """
        Record the completion of a graph node execution.

        :param node_id: The node execution UUID from start_node().
        :param status: Final status ("completed" or "failed").
        :param output_data: Node output data.
        :param error_message: Error message if failed.
        """
        if self._db is None:
            return

        duration_ms = None
        started_at = self._node_started_at.pop(node_id, None)
        if started_at:
            delta = datetime.now(tz=UTC) - started_at
            duration_ms = int(delta.total_seconds() * 1000)

        await pipeline_ops.complete_node_execution(
            db=self._db,
            node_id=node_id,
            status=status,
            output_json=output_data,
            error_message=error_message,
            duration_ms=duration_ms,
        )

    async def record_agent_call(
        self,
        *,
        node_id: uuid.UUID,
        agent_name: str,
        model_id: str = "",
        messages: list[ModelMessage] | None = None,
        token_usage: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """
        Record a PydanticAI agent invocation.

        :param node_id: Parent node execution UUID.
        :param agent_name: Agent name.
        :param model_id: LLM model identifier.
        :param messages: Agent message history.
        :param token_usage: Token usage breakdown.
        :param duration_ms: Call duration in milliseconds.
        """
        if self._db is None or self._trace_id is None:
            return

        now = datetime.now(tz=UTC)

        # Serialise messages to JSON-safe format
        messages_json = None
        if messages:
            messages_json = [
                {"role": getattr(m, "role", "unknown"), "parts": str(m.parts)} for m in messages
            ]

        await pipeline_ops.persist_agent_call(
            db=self._db,
            trace_id=self._trace_id,
            node_execution_id=node_id,
            agent_name=agent_name,
            model_id=model_id,
            messages_json=messages_json,
            token_usage_json=token_usage,
            duration_ms=duration_ms,
            started_at=now,
            completed_at=now,
        )

    async def record_agent_result(
        self,
        *,
        node_id: uuid.UUID,
        agent_name: str,
        model_id: str,
        result: Any,
        duration_ms: int | None = None,
    ) -> None:
        """
        Extract token usage from a PydanticAI agent result and record the agent call.

        :param node_id: Parent node execution UUID.
        :param agent_name: Agent name.
        :param model_id: LLM model identifier.
        :param result: PydanticAI AgentRunResult — typed as Any to avoid hard coupling.
        :param duration_ms: Call duration in milliseconds.
        """
        token_usage: dict[str, Any] | None = None
        messages: list[ModelMessage] | None = None

        try:
            usage = result.usage()
            if usage is not None:
                input_tokens = getattr(usage, "request_tokens", None)
                output_tokens = getattr(usage, "response_tokens", None)
                total_tokens = getattr(usage, "total_tokens", None)

                cost_usd = None
                if input_tokens is not None and output_tokens is not None:
                    cost_usd = costing.estimate_cost_usd(
                        model_id=model_id,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )

                token_usage = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                    "cost_usd": cost_usd,
                }
        except Exception as exc:
            logs.log_exception(
                exc,
                params={"agent_name": agent_name, "model_id": model_id},
            )

        try:
            messages = result.all_messages()
        except Exception as exc:
            logs.log_exception(
                exc,
                params={"agent_name": agent_name, "model_id": model_id},
            )

        await self.record_agent_call(
            node_id=node_id,
            agent_name=agent_name,
            model_id=model_id,
            messages=messages,
            token_usage=token_usage,
            duration_ms=duration_ms,
        )

        if token_usage is not None:
            self._agent_cost_breakdowns.append(
                {
                    "agent_name": agent_name,
                    "model_id": model_id,
                    "input_tokens": token_usage.get("input_tokens"),
                    "output_tokens": token_usage.get("output_tokens"),
                    "total_tokens": token_usage.get("total_tokens"),
                    "cost_usd": token_usage.get("cost_usd"),
                }
            )
