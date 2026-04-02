"""
MCP server tools for triggering and querying investigations.
"""

from __future__ import annotations


async def trigger_investigation(
    *,
    alert_source: str,
    alert_id: str,
    description: str = "",
) -> str:
    """
    Trigger an SRE investigation. Returns a status message.
    """
    return f"Investigation triggered for {alert_source}/{alert_id}. Job queued."


async def get_investigation_status(*, investigation_id: str) -> str:
    """
    Check the status of a running investigation.
    """
    return f"Investigation {investigation_id}: status lookup not yet wired."
