"""Frozen policy primitives carried by ``BaseConfiguration``."""

from __future__ import annotations

from typing import Literal

import attrs


ConfidenceLabel = Literal["LOW", "MEDIUM", "HIGH"]
ApproverRole = Literal["oncall", "team_lead", "compliance", "ace_engineer"]
OutputKind = Literal["slack_channel", "slack_dm", "pagerduty_note", "jira_comment"]


@attrs.frozen(kw_only=True, slots=True)
class ApprovalPolicy:
    require_human_below_label: ConfidenceLabel = "HIGH"
    approver_role: ApproverRole = "oncall"
    approval_timeout_seconds: int = 900
    auto_approve_after_n_clean_runs: int | None = None
    require_human_first_send_of_template: bool = False

    @classmethod
    def empty(cls) -> ApprovalPolicy:
        return cls(
            approver_role="compliance",
            approval_timeout_seconds=0,
            require_human_first_send_of_template=True,
        )


@attrs.frozen(kw_only=True, slots=True)
class OutputChannel:
    kind: OutputKind
    target: str
    min_confidence_label: ConfidenceLabel


@attrs.frozen(kw_only=True, slots=True)
class RedactionPolicy:
    deny_patterns: tuple[str, ...] = ()
    judge_score_min: float = 0.9

    @classmethod
    def default(cls) -> RedactionPolicy:
        # {tenant_id} is str.format()-substituted by the redactor at runtime.
        return cls(
            deny_patterns=(
                r"(?i)(api[_-]?key|secret|token|password|bearer)[\s:=]+[\w\-]+",
                r"(?i)pm-(?!{tenant_id})\w+",
            ),
            judge_score_min=0.9,
        )

    @classmethod
    def empty(cls) -> RedactionPolicy:
        # judge_score_min=1.0 means reject every output — placeholder fails closed.
        return cls(deny_patterns=(), judge_score_min=1.0)
