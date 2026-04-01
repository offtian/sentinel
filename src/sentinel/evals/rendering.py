"""
Rich console rendering for evaluation reports.

Render evaluation reports as formatted tables using the ``rich``
library for clear visual output in CI and local runs.
"""

from __future__ import annotations

from rich import console as rich_console
from rich import table as rich_table

from sentinel.evals import reporting


def _get_table_row(
    *,
    case: object,
    report_has_assertions: bool,
) -> list[str]:
    """
    Build a single table row from a report case.
    """
    # Import here to avoid circular imports at module level
    from pydantic_evals import reporting as pydantic_reporting

    report_case: pydantic_reporting.ReportCase = case  # type: ignore[assignment]
    row = [
        str(report_case.name),
        str(report_case.inputs),
    ]
    if report_has_assertions:
        row.append(reporting.get_assertions_as_str(report_case.assertions))
    row.append(reporting.get_task_duration_as_str(duration=report_case.task_duration))
    return row


def render_report_as_rich_table(*, report: reporting.EvaluationReport) -> rich_table.Table:
    """
    Render the evaluation report as a ``rich`` table.
    """
    headers_and_footers = [
        ("Case ID", "Averages"),
        ("Inputs", ""),
    ]
    if report.has_assertions:
        headers_and_footers.append(("Assertions", f"{report.average_assertion_success_rate:.2f}%"))
    headers_and_footers.append(
        ("Duration", f"{report.average_duration:.2f}s"),
    )

    rows = [
        _get_table_row(
            case=case,
            report_has_assertions=report.has_assertions,
        )
        for case in report.cases
    ]

    table = rich_table.Table(
        title=f"Evaluation summary: {report.name}",
        show_lines=True,
        footer_style="italic",
        show_footer=True,
    )

    for header, footer in headers_and_footers:
        table.add_column(header, footer)

    for row in rows:
        table.add_row(*row)

    return table


def render_report_to_console(*, report: reporting.EvaluationReport) -> None:
    """
    Print the evaluation report table to the console.
    """
    table = render_report_as_rich_table(report=report)
    console = rich_console.Console()
    console.print(table)
