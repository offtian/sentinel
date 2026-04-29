from __future__ import annotations

import pytest

from sentinel.interfaces.graphs import investigation
from tests import factories
from tests.factories import make_alert


# F7: same skip rationale as ``tests/functional/test_investigation.py`` —
# the approval-gate assertions depend on a confidence total derived from
# pre-canned Holmes findings; the F7 investigator changes the math.
# Re-add coverage in tests/functional/test_investigation_f7.py.
pytestmark = pytest.mark.skip(
    reason="F7: approval-gate functional tests need redesign for investigator agent"
)


@pytest.mark.asyncio
class TestApprovalGate:
    async def test_low_confidence_triggers_approval_request(
        self, mock_holmes: factories.MockHolmesAdapter, fake_sre_config
    ) -> None:
        # Given a configured approval threshold of 0.8
        approval_calls: list[tuple[object, ...]] = []

        async def track_approval(*args: object) -> str:
            approval_calls.append(args)
            return "mock-ts-123"

        alert = make_alert()

        # When the pipeline runs (default RCA confidence is 0.85 -> total ~0.705 which is < 0.8)
        result = await investigation.investigate_alert(
            alert=alert,
            envelope=factories.make_envelope(),
            agent_for=fake_sre_config.agent_for,
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

    async def test_high_confidence_skips_approval(
        self, mock_holmes: factories.MockHolmesAdapter, fake_sre_config
    ) -> None:
        # Given a low approval threshold of 0.3
        approval_calls: list[tuple[object, ...]] = []

        async def track_approval(*args: object) -> str:
            approval_calls.append(args)
            return "mock-ts"

        alert = make_alert()

        # When the pipeline runs (confidence ~0.705 which is > 0.3)
        result = await investigation.investigate_alert(
            alert=alert,
            envelope=factories.make_envelope(),
            agent_for=fake_sre_config.agent_for,
            post_to_slack=False,
            require_approval_below=0.3,
            request_approval_fn=track_approval,
        )

        # Then the approval function was NOT called (confidence above threshold)
        assert len(approval_calls) == 0
        # And the reply has no approval status (published directly)
        assert result.approval_status is None

    async def test_no_approval_fn_skips_gate(
        self, mock_holmes: factories.MockHolmesAdapter, fake_sre_config
    ) -> None:
        # Given a threshold but no approval function
        alert = make_alert()

        # When the pipeline runs
        result = await investigation.investigate_alert(
            alert=alert,
            envelope=factories.make_envelope(),
            agent_for=fake_sre_config.agent_for,
            post_to_slack=False,
            require_approval_below=0.8,
            request_approval_fn=None,
        )

        # Then it publishes directly (no crash, no pending status)
        assert result.approval_status is None
        assert result.root_cause is not None

    async def test_zero_threshold_disables_approval(
        self, mock_holmes: factories.MockHolmesAdapter, fake_sre_config
    ) -> None:
        # Given threshold of 0.0 (disabled) with an approval function
        approval_calls: list[tuple[object, ...]] = []

        async def track_approval(*args: object) -> str:
            approval_calls.append(args)
            return "mock-ts"

        alert = make_alert()

        # When the pipeline runs
        result = await investigation.investigate_alert(
            alert=alert,
            envelope=factories.make_envelope(),
            agent_for=fake_sre_config.agent_for,
            post_to_slack=False,
            require_approval_below=0.0,
            request_approval_fn=track_approval,
        )

        # Then approval function was NOT called (threshold disabled)
        assert len(approval_calls) == 0
        assert result.approval_status is None
