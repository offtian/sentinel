"""
Evaluation runner -- main entry point for the eval framework.

Orchestrate loading cases, running evaluators via ``pydantic_evals``,
and rendering the results.
"""

from __future__ import annotations

import time
from typing import Any

from sentinel.data import db
from sentinel.domain.evaluation import operations
from sentinel.evals import metrics
from sentinel.utils import logs

from . import cases, rendering, reporting, types


_KNOWN_AGENTS = tuple(cases.AGENT_NAMES)


async def _noop_task(input_data: types.InputData) -> str:
    """
    No-op task that returns the case output as a string.

    The evaluators read directly from ``input_data.case_payload`` so the task
    output is unused for scoring. We return a stringified version for the
    pydantic_evals report rendering.
    """
    output = input_data.case_payload.get("output", {})
    return str(output)


async def run(
    *,
    agent_name: str | None = None,
    persist: bool = False,
) -> None:
    """
    Load the dataset for the requested agent(s), attach evaluators, run, and render report.

    :param agent_name: Run evals for a specific agent, or ``None`` to run all agents.
    :param persist: When ``True``, save results to the database after each agent evaluation.
    :raises ValueError: if agent_name is not recognized.
    """
    agents = _resolve_agents(agent_name=agent_name)

    for agent in agents:
        logs.log_event("eval.run_started", params={"agent": agent})

        dataset: Any = cases.load_cases(agent_name=agent)
        if len(dataset.cases) == 0:
            logs.log_event("eval.no_cases", params={"agent": agent})
            continue

        start_monotonic = time.monotonic()

        evaluation_report = await dataset.evaluate(
            task=_noop_task,
            name=f"sentinel_eval_{agent}",
        )

        elapsed_ms = int((time.monotonic() - start_monotonic) * 1000)

        # Compute composite score if a metric spec exists for this agent.
        composite_score: float | None = None
        assertion_results: dict[str, bool | float] = {}
        metric_spec = metrics.AGENT_METRIC_SPECS.get(agent)
        if metric_spec is not None:
            assertion_results = _collect_assertion_results(
                report=evaluation_report,
            )
            composite_score = metrics.compute_composite_score(
                spec=metric_spec,
                assertion_results=assertion_results,
            )

        report = reporting.EvaluationReport(
            evaluation_report,
            composite_score=composite_score,
        )

        rendering.render_report_to_console(report=report)

        logs.log_event(
            "eval.run_completed",
            params={
                "agent": agent,
                "average_assertion_success_rate": f"{report.average_assertion_success_rate:.2f}%",
                "composite_score": f"{composite_score:.4f}"
                if composite_score is not None
                else "N/A",
                "cases": len(report.cases),
                "failures": len(report.failures),
            },
        )

        if persist:
            await _persist_results(
                agent=agent,
                evaluation_report=evaluation_report,
                report=report,
                composite_score=composite_score,
                assertion_results=assertion_results,
                elapsed_ms=elapsed_ms,
            )


async def _persist_results(
    *,
    agent: str,
    evaluation_report: Any,
    report: reporting.EvaluationReport,
    composite_score: float | None,
    assertion_results: dict[str, bool | float],
    elapsed_ms: int,
) -> None:
    """
    Persist evaluation results to the database.

    Handle all errors gracefully -- the eval run must never fail because
    persistence failed.
    """
    try:
        database = db.get_db()
    except RuntimeError as exc:
        logs.log_event(
            "eval.persist_skipped",
            params={"agent": agent, "reason": str(exc)},
        )
        return

    total_cases = len(report.cases)
    failed_cases = len(report.failures)
    passed_cases = total_cases - failed_cases
    average_score = float(report.average_assertion_success_rate) / 100

    results_json: dict[str, Any] = {
        "cases": _build_results_json(report=evaluation_report),
    }

    assertion_details: dict[str, Any] = {
        k: bool(v) if isinstance(v, bool) else v for k, v in assertion_results.items()
    }

    try:
        await operations.persist_eval_run(
            db=database,
            dataset_name=f"sentinel_eval_{agent}",
            agent_name=agent,
            total_cases=total_cases,
            passed_cases=passed_cases,
            failed_cases=failed_cases,
            average_score=average_score,
            composite_score=composite_score,
            assertion_details_json=assertion_details if assertion_details else None,
            results_json=results_json,
            run_duration_ms=elapsed_ms,
        )
        logs.log_event(
            "eval.persisted",
            params={"agent": agent, "total_cases": total_cases},
        )
    except Exception as exc:
        logs.log_event(
            "eval.persist_skipped",
            params={"agent": agent, "reason": str(exc)},
        )


def _build_results_json(*, report: Any) -> list[dict[str, Any]]:
    """
    Extract per-case results from a pydantic_evals report.

    For each case, capture the case name and a dict of evaluator_key to bool
    indicating whether the assertion passed.
    """
    results: list[dict[str, Any]] = []
    for case in report.cases:
        assertions = {key: bool(result.value) for key, result in case.assertions.items()}
        results.append(
            {
                "case_name": case.name,
                "assertions": assertions,
            }
        )
    return results


def _resolve_agents(*, agent_name: str | None) -> tuple[str, ...]:
    """
    Return the tuple of agent names to evaluate.

    :raises ValueError: if a specific agent_name is not recognized.
    """
    if agent_name is None:
        return _KNOWN_AGENTS

    if agent_name not in _KNOWN_AGENTS:
        raise ValueError(
            f"Unknown agent name: {agent_name!r}. Expected one of: {sorted(_KNOWN_AGENTS)}"
        )
    return (agent_name,)


def _collect_assertion_results(
    *,
    report: Any,
) -> dict[str, bool | float]:
    """
    Collect assertion results across all cases in a report.

    Aggregate per key: True only if the assertion passed in every case.
    Uses a running boolean to short-circuit on first failure per key.
    """
    if not report.cases:
        return {}

    aggregated: dict[str, bool | float] = {}
    for case in report.cases:
        for key, result in case.assertions.items():
            if key not in aggregated or aggregated[key]:
                aggregated[key] = bool(result.value)

    return aggregated
