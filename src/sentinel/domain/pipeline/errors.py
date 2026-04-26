from __future__ import annotations

import uuid
from typing import Any

import attrs


@attrs.frozen
class NodeError:
    """
    Capture a pipeline node failure with enough context for logging and degraded continuation.

    Immutable value object -- safe to pass between nodes and log as structured data.
    """

    node_name: str
    error_type: str
    message: str
    is_recoverable: bool = False


class PipelineNodeFailed(Exception):
    """
    Raise when a critical pipeline node fails and the pipeline cannot continue.

    Wraps a ``NodeError`` so callers can inspect structured failure details.
    """

    def __init__(self, *, node_error: NodeError) -> None:
        self.node_error = node_error
        super().__init__(f"Pipeline node '{node_error.node_name}' failed: {node_error.message}")


class ReplayBundleNotFoundError(Exception):
    """
    Raise when no pipeline run record exists for the requested run_id.
    """

    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(f"No pipeline run found for run_id={run_id}")
        self.run_id = run_id


class ReplayBundleSHAMismatchError(Exception):
    """
    Raise when a persisted replay bundle's recomputed sha differs from the stored one.

    Surfaces canonicalisation regressions and database corruption: the stored
    column is sha256 over the canonical JSON of the bundle at write time,
    so a mismatch on read means the bundle's bytes have drifted from their
    digest. Either way, replay against this row is unsafe.
    """

    def __init__(
        self,
        run_id: uuid.UUID,
        stored_sha: str,
        recomputed_sha: str,
    ) -> None:
        self.run_id = run_id
        self.stored_sha = stored_sha
        self.recomputed_sha = recomputed_sha
        super().__init__(
            f"Replay bundle sha mismatch for run_id={run_id}: "
            f"stored={stored_sha} recomputed={recomputed_sha}"
        )


class RecordedReplayMismatchError(Exception):
    """
    Raise when a replay run's tool/LLM call doesn't match the recorded entry.

    Replay is strict on order, name, and inputs: any drift between what the
    re-executing pipeline asks for and what was originally recorded points
    to a determinism regression that F4.8's CI guards against. ``kind``
    discriminates the category (``tool`` / ``tool_name`` / ``tool_args`` /
    ``llm``); ``reason`` carries optional extra context (for example
    ``"exhausted"`` when the recorded queue ran out before the pipeline
    finished).
    """

    def __init__(
        self,
        *,
        kind: str,
        expected: Any,
        actual: Any,
        reason: str | None = None,
    ) -> None:
        self.kind = kind
        self.expected = expected
        self.actual = actual
        self.reason = reason
        message = f"Recorded replay mismatch ({kind}): expected={expected!r} actual={actual!r}"
        if reason:
            message += f" ({reason})"
        super().__init__(message)
