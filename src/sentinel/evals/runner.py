"""
Evaluation runner -- main entry point for the eval framework.

Orchestrate loading cases, running evaluators via ``pydantic_evals``,
and rendering the results.
"""

from __future__ import annotations

from typing import Any

from sentinel.evals import metrics
from sentinel.utils import logs

from . import cases, rendering, reporting, types


_KNOWN_AGENTS = (
    "alert_classifier",
    "root_cause_analyser",
    "response_drafter",
    "chart_generator",
    "intent_router",
    "ticket_reviewer",
    "k8s_investigator",
)


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
) -> None:
    """
    Load the dataset for the requested agent(s), attach evaluators, run, and render report.

    :param agent_name: Run evals for a specific agent, or ``None`` to run all agents.
    :raises ValueError: if agent_name is not recognized.
    """
    agents = _resolve_agents(agent_name=agent_name)

    for agent in agents:
        logs.log_event("eval.run_started", params={"agent": agent})

        dataset: Any = cases.load_cases(agent_name=agent)
        if len(dataset.cases) == 0:
            logs.log_event("eval.no_cases", params={"agent": agent})
            continue

        evaluation_report = await dataset.evaluate(
            task=_noop_task,
            name=f"sentinel_eval_{agent}",
        )

        # Compute composite score if a metric spec exists for this agent.
        composite_score: float | None = None
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

    Aggregate by taking the average pass rate per assertion key.
    """
    key_values: dict[str, list[bool]] = {}
    for case in report.cases:
        for key, result in case.assertions.items():
            key_values.setdefault(key, []).append(bool(result.value))

    aggregated: dict[str, bool | float] = {}
    for key, values in key_values.items():
        aggregated[key] = all(values) if values else False

    return aggregated
