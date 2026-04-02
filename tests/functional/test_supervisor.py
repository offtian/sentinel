from __future__ import annotations

from functools import partial

import pytest

from sentinel.application.supervisor import orchestrator
from sentinel.domain.supervisor import entities as supervisor_entities
from sentinel.interfaces.graphs import sre_investigation, support_review
from tests import factories


@pytest.mark.asyncio
class TestSuperviseSreInvestigation:
    async def test_publishes_when_quality_passes(
        self,
        mock_holmes,
        patch_alert_classifier,
        patch_root_cause_analyser,
    ) -> None:
        # Given a standard alert with mocked agents that produce good output
        alert = factories.make_alert()
        investigate = partial(
            sre_investigation.investigate_alert,
            alert,
            holmes=mock_holmes,
            post_to_slack=False,
        )

        # When the supervised investigation runs
        result = await orchestrator.supervise_sre_investigation(
            investigate_fn=investigate,
            alert_id=alert.id,
            max_retries=1,
        )

        # Then the decision is PUBLISH with no retries
        assert result.decision == supervisor_entities.SupervisorDecision.PUBLISH
        assert result.retry_count == 0
        assert result.verdict.passed is True
        assert result.reply.alert_id == alert.id
        assert result.reply.root_cause is not None

    async def test_retries_and_escalates_on_persistent_low_quality(
        self,
        mock_holmes,
        patch_alert_classifier,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given agents that produce a degraded reply (no root cause analysis)
        # We patch root_cause_analyser to raise an exception, which causes the
        # pipeline to produce a fallback reply with generic text.
        from sentinel.interfaces.graphs.agents import root_cause_analyser

        async def failing_run(*, user_prompt, model, deps, **kwargs):
            raise RuntimeError("LLM unavailable")

        monkeypatch.setattr(root_cause_analyser.agent, "run", failing_run)

        alert = factories.make_alert()
        investigate = partial(
            sre_investigation.investigate_alert,
            alert,
            holmes=mock_holmes,
            post_to_slack=False,
        )

        # When the supervised investigation runs with 1 retry
        result = await orchestrator.supervise_sre_investigation(
            investigate_fn=investigate,
            alert_id=alert.id,
            max_retries=1,
        )

        # Then the decision is ESCALATE or REJECT (not PUBLISH)
        assert result.decision in (
            supervisor_entities.SupervisorDecision.ESCALATE,
            supervisor_entities.SupervisorDecision.REJECT,
        )
        assert result.verdict.passed is False
        assert len(result.verdict.issues) > 0

    async def test_zero_retries_decides_immediately(
        self,
        mock_holmes,
        patch_alert_classifier,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given agents that produce degraded output
        from sentinel.interfaces.graphs.agents import root_cause_analyser

        async def failing_run(*, user_prompt, model, deps, **kwargs):
            raise RuntimeError("LLM unavailable")

        monkeypatch.setattr(root_cause_analyser.agent, "run", failing_run)

        alert = factories.make_alert()
        investigate = partial(
            sre_investigation.investigate_alert,
            alert,
            holmes=mock_holmes,
            post_to_slack=False,
        )

        # When the supervised investigation runs with zero retries
        result = await orchestrator.supervise_sre_investigation(
            investigate_fn=investigate,
            alert_id=alert.id,
            max_retries=0,
        )

        # Then the retry_count is 0 and decision is not PUBLISH
        assert result.retry_count == 0
        assert result.decision != supervisor_entities.SupervisorDecision.PUBLISH


@pytest.mark.asyncio
class TestSuperviseSupportReview:
    async def test_publishes_when_quality_passes(
        self,
        patch_ticket_reviewer,
        patch_response_drafter,
    ) -> None:
        # Given a standard ticket with mocked agents that produce good output
        from tests.functional.conftest import StubDocumentSearcher, StubPastTicketSearcher

        ticket = factories.make_ticket()
        review = partial(
            support_review.review_ticket,
            ticket,
            document_searcher=StubDocumentSearcher(),
            ticket_searcher=StubPastTicketSearcher(),
        )

        # When the supervised review runs
        result = await orchestrator.supervise_support_review(
            review_fn=review,
            ticket_key=ticket.key,
            max_retries=1,
        )

        # Then the decision is PUBLISH
        assert result.decision == supervisor_entities.SupervisorDecision.PUBLISH
        assert result.retry_count == 0
        assert result.verdict.passed is True
        assert result.reply.ticket_key == ticket.key

    async def test_retries_and_escalates_on_persistent_low_quality(
        self,
        patch_ticket_reviewer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given agents that produce degraded output (drafter fails)
        from sentinel.interfaces.graphs.agents import response_drafter

        async def failing_run(*, user_prompt, model, deps, **kwargs):
            raise RuntimeError("LLM unavailable")

        monkeypatch.setattr(response_drafter.agent, "run", failing_run)

        from tests.functional.conftest import StubDocumentSearcher, StubPastTicketSearcher

        ticket = factories.make_ticket()
        review = partial(
            support_review.review_ticket,
            ticket,
            document_searcher=StubDocumentSearcher(),
            ticket_searcher=StubPastTicketSearcher(),
        )

        # When the supervised review runs with 1 retry
        result = await orchestrator.supervise_support_review(
            review_fn=review,
            ticket_key=ticket.key,
            max_retries=1,
        )

        # Then the decision is ESCALATE or REJECT
        assert result.decision in (
            supervisor_entities.SupervisorDecision.ESCALATE,
            supervisor_entities.SupervisorDecision.REJECT,
        )
        assert result.verdict.passed is False

    async def test_no_documentation_yields_rejection(
        self,
        patch_ticket_reviewer,
        patch_response_drafter,
    ) -> None:
        # Given a ticket with no documentation available (empty searchers)
        from tests.functional.conftest import EmptyDocumentSearcher, EmptyPastTicketSearcher

        ticket = factories.make_ticket()
        review = partial(
            support_review.review_ticket,
            ticket,
            document_searcher=EmptyDocumentSearcher(),
            ticket_searcher=EmptyPastTicketSearcher(),
        )

        # When the supervised review runs
        result = await orchestrator.supervise_support_review(
            review_fn=review,
            ticket_key=ticket.key,
            max_retries=1,
        )

        # Then quality fails because the pipeline produces a generic "no docs" response
        assert result.verdict.passed is False
        assert result.decision in (
            supervisor_entities.SupervisorDecision.ESCALATE,
            supervisor_entities.SupervisorDecision.REJECT,
        )
