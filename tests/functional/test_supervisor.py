from __future__ import annotations

from functools import partial

import pytest

from sentinel.application.supervisor import orchestrator
from sentinel.domain.supervisor import entities as supervisor_entities
from sentinel.interfaces.graphs import sre_investigation, support_review
from tests import factories
from tests.functional.conftest import (
    StubDocumentSearcher,
    StubPastTicketSearcher,
    _build_fake_config,
    _fake_alert_classifier_run,
    _fake_ticket_reviewer_run,
    _make_fake_agent,
)


@pytest.mark.asyncio
class TestSuperviseSreInvestigation:
    async def test_publishes_when_quality_passes(
        self,
        mock_holmes,
        fake_sre_config,
    ) -> None:
        # Given a standard alert with mocked agents that produce good output
        alert = factories.make_alert()
        investigate = partial(
            sre_investigation.investigate_alert,
            alert,
            envelope=factories.make_envelope(),
            agent_for=fake_sre_config.agent_for,
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
    ) -> None:
        # Given agents that produce a degraded reply (root cause analysis fails)
        async def failing_run(*, user_prompt, deps, **kwargs):
            raise RuntimeError("LLM unavailable")

        config = _build_fake_config(
            {
                "alert_classifier": _make_fake_agent(_fake_alert_classifier_run),
                "root_cause_analyser": _make_fake_agent(failing_run),
            }
        )

        alert = factories.make_alert()
        investigate = partial(
            sre_investigation.investigate_alert,
            alert,
            envelope=factories.make_envelope(),
            agent_for=config.agent_for,
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
    ) -> None:
        # Given agents that produce degraded output
        async def failing_run(*, user_prompt, deps, **kwargs):
            raise RuntimeError("LLM unavailable")

        config = _build_fake_config(
            {
                "alert_classifier": _make_fake_agent(_fake_alert_classifier_run),
                "root_cause_analyser": _make_fake_agent(failing_run),
            }
        )

        alert = factories.make_alert()
        investigate = partial(
            sre_investigation.investigate_alert,
            alert,
            envelope=factories.make_envelope(),
            agent_for=config.agent_for,
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
        fake_support_config,
    ) -> None:
        # Given a standard ticket with mocked agents that produce good output
        ticket = factories.make_ticket()
        review = partial(
            support_review.review_ticket,
            ticket,
            envelope=factories.make_envelope(),
            agent_for=fake_support_config.agent_for,
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
    ) -> None:
        # Given agents that produce degraded output (drafter fails)
        async def failing_run(*, user_prompt, deps, **kwargs):
            raise RuntimeError("LLM unavailable")

        config = _build_fake_config(
            {
                "ticket_reviewer": _make_fake_agent(_fake_ticket_reviewer_run),
                "response_drafter": _make_fake_agent(failing_run),
            }
        )

        ticket = factories.make_ticket()
        review = partial(
            support_review.review_ticket,
            ticket,
            envelope=factories.make_envelope(),
            agent_for=config.agent_for,
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
        fake_support_config,
    ) -> None:
        # Given a ticket with no documentation available (empty searchers)
        from tests.functional.conftest import EmptyDocumentSearcher, EmptyPastTicketSearcher

        ticket = factories.make_ticket()
        review = partial(
            support_review.review_ticket,
            ticket,
            envelope=factories.make_envelope(),
            agent_for=fake_support_config.agent_for,
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
