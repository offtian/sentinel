"""
GitOps committer for generated Helm charts.

Write chart files to the gitops directory, create a feature branch,
commit, push, and open a pull request.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from sentinel.domain.charts import entities
from sentinel.settings import PROJECT_ROOT
from sentinel.utils import logs


logger = logs.get_logger()

_DEFAULT_GITOPS_ROOT = PROJECT_ROOT / "gitops" / "charts"


async def _run_command(
    *args: str,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """
    Run a shell command and return (returncode, stdout, stderr).
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode().strip(), stderr.decode().strip()


def write_chart_files(
    *,
    chart: entities.ChartOutput,
    gitops_root: Path = _DEFAULT_GITOPS_ROOT,
) -> Path:
    """
    Write generated chart files to the gitops directory.

    :param chart: The chart output with generated files.
    :param gitops_root: Root directory for gitops charts.
    :returns: The chart output directory path.
    """
    output_dir = gitops_root / chart.service_name
    output_dir.mkdir(parents=True, exist_ok=True)

    for gf in chart.files:
        file_path = output_dir / gf.path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(gf.content)

    logger.info(
        "chart_files_written",
        service_name=chart.service_name,
        output_dir=str(output_dir),
        file_count=len(chart.files),
    )

    return output_dir


class GitOpsCommitError(Exception):
    """
    Raise when any step of the GitOps commit workflow fails.
    """


async def commit_to_gitops(
    *,
    chart: entities.ChartOutput,
    gitops_root: Path = _DEFAULT_GITOPS_ROOT,
    branch_prefix: str = "chart",
) -> str:
    """
    Write chart files, create a branch, commit, push, and open a PR.

    :param chart: The chart output with generated files.
    :param gitops_root: Root directory for gitops charts.
    :param branch_prefix: Prefix for the branch name.
    :returns: The pull request URL.
    :raises GitOpsCommitError: if any git or gh command fails.
    """
    output_dir = write_chart_files(chart=chart, gitops_root=gitops_root)

    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    branch_name = f"{branch_prefix}/{chart.service_name}-{timestamp}"

    rc, out, err = await _run_command("git", "checkout", "-b", branch_name, cwd=PROJECT_ROOT)
    if rc != 0:
        logger.warning("git_checkout_failed", branch=branch_name, stderr=err)
        raise GitOpsCommitError(f"Branch creation failed: {err}")

    rc, out, err = await _run_command("git", "add", str(output_dir), cwd=PROJECT_ROOT)
    if rc != 0:
        logger.warning("git_add_failed", stderr=err)
        raise GitOpsCommitError(f"Git add failed: {err}")

    commit_msg = f"feat: generate Helm chart for {chart.service_name}"
    rc, out, err = await _run_command("git", "commit", "-m", commit_msg, cwd=PROJECT_ROOT)
    if rc != 0:
        logger.warning("git_commit_failed", stderr=err)
        raise GitOpsCommitError(f"Git commit failed: {err}")

    rc, out, err = await _run_command("git", "push", "-u", "origin", branch_name, cwd=PROJECT_ROOT)
    if rc != 0:
        logger.warning("git_push_failed", stderr=err)
        raise GitOpsCommitError(f"Git push failed: {err}")

    pr_title = f"feat: deploy {chart.service_name} Helm chart"
    pr_body = (
        f"## Generated Helm Chart\n\n"
        f"Service: `{chart.service_name}`\n"
        f"Files: {len(chart.files)}\n"
        f"Generation attempts: {chart.generation_attempts}\n"
        f"Confidence: {chart.confidence_score or 'N/A'}\n"
    )
    rc, out, err = await _run_command(
        "gh",
        "pr",
        "create",
        "--title",
        pr_title,
        "--body",
        pr_body,
        cwd=PROJECT_ROOT,
    )
    if rc != 0:
        logger.warning("gh_pr_create_failed", stderr=err)
        raise GitOpsCommitError(f"PR creation failed: {err}")

    logger.info("chart_pr_created", service_name=chart.service_name, pr_url=out)
    return out
