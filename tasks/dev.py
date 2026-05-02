from __future__ import annotations

import os

import invoke


@invoke.task
def format_code(ctx: invoke.Context) -> None:
    """
    Auto-format code to conform with conventions.
    """
    ctx.run("uv run ruff format src/ tests/")
    ctx.run("uv run ruff check --fix src/ tests/")


@invoke.task(name="fmt")
def fmt(ctx: invoke.Context) -> None:
    """
    Run all static analysis tools and fix what can be fixed.
    """
    ctx.run("uv run ruff check --fix src/ tests/")
    ctx.run("uv run ruff format src/ tests/")
    mypy(ctx)
    import_linter(ctx)


@invoke.task
def lint(ctx: invoke.Context) -> None:
    """
    Run all static analysis tools without auto-fixing.
    """
    ruff_format_check(ctx)
    ruff_lint(ctx)
    mypy(ctx)
    import_linter(ctx)


@invoke.task
def ruff_format_check(ctx: invoke.Context) -> None:
    """
    Check code formatting with ruff (no changes applied).
    """
    ctx.run("uv run ruff format --check src/ tests/")


@invoke.task
def ruff_lint(ctx: invoke.Context) -> None:
    """
    Run ruff linting without auto-fix.
    """
    ctx.run("uv run ruff check src/ tests/")


@invoke.task
def mypy(ctx: invoke.Context) -> None:
    """
    Run mypy type checking.
    """
    is_ci = os.environ.get("CIRCLECI") is not None
    mypy_args = "--junit-xml=test-results/mypy.xml" if is_ci else ""

    if mypy_args:
        ctx.run(f"uv run mypy {mypy_args} src/")
    else:
        ctx.run("uv run mypy src/")


@invoke.task
def import_linter(ctx: invoke.Context) -> None:
    """
    Run import linter to enforce architectural boundaries.
    """
    ctx.run("uv run lint-imports")


@invoke.task
def run_all_checks(ctx: invoke.Context) -> None:
    """
    Run all checks and tests — mirrors the CI pipeline.
    """
    ctx.run("inv dev.ruff-format-check")
    ctx.run("inv dev.ruff-lint")
    ctx.run("inv dev.mypy")
    ctx.run("inv dev.import-linter")
    ctx.run("inv test.unit")
    ctx.run("inv database.check-missing-migrations")
