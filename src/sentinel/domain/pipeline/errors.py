from __future__ import annotations

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
        super().__init__(
            f"Pipeline node '{node_error.node_name}' failed: {node_error.message}"
        )
