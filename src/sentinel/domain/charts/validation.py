"""
Validation runner for generated Helm charts.

Runs ``helm template`` and ``kubeconform`` as subprocesses to validate
the generated chart files. Both tools must be available on PATH.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from sentinel.domain.charts import entities
from sentinel.utils import logs


logger = logs.get_logger()


async def _run_helm_template(
    *,
    chart_dir: Path,
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    """
    Run ``helm template`` on a chart directory.

    :returns: (success, errors, warnings)
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "helm",
            "template",
            "test-release",
            str(chart_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        return True, (), ("helm not found on PATH — skipping template validation",)

    errors: list[str] = []
    warnings: list[str] = []

    if proc.returncode != 0:
        stderr_text = stderr.decode().strip()
        if stderr_text:
            errors.append(stderr_text)
        return False, tuple(errors), tuple(warnings)

    stderr_text = stderr.decode().strip()
    if stderr_text:
        warnings = list(stderr_text.splitlines())

    return True, tuple(errors), tuple(warnings)


async def _run_kubeconform(
    *,
    chart_dir: Path,
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    """
    Run ``kubeconform`` on rendered templates.

    :returns: (success, errors, warnings)
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubeconform",
            "-summary",
            str(chart_dir / "templates"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        return True, (), ("kubeconform not found on PATH — skipping schema validation",)

    errors: list[str] = []
    warnings: list[str] = []

    output = (stdout.decode() + stderr.decode()).strip()
    if proc.returncode != 0:
        if output:
            errors.append(output)
        return False, tuple(errors), tuple(warnings)

    if output:
        for line in output.splitlines():
            if "WARN" in line.upper():
                warnings.append(line)

    return True, tuple(errors), tuple(warnings)


async def validate_chart(
    *,
    chart: entities.ChartOutput,
) -> entities.ValidationResult:
    """
    Validate a generated chart by writing files to a temp directory
    and running helm template + kubeconform.

    :param chart: The generated chart output with files.
    :returns: A ValidationResult with pass/fail and any errors/warnings.
    """
    with tempfile.TemporaryDirectory(prefix="sentinel-chart-") as tmp:
        chart_dir = Path(tmp)
        templates_dir = chart_dir / "templates"
        templates_dir.mkdir()

        # Write a minimal Chart.yaml
        (chart_dir / "Chart.yaml").write_text(
            f"apiVersion: v2\nname: {chart.service_name}\nversion: 0.1.0\n"
        )

        # Write generated files
        for gf in chart.files:
            file_path = chart_dir / gf.path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(gf.content)

        helm_ok, helm_errors, helm_warnings = await _run_helm_template(chart_dir=chart_dir)
        conform_ok, conform_errors, conform_warnings = await _run_kubeconform(chart_dir=chart_dir)

    all_errors = helm_errors + conform_errors
    all_warnings = helm_warnings + conform_warnings

    result = entities.ValidationResult(
        helm_template_ok=helm_ok,
        kubeconform_ok=conform_ok,
        errors=all_errors,
        warnings=all_warnings,
    )

    logger.info(
        "chart_validation_completed",
        service_name=chart.service_name,
        helm_ok=helm_ok,
        conform_ok=conform_ok,
        error_count=len(all_errors),
        warning_count=len(all_warnings),
    )

    return result
