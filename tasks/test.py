from __future__ import annotations

import os

import invoke


def _ci_args() -> str:
    is_ci = os.environ.get("CIRCLECI") is not None
    return "--junitxml=test-results/junit.xml" if is_ci else ""


@invoke.task(
    help={"workers": "Number of parallel workers (default: 4). Pass 0 to disable parallelism."}
)
def run(ctx: invoke.Context, workers: int = 4) -> None:
    """
    Run the full test suite (unit + integration + functional).
    """
    unit(ctx, workers)
    integration(ctx, workers)
    functional(ctx, workers)


@invoke.task(
    help={"workers": "Number of parallel workers (default: 4). Pass 0 to disable parallelism."}
)
def unit(ctx: invoke.Context, workers: int = 4) -> None:
    """
    Run unit tests in parallel with pytest-xdist.
    """
    n_flag = f"-n {workers}"
    ctx.run(f"uv run pytest tests/unit/ {n_flag} {_ci_args()}", in_stream=False)


@invoke.task(
    help={"workers": "Number of parallel workers (default: 4). Pass 0 to disable parallelism."}
)
def integration(ctx: invoke.Context, workers: int = 4) -> None:
    """
    Run integration tests in parallel (requires a running database).
    """
    n_flag = f"-n {workers}"
    ctx.run(f"uv run pytest tests/integration/ {n_flag} {_ci_args()}", in_stream=False)


@invoke.task(
    help={"workers": "Number of parallel workers (default: 0). Pass 0 to disable parallelism."}
)
def functional(ctx: invoke.Context, workers: int = 0) -> None:
    """
    Run functional / evaluation tests sequentially (single process).
    """
    n_flag = f"-n {workers}"
    ctx.run(f"uv run pytest tests/functional/ {n_flag} {_ci_args()}", in_stream=False)
