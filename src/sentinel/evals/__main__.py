"""
Run component evaluations.

Usage:
    uv run python -m sentinel.evals --all
    uv run python -m sentinel.evals --agent alert_classifier
    uv run python -m sentinel.evals --agent root_cause_analyser
    uv run python -m sentinel.evals --agent response_drafter
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from sentinel.evals import framework
from sentinel.evals.agents import alert_classifier, response_drafter, root_cause_analyser
from sentinel.utils import logs


_EVALUATORS: dict[str, type[framework.BaseEvaluator]] = {
    "alert_classifier": alert_classifier.AlertClassifierEvaluator,
    "root_cause_analyser": root_cause_analyser.RootCauseAnalyserEvaluator,
    "response_drafter": response_drafter.ResponseDrafterEvaluator,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Tier 2 component evaluations for Sentinel agents.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--all",
        action="store_true",
        help="Run all evaluators.",
    )
    group.add_argument(
        "--agent",
        choices=list(_EVALUATORS.keys()),
        help="Run a specific agent evaluator.",
    )
    return parser


def _print_report(*, report: framework.EvalReport) -> None:
    print(f"\n{'=' * 60}")
    print(f"Evaluator: {report.evaluator_name}")
    print(
        f"Pass rate: {report.pass_rate:.1%} ({_passed_count(report=report)}/{len(report.results)})"
    )
    print(f"{'=' * 60}")

    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        print(f"\n  [{status}] {result.case_id}")
        for metric in result.metrics:
            indicator = "+" if metric.passed else "-"
            print(
                f"    [{indicator}] {metric.name}: {metric.value:.2f} (threshold: {metric.threshold:.2f})"
            )


def _passed_count(*, report: framework.EvalReport) -> int:
    return sum(1 for r in report.results if r.passed)


async def _run_evaluator(*, evaluator_cls: type[framework.BaseEvaluator]) -> framework.EvalReport:
    evaluator = evaluator_cls()
    dataset: list[dict[str, Any]] = evaluator.load_dataset()  # type: ignore[attr-defined]
    return await evaluator.run(dataset=dataset)


async def _main(*, agents: list[str]) -> bool:
    all_passed = True
    for agent_name in agents:
        evaluator_cls = _EVALUATORS[agent_name]
        report = await _run_evaluator(evaluator_cls=evaluator_cls)
        _print_report(report=report)
        if report.pass_rate < 1.0:
            all_passed = False
    return all_passed


def main() -> None:
    """
    Parse arguments and run the requested evaluations.
    """
    parser = _build_parser()
    args = parser.parse_args()

    if args.all:
        agents = list(_EVALUATORS.keys())
    else:
        agents = [args.agent]

    logs.log_event("eval.cli_started", params={"agents": agents})
    all_passed = asyncio.run(_main(agents=agents))

    if not all_passed:
        logs.log_event("eval.cli_finished", params={"status": "some_failures"})
        sys.exit(1)
    else:
        logs.log_event("eval.cli_finished", params={"status": "all_passed"})


if __name__ == "__main__":
    main()
