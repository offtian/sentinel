from __future__ import annotations

import warnings
from typing import Any


with warnings.catch_warnings():
    warnings.simplefilter("ignore", UserWarning)
    from pdpyras import APISession

from sentinel.settings import get_settings
from sentinel.utils import logs


class PagerDutyClient:
    """
    Wraps the pdpyras SDK for interacting with the PagerDuty REST API.

    Used to write investigation notes back to incidents and manage incident state.
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = api_key if api_key is not None else get_settings().pagerduty_api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _get_session(self) -> APISession:
        return APISession(self._api_key)

    async def add_incident_note(
        self,
        incident_id: str,
        content: str,
    ) -> dict[str, Any] | None:
        """
        Add an investigation note to a PagerDuty incident.

        Args:
            incident_id: The PagerDuty incident ID.
            content: The note content (markdown supported).

        Returns:
            The created note dict, or None on failure.
        """
        if not self.is_configured:
            logs.log_event(
                "pagerduty_note_skipped",
                params={"reason": "Not configured", "incident_id": incident_id},
            )
            return None

        try:
            session = self._get_session()
            response = session.post(
                f"/incidents/{incident_id}/notes",
                json={"note": {"content": content}},
            )
            response.raise_for_status()
            result: dict[str, Any] = response.json().get("note", {})

            logs.log_event(
                "pagerduty_note_added",
                params={"incident_id": incident_id, "note_id": result.get("id")},
            )
            return result

        except Exception as e:
            logs.log_exception(e, params={"incident_id": incident_id})
            return None

    async def get_incident(self, *, incident_id: str) -> dict[str, Any] | None:
        """Fetch details for a specific PagerDuty incident."""
        if not self.is_configured:
            return None

        try:
            session = self._get_session()
            response = session.get(f"/incidents/{incident_id}")
            response.raise_for_status()
            result: dict[str, Any] = response.json().get("incident", {})
            return result

        except Exception as e:
            logs.log_exception(e, params={"incident_id": incident_id})
            return None

    async def update_incident_status(
        self,
        *,
        incident_id: str,
        status: str,
        requester_email: str,
    ) -> bool:
        """
        Update the status of a PagerDuty incident.

        Args:
            incident_id: The PagerDuty incident ID.
            status: New status (triggered, acknowledged, resolved).
            requester_email: Email of the user making the change.

        Returns:
            True if successful, False otherwise.
        """
        if not self.is_configured:
            logs.log_event(
                "pagerduty_status_update_skipped",
                params={"reason": "Not configured", "incident_id": incident_id},
            )
            return False

        try:
            session = self._get_session()
            response = session.put(
                f"/incidents/{incident_id}",
                json={
                    "incident": {
                        "type": "incident_reference",
                        "status": status,
                    }
                },
                headers={"From": requester_email},
            )
            response.raise_for_status()

            logs.log_event(
                "pagerduty_status_updated",
                params={"incident_id": incident_id, "status": status},
            )
            return True

        except Exception as e:
            logs.log_exception(e, params={"incident_id": incident_id, "status": status})
            return False

    def format_investigation_note(
        self,
        *,
        root_cause: str | None,
        remediation: str | None,
        confidence_label: str | None,
        findings_summary: str,
    ) -> str:
        """Format an investigation result into a PagerDuty note."""
        parts = ["## Sentinel Investigation Results\n"]

        if confidence_label:
            parts.append(f"**Confidence:** {confidence_label}\n")

        if root_cause:
            parts.append(f"### Root Cause\n{root_cause}\n")

        if remediation:
            parts.append(f"### Remediation\n{remediation}\n")

        if findings_summary:
            parts.append(f"### Findings\n{findings_summary}\n")

        return "\n".join(parts)
