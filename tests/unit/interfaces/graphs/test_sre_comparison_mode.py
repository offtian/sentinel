from __future__ import annotations

from unittest import mock

import pytest

from sentinel.interfaces.graphs import sre_investigation
from tests import factories
from tests.functional.conftest import _build_fake_config


class TestComparisonModeInPipeline:
    @pytest.mark.asyncio
    async def test_comparison_result_stored_on_state_when_challenger_provided(self) -> None:
        # Given an investigation state with an alert and a challenger adapter
        alert = factories.make_alert()
        challenger = factories.MockKagentAdapter()

        state = sre_investigation.State(alert=alert)
        state.comparison_result = None
        deps = sre_investigation.Dependencies(
            status_update_client=mock.AsyncMock(),
            agent_for=_build_fake_config({}).agent_for,
            holmes=factories.MockHolmesAdapter(),
            post_to_slack=False,
            challenger_adapter=challenger,
        )
        ctx = mock.MagicMock()
        ctx.state = state
        ctx.deps = deps

        node = sre_investigation.InvestigateWithHolmes()

        # When the node runs
        await node.run(ctx)

        # Then a comparison result is stored on state
        assert state.comparison_result is not None
        assert state.comparison_result.case_id == alert.id

    @pytest.mark.asyncio
    async def test_no_comparison_when_challenger_is_none(self) -> None:
        # Given no challenger adapter
        alert = factories.make_alert()
        state = sre_investigation.State(alert=alert)
        state.comparison_result = None
        deps = sre_investigation.Dependencies(
            status_update_client=mock.AsyncMock(),
            agent_for=_build_fake_config({}).agent_for,
            holmes=factories.MockHolmesAdapter(),
            post_to_slack=False,
        )
        ctx = mock.MagicMock()
        ctx.state = state
        ctx.deps = deps

        node = sre_investigation.InvestigateWithHolmes()

        # When the node runs
        await node.run(ctx)

        # Then no comparison result is stored
        assert state.comparison_result is None

    @pytest.mark.asyncio
    async def test_challenger_failure_does_not_break_pipeline(self) -> None:
        # Given a challenger adapter that raises an exception
        alert = factories.make_alert()
        failing_challenger = mock.AsyncMock()
        failing_challenger.investigate.side_effect = RuntimeError("kagent timeout")

        state = sre_investigation.State(alert=alert)
        state.comparison_result = None
        deps = sre_investigation.Dependencies(
            status_update_client=mock.AsyncMock(),
            agent_for=_build_fake_config({}).agent_for,
            holmes=factories.MockHolmesAdapter(),
            post_to_slack=False,
            challenger_adapter=failing_challenger,
        )
        ctx = mock.MagicMock()
        ctx.state = state
        ctx.deps = deps

        node = sre_investigation.InvestigateWithHolmes()

        # When the node runs
        next_node = await node.run(ctx)

        # Then the pipeline continues without a comparison result
        assert state.comparison_result is None
        assert isinstance(next_node, sre_investigation.AnalyseRootCause)
