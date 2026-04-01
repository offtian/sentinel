"""Shared fixtures for evaluation tests — reuses functional test stubs."""

from tests.functional.conftest import (  # noqa: F401
    patch_alert_classifier,
    patch_response_drafter,
    patch_root_cause_analyser,
    patch_ticket_reviewer,
)
