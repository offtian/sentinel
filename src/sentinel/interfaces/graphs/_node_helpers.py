"""

Helpers for instrumenting Pydantic Graph pipeline nodes.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from sentinel.utils import metrics


def instrumented_node_run[T](
    *,
    pipeline: str,
    node: str,
    fn: Callable[[], Awaitable[T]],
) -> Callable[[], Awaitable[T]]:
    """

    Wrap a node run callable to record its duration as a metric.

    Records duration with status=ok on normal return and status=error if the
    callable raises. The original exception is re-raised unchanged.
    """

    async def _runner() -> T:
        start = time.perf_counter()
        status = "ok"
        try:
            return await fn()
        except Exception:
            status = "error"
            raise
        finally:
            duration = time.perf_counter() - start
            metrics.record_pipeline_node_duration(
                pipeline=pipeline,
                node=node,
                duration_seconds=duration,
                status=status,
            )

    return _runner
