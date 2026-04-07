"""

Cross-cutting metrics recording helpers backed by OpenTelemetry.

All recorder functions swallow exceptions and no-op when metrics are
disabled — metrics must never break the application.
"""

from __future__ import annotations

from typing import Any

from sentinel.utils import logs


# Module-level singletons populated by `init_meters()`. They start as None so
# helpers no-op cleanly until OTel has been initialised.
_meter: Any | None = None
_investigations_total: Any | None = None
_reviews_total: Any | None = None
_pipeline_node_duration: Any | None = None
_confidence_score: Any | None = None
_approval_decisions_total: Any | None = None
_llm_calls_total: Any | None = None
_llm_call_duration: Any | None = None
_jobs_processed_total: Any | None = None
_job_duration: Any | None = None
_job_queue_depth: Any | None = None

_warned_once = False


def _safe_record(name: str, fn: Any) -> None:
    """

    Run a recording callable and swallow any exception, logging once at WARN.
    """
    global _warned_once  # noqa: PLW0603
    try:
        fn()
    except Exception as exc:
        if not _warned_once:
            logs.log_exception(exc, params={"recorder": name})
            _warned_once = True


def init_meters(*, meter: Any) -> None:
    """

    Populate module-level instrument singletons from an OTel meter.
    """
    global _meter  # noqa: PLW0603
    global _investigations_total, _reviews_total  # noqa: PLW0603
    global _pipeline_node_duration, _confidence_score  # noqa: PLW0603
    global _approval_decisions_total  # noqa: PLW0603
    global _llm_calls_total, _llm_call_duration  # noqa: PLW0603
    global _jobs_processed_total, _job_duration, _job_queue_depth  # noqa: PLW0603

    _meter = meter

    _investigations_total = meter.create_counter(
        "sentinel_investigations_total",
        description="Total SRE investigations completed",
    )
    _reviews_total = meter.create_counter(
        "sentinel_reviews_total",
        description="Total support reviews completed",
    )
    _pipeline_node_duration = meter.create_histogram(
        "sentinel_pipeline_node_duration_seconds",
        unit="s",
        description="Duration of pipeline node execution",
    )
    _confidence_score = meter.create_histogram(
        "sentinel_confidence_score",
        description="Confidence score recorded by DetermineConfidence nodes",
    )
    _approval_decisions_total = meter.create_counter(
        "sentinel_approval_decisions_total",
        description="Approval decisions recorded by reviewers or auto-approval",
    )
    _llm_calls_total = meter.create_counter(
        "sentinel_llm_calls_total",
        description="LLM calls dispatched via the LiteLLM gateway",
    )
    _llm_call_duration = meter.create_histogram(
        "sentinel_llm_call_duration_seconds",
        unit="s",
        description="Duration of LLM calls dispatched via the LiteLLM gateway",
    )
    _jobs_processed_total = meter.create_counter(
        "sentinel_jobs_processed_total",
        description="Worker jobs processed",
    )
    _job_duration = meter.create_histogram(
        "sentinel_job_duration_seconds",
        unit="s",
        description="Worker job duration",
    )
    _job_queue_depth = meter.create_gauge(
        "sentinel_job_queue_depth",
        description="Current job queue depth by job type and status",
    )


def reset_meters() -> None:
    """

    Reset all instrument singletons. Used by tests.
    """
    global _meter  # noqa: PLW0603
    global _investigations_total, _reviews_total  # noqa: PLW0603
    global _pipeline_node_duration, _confidence_score  # noqa: PLW0603
    global _approval_decisions_total  # noqa: PLW0603
    global _llm_calls_total, _llm_call_duration  # noqa: PLW0603
    global _jobs_processed_total, _job_duration, _job_queue_depth  # noqa: PLW0603
    global _warned_once  # noqa: PLW0603

    _meter = None
    _investigations_total = None
    _reviews_total = None
    _pipeline_node_duration = None
    _confidence_score = None
    _approval_decisions_total = None
    _llm_calls_total = None
    _llm_call_duration = None
    _jobs_processed_total = None
    _job_duration = None
    _job_queue_depth = None
    _warned_once = False


def record_investigation_completed(
    *,
    confidence_label: str,
    approval_required: bool,
    outcome: str,
) -> None:
    """

    Record that an SRE investigation has reached a terminal state.
    """
    if _investigations_total is None:
        return
    _safe_record(
        "investigations_total",
        lambda: _investigations_total.add(
            1,
            {
                "confidence_label": confidence_label,
                "approval_required": str(approval_required).lower(),
                "outcome": outcome,
            },
        ),
    )


def record_review_completed(*, confidence_label: str, outcome: str) -> None:
    """

    Record that a support review has reached a terminal state.
    """
    if _reviews_total is None:
        return
    _safe_record(
        "reviews_total",
        lambda: _reviews_total.add(
            1,
            {"confidence_label": confidence_label, "outcome": outcome},
        ),
    )


def record_pipeline_node_duration(
    *,
    pipeline: str,
    node: str,
    duration_seconds: float,
    status: str,
) -> None:
    """

    Record the wall-clock duration of a pipeline node execution.
    """
    if _pipeline_node_duration is None:
        return
    _safe_record(
        "pipeline_node_duration",
        lambda: _pipeline_node_duration.record(
            duration_seconds,
            {"pipeline": pipeline, "node": node, "status": status},
        ),
    )


def record_confidence_score(*, pipeline: str, score: float) -> None:
    """

    Record the confidence score produced by a DetermineConfidence node.
    """
    if _confidence_score is None:
        return
    _safe_record(
        "confidence_score",
        lambda: _confidence_score.record(score, {"pipeline": pipeline}),
    )


def record_approval_decision(*, decision: str, pipeline: str) -> None:
    """

    Record an approval decision (approve / reject / auto_approve).
    """
    if _approval_decisions_total is None:
        return
    _safe_record(
        "approval_decisions_total",
        lambda: _approval_decisions_total.add(
            1,
            {"decision": decision, "pipeline": pipeline},
        ),
    )


def record_llm_call(
    *,
    agent: str,
    model: str,
    duration_seconds: float,
    status: str,
) -> None:
    """

    Record a single LLM call dispatched via the LiteLLM gateway.
    """
    if _llm_calls_total is None or _llm_call_duration is None:
        return
    _safe_record(
        "llm_calls_total",
        lambda: _llm_calls_total.add(
            1,
            {"agent": agent, "model": model, "status": status},
        ),
    )
    _safe_record(
        "llm_call_duration",
        lambda: _llm_call_duration.record(
            duration_seconds,
            {"agent": agent, "model": model},
        ),
    )


def record_job_processed(
    *,
    job_type: str,
    outcome: str,
    duration_seconds: float,
) -> None:
    """

    Record a worker job that has reached a terminal state.
    """
    if _jobs_processed_total is None or _job_duration is None:
        return
    _safe_record(
        "jobs_processed_total",
        lambda: _jobs_processed_total.add(
            1,
            {"job_type": job_type, "outcome": outcome},
        ),
    )
    _safe_record(
        "job_duration",
        lambda: _job_duration.record(
            duration_seconds,
            {"job_type": job_type},
        ),
    )


def set_job_queue_depth(*, job_type: str, status: str, depth: int) -> None:
    """

    Set the current job queue depth gauge for a job type / status combination.
    """
    if _job_queue_depth is None:
        return
    _safe_record(
        "job_queue_depth",
        lambda: _job_queue_depth.set(
            depth,
            {"job_type": job_type, "status": status},
        ),
    )
