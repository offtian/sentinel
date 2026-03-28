"""
Streamlit-compatible status update client for live pipeline progress.

Implements the ``StatusUpdateClient`` interface so the same graph pipelines
used by the Slack bot can report progress into a Streamlit chat container.
"""

from __future__ import annotations

from collections.abc import Callable

from sentinel.interfaces.graphs.common import StatusUpdateClient


class StreamlitStatusUpdateClient(StatusUpdateClient):
    """
    Push pipeline status updates into a Streamlit chat via a callback.

    The callback receives a status string (e.g. "Classifying alert...")
    and should update the appropriate ``st.status`` or ``st.empty()``
    placeholder.  This keeps the graph pipelines free of any Streamlit
    import.
    """

    def __init__(self, *, on_status: Callable[[str], None]) -> None:
        self._on_status = on_status

    async def update_status(self, message: str) -> None:
        self._on_status(message)
