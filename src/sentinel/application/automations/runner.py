"""
Execute registered scheduled automations by name.

Each automation is a simple async function that accepts keyword params
and returns a dict result. New automations are registered in the
``_REGISTRY`` mapping.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sentinel.utils import logs


# Type alias for automation functions.
AutomationFn = Callable[..., Awaitable[dict[str, Any]]]


async def _repo_health_check(*, params: dict[str, Any]) -> dict[str, Any]:
    """
    Placeholder repository health check automation.

    In production this would call the GitHub API to inspect repos,
    check stale PRs, and post a summary to Slack.
    """
    logs.log_event(
        "automation.repo_health_check.started",
        params=params,
    )

    # Placeholder — replace with real GitHub API calls in production
    summary = {
        "automation": "repo_health_check",
        "status": "completed",
        "message": "Repository health check completed successfully.",
        "details": {
            "repos_checked": params.get("repos", []),
            "stale_prs": [],
            "issues": [],
        },
    }

    logs.log_event(
        "automation.repo_health_check.completed",
        params={"repos_checked": len(params.get("repos", []))},
    )

    return summary


# Registry of available automations.
_REGISTRY: dict[str, AutomationFn] = {
    "repo_health_check": _repo_health_check,
}


class UnknownAutomationError(Exception):
    """Raised when an automation name is not found in the registry."""


async def run_automation(
    *,
    automation_name: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """
    Look up and execute a registered automation.

    :param automation_name: key in the automation registry
    :param params: keyword arguments forwarded to the automation function
    :raises UnknownAutomationError: if the automation name is not registered
    """
    handler = _REGISTRY.get(automation_name)
    if handler is None:
        raise UnknownAutomationError(
            f"Unknown automation: {automation_name!r}. Available: {', '.join(sorted(_REGISTRY))}"
        )

    logs.log_event(
        "automation.dispatched",
        params={"automation_name": automation_name},
    )

    return await handler(params=params)


def list_automations() -> list[str]:
    """Return the names of all registered automations."""
    return sorted(_REGISTRY)
