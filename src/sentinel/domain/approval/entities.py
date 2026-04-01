from __future__ import annotations

import enum
from datetime import datetime

import attrs


class ApprovalDecision(enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto_approved"


@attrs.frozen
class ApprovalRequest:
    """
    Record a human-approval-required finding before it reaches external systems.

    Immutable -- approval/rejection returns a new instance.
    Used for regulatory compliance: every automated output that reaches
    Slack/PagerDuty must have an approval record.
    """

    investigation_id: str
    alert_id: str
    alert_title: str
    confidence_label: str
    confidence_total: float
    root_cause: str
    remediation: str
    requested_at: datetime
    decision: ApprovalDecision = ApprovalDecision.PENDING
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    slack_message_ts: str | None = None

    def approve(self, *, reviewer: str, at: datetime) -> ApprovalRequest:
        """Return a new ApprovalRequest marked as approved."""
        return attrs.evolve(
            self,
            decision=ApprovalDecision.APPROVED,
            reviewed_by=reviewer,
            reviewed_at=at,
        )

    def reject(self, *, reviewer: str, at: datetime) -> ApprovalRequest:
        """Return a new ApprovalRequest marked as rejected."""
        return attrs.evolve(
            self,
            decision=ApprovalDecision.REJECTED,
            reviewed_by=reviewer,
            reviewed_at=at,
        )

    def auto_approve(self, *, at: datetime) -> ApprovalRequest:
        """Return a new ApprovalRequest marked as auto-approved (timeout elapsed)."""
        return attrs.evolve(
            self,
            decision=ApprovalDecision.AUTO_APPROVED,
            reviewed_by="system:auto_approve",
            reviewed_at=at,
        )
