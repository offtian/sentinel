from __future__ import annotations

from datetime import UTC, datetime

import attrs.exceptions
import pytest

from sentinel.domain.approval import entities


def _make_approval_request(
    **overrides: object,
) -> entities.ApprovalRequest:
    defaults = {
        "investigation_id": "inv-123",
        "alert_id": "P123ABC",
        "alert_title": "High CPU on web-01",
        "confidence_label": "Low",
        "confidence_total": 0.3,
        "root_cause": "Possible memory leak",
        "remediation": "Increase memory limit",
        "requested_at": datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
    }
    return entities.ApprovalRequest(**{**defaults, **overrides})


class TestApprovalDecision:
    def test_has_expected_values(self) -> None:
        # Given the ApprovalDecision enum

        # Then it has the expected members
        assert entities.ApprovalDecision.PENDING.value == "pending"
        assert entities.ApprovalDecision.APPROVED.value == "approved"
        assert entities.ApprovalDecision.REJECTED.value == "rejected"
        assert entities.ApprovalDecision.AUTO_APPROVED.value == "auto_approved"


class TestApprovalRequest:
    def test_creates_pending_request(self) -> None:
        # Given approval request parameters
        request = _make_approval_request()

        # Then it defaults to pending with no reviewer
        assert request.decision == entities.ApprovalDecision.PENDING
        assert request.reviewed_by is None
        assert request.reviewed_at is None
        assert request.investigation_id == "inv-123"

    def test_is_frozen(self) -> None:
        # Given an approval request
        request = _make_approval_request()

        # When we attempt to mutate

        # Then it raises FrozenInstanceError
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            request.decision = entities.ApprovalDecision.APPROVED  # type: ignore[misc]

    def test_approve_returns_new_instance(self) -> None:
        # Given a pending approval request
        request = _make_approval_request()

        # When we approve it
        review_time = datetime(2026, 4, 1, 12, 5, tzinfo=UTC)
        approved = request.approve(reviewer="jane@hedge.com", at=review_time)

        # Then a new instance is returned with approved state
        assert approved.decision == entities.ApprovalDecision.APPROVED
        assert approved.reviewed_by == "jane@hedge.com"
        assert approved.reviewed_at == review_time
        # And original is unchanged
        assert request.decision == entities.ApprovalDecision.PENDING

    def test_reject_returns_new_instance(self) -> None:
        # Given a pending approval request
        request = _make_approval_request()

        # When we reject it
        review_time = datetime(2026, 4, 1, 12, 5, tzinfo=UTC)
        rejected = request.reject(reviewer="john@hedge.com", at=review_time)

        # Then a new instance is returned with rejected state
        assert rejected.decision == entities.ApprovalDecision.REJECTED
        assert rejected.reviewed_by == "john@hedge.com"

    def test_auto_approve_returns_new_instance(self) -> None:
        # Given a pending approval request
        request = _make_approval_request()

        # When it auto-approves
        auto_time = datetime(2026, 4, 1, 12, 10, tzinfo=UTC)
        auto_approved = request.auto_approve(at=auto_time)

        # Then a new instance is returned with auto_approved state
        assert auto_approved.decision == entities.ApprovalDecision.AUTO_APPROVED
        assert auto_approved.reviewed_by == "system:auto_approve"
        assert auto_approved.reviewed_at == auto_time
