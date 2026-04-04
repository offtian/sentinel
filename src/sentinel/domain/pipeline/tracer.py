"""
Database-backed execution tracer for pipeline runs.

Replaces the in-memory TraceCollector with persistent tracing while
maintaining backward compatibility (exposes the same .record() and
.traces interface).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ModelMessage

from sentinel.domain.pipeline import operations as pipeline_ops
from sentinel.domain.pipeline import types


if TYPE_CHECKING:
    import databases


class ExecutionTracer:
    """
    Record pipeline execution traces to the database.

    Also satisfies the TraceCollector interface for backward compatibility
    with the Streamlit chat UI.

    When db is None, tracing calls are no-ops but trace_id is still
    generated for correlation.
    """

    def __init__(self, *, db: databases.Database | None) -> None:
        self._db = db
        self._trace_id: uuid.UUID | None = None
        self._pipeline_run_id: uuid.UUID | None = None
        self._pipeline_started_at: datetime | None = None
        self._node_order: int = 0
        self._node_started_at: dict[uuid.UUID, datetime] = {}

        # TraceCollector backward compatibility
        self.traces: list[types.AgentTrace] = []

    @property
    def trace_id(self) -> uuid.UUID | None:
        """Return the current trace correlation ID."""
        return self._trace_id

    @property
    def pipeline_run_id(self) -> uuid.UUID | None:
        """Return the current pipeline run ID."""
        return self._pipeline_run_id

    def record(self, *, agent_name: str, messages: list[ModelMessage]) -> None:
        """
        Record an agent trace (TraceCollector interface).

        Accumulates traces in-memory for Streamlit UI.
        """
        self.traces.append(types.AgentTrace(agent_name=agent_name, messages=messages))

    async def start_pipeline(
        self,
        *,
        pipeline_type: str,
        job_request_id: uuid.UUID | None = None,
        input_data: dict[str, Any] | None = None,
    ) -> None:
        """
        Record the start of a pipeline execution.

        :param pipeline_type: Pipeline name (e.g. "sre_investigation").
        :param job_request_id: Associated job request UUID.
        :param input_data: Pipeline input data.
        """
        self._trace_id = uuid.uuid4()
        self._pipeline_started_at = datetime.now(tz=UTC)
        self._node_order = 0

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
        )

    async def complete_pipeline(
        self,
        *,
        status: str,
        output_data: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        """
        Record the completion of a pipeline execution.

        :param status: Final status ("completed" or "failed").
        :param output_data: Pipeline output data.
        :param error_message: Error message if failed.
        """
        if self._db is None or self._pipeline_run_id is None:
            return

        duration_ms = None
        if self._pipeline_started_at:
            delta = datetime.now(tz=UTC) - self._pipeline_started_at
            duration_ms = int(delta.total_seconds() * 1000)

        await pipeline_ops.complete_pipeline_run(
            db=self._db,
            run_id=self._pipeline_run_id,
            status=status,
            output_json=output_data,
            error_message=error_message,
            duration_ms=duration_ms,
        )

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
