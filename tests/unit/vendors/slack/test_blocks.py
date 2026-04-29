from __future__ import annotations

from sentinel.vendors.slack import _blocks as slack_blocks


class TestInvestigationSummaryBlocks:
    def test_includes_alert_id_and_confidence(self) -> None:
        # Given an investigation result with high confidence
        blocks = slack_blocks.investigation_summary_blocks(
            alert_id="P-001",
            alert_title="CPU spike",
            root_cause="OOM killer",
            remediation="Increase limit",
            confidence_label="High",
            findings_summary="Pod OOMKilled 3 times",
        )

        # When we collect all text strings from the blocks
        texts = _all_texts(blocks)

        # Then alert ID, green confidence emoji, and root cause are present
        assert any("P-001" in t for t in texts)
        assert any(":large_green_circle:" in t for t in texts)
        assert any("OOM killer" in t for t in texts)

    def test_omits_remediation_block_when_none(self) -> None:
        # Given no remediation supplied
        blocks = slack_blocks.investigation_summary_blocks(
            alert_id="P-002",
            alert_title="Disk full",
            root_cause="Log rotation disabled",
            remediation=None,
            confidence_label="Low",
            findings_summary="",
        )

        # Then no remediation section appears
        texts = _all_texts(blocks)
        assert not any("Remediation" in t for t in texts)

    def test_unknown_confidence_falls_back_to_red_circle(self) -> None:
        # Given an unrecognised confidence label
        blocks = slack_blocks.investigation_summary_blocks(
            alert_id="X",
            alert_title="X",
            root_cause=None,
            remediation=None,
            confidence_label="Unknown",
            findings_summary="",
        )

        texts = _all_texts(blocks)
        assert any(":red_circle:" in t for t in texts)


class TestApprovalRequestBlocks:
    def test_includes_approve_and_reject_buttons(self) -> None:
        # Given an investigation pending approval
        blocks = slack_blocks.approval_request_blocks(
            investigation_id="inv-123",
            alert_id="P-001",
            alert_title="CPU spike",
            root_cause="OOM",
            remediation="Scale up",
            confidence_label="Medium",
            findings_summary="",
        )

        # When we look for the actions block
        actions = [b for b in blocks if b["type"] == "actions"]

        # Then both buttons are present
        assert len(actions) == 1
        elements = actions[0]["elements"]
        action_ids = [e["action_id"] for e in elements]
        assert "approve_investigation" in action_ids
        assert "reject_investigation" in action_ids

    def test_block_id_encodes_investigation_id(self) -> None:
        # Given investigation_id "inv-456"
        blocks = slack_blocks.approval_request_blocks(
            investigation_id="inv-456",
            alert_id="P-002",
            alert_title="Disk full",
            root_cause=None,
            remediation=None,
            confidence_label=None,
            findings_summary="",
        )

        # Then the actions block_id contains the investigation_id
        actions = [b for b in blocks if b["type"] == "actions"]
        assert actions[0]["block_id"] == "approval_inv-456"


class TestSupportSummaryBlocks:
    def test_includes_ticket_key_and_response(self) -> None:
        # Given a support reply
        blocks = slack_blocks.support_summary_blocks(
            ticket_key="SUPP-99",
            ticket_summary="Cannot login",
            suggested_response="Reset password via /account",
            confidence_label="High",
            category="account",
        )

        texts = _all_texts(blocks)
        assert any("SUPP-99" in t for t in texts)
        assert any("Reset password" in t for t in texts)


class TestDriftAlertBlocks:
    def test_includes_runbook_and_severity(self) -> None:
        # Given a drift event with high severity
        blocks = slack_blocks.drift_alert_blocks(
            runbook_id="runbook/deploy-rollback",
            content_sha="abc123",
            drift_type="content_changed",
            drift_severity="high",
            suggested_fix="Re-sync from source",
            resolution_pr_template_url="https://github.com/org/repo/compare",
        )

        texts = _all_texts(blocks)
        assert any("runbook/deploy-rollback" in t for t in texts)
        assert any(":red_circle:" in t for t in texts)


def _all_texts(blocks: list[dict]) -> list[str]:
    """Recursively collect all text strings from a Block Kit payload."""
    texts: list[str] = []
    for block in blocks:
        _collect_texts(block, texts)
    return texts


def _collect_texts(obj: object, acc: list[str]) -> None:
    if isinstance(obj, str):
        acc.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_texts(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            _collect_texts(item, acc)
