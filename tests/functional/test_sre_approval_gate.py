from __future__ import annotations

import pytest

from sentinel.interfaces.graphs import sre_investigation
from tests import factories
from tests.factories import make_alert


@pytest.mark.usefixtures("patch_alert_classifier", "patch_root_cause_analyser")
class TestApprovalGate:
    @pytest.mark.asyncio
    async def test_low_confidence_triggers_approval_request(
        self, mock_holmes: factories.MockHolmesAdapter
    ) -> None:
        # Given a configured approval threshold of 0.8
        approval_calls: list[tuple[object, ...]] = []

        async def track_approval(*args: object) -> str:
            approval_calls.append(args)
            return "mock-ts-123"

        alert = make_alert()

        # When the pipeline runs (default RCA confidence is 0.85 -> total ~0.705 which is < 0.8)
        result = await sre_investigation.investigate_alert(
            alert=alert,
            holmes=mock_holmes,
            classifier_model="test",
            analyser_model="test",
            post_to_slack=False,
            require_approval_below=0.8,
            request_approval_fn=track_approval,
        )

        # Then the approval function was called
        assert len(approval_calls) == 1
        # And the reply indicates pending approval
        assert result.approval_status == "pending"
        # And investigation data is still populated
        assert result.root_cause is not None
        assert result.confidence is not None

    @pytest.mark.asyncio
    async def test_high_confidence_skips_approval(
        self, mock_holmes: factories.MockHolmesAdapter
    ) -> None:
        # Given a low approval threshold of 0.3
        approval_calls: list[tuple[object, ...]] = []

        async def track_approval(*args: object) -> str:
            approval_calls.append(args)
            return "mock-ts"

        alert = make_alert()

        # When the pipeline runs (confidence ~0.705 which is > 0.3)
        result = await sre_investigation.investigate_alert(
            alert=alert,
            holmes=mock_holmes,
            classifier_model="test",
            analyser_model="test",
            post_to_slack=False,
            require_approval_below=0.3,
            request_approval_fn=track_approval,
        )

        # Then the approval function was NOT called (confidence above threshold)
        assert len(approval_calls) == 0
        # And the reply has no approval status (published directly)
        assert result.approval_status is None

    @pytest.mark.asyncio
    async def test_no_approval_fn_skips_gate(
        self, mock_holmes: factories.MockHolmesAdapter
    ) -> None:
        # Given a threshold but no approval function
        alert = make_alert()

        # When the pipeline runs
        result = await sre_investigation.investigate_alert(
            alert=alert,
            holmes=mock_holmes,
            classifier_model="test",
            analyser_model="test",
            post_to_slack=False,
            require_approval_below=0.8,
            request_approval_fn=None,
        )

        # Then it publishes directly (no crash, no pending status)
        assert result.approval_status is None
        assert result.root_cause is not None

    @pytest.mark.asyncio
    async def test_zero_threshold_disables_approval(
        self, mock_holmes: factories.MockHolmesAdapter
    ) -> None:
        # Given threshold of 0.0 (disabled) with an approval function
        approval_calls: list[tuple[object, ...]] = []

        async def track_approval(*args: object) -> str:
            approval_calls.append(args)
            return "mock-ts"

        alert = make_alert()

        # When the pipeline runs
        result = await sre_investigation.investigate_alert(
            alert=alert,
            holmes=mock_holmes,
            classifier_model="test",
            analyser_model="test",
            post_to_slack=False,
            require_approval_below=0.0,
            request_approval_fn=track_approval,
        )

        # Then approval function was NOT called (threshold disabled)
        assert len(approval_calls) == 0
        assert result.approval_status is None
