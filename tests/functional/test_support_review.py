from __future__ import annotations

import pytest

from sentinel.interfaces.graphs import support_review
from tests.factories import make_ticket
from tests.functional.conftest import (
    EmptyDocumentSearcher,
    EmptyPastTicketSearcher,
    StubDocumentSearcher,
    StubPastTicketSearcher,
)


@pytest.mark.usefixtures("patch_ticket_reviewer", "patch_response_drafter")
class TestSupportReviewPipeline:
    async def test_full_pipeline_returns_populated_reply(self, sample_ticket):
        # Given a support ticket with documentation and past tickets available
        # When running the full support review pipeline
        reply = await support_review.review_ticket(
            ticket=sample_ticket,
            document_searcher=StubDocumentSearcher(),
            ticket_searcher=StubPastTicketSearcher(),
        )

        # Then the reply contains a suggested response with sources and confidence
        assert reply.ticket_key == sample_ticket.key
        assert reply.suggested_response != ""
        assert "SSO" in reply.suggested_response or "password" in reply.suggested_response.lower()
        assert reply.confidence is not None
        assert reply.confidence.total == pytest.approx(0.82)
        assert reply.confidence.label.value == "High"

    async def test_pipeline_populates_sources(self, sample_ticket):
        # Given documentation search returns results
        # When running the review pipeline
        reply = await support_review.review_ticket(
            ticket=sample_ticket,
            document_searcher=StubDocumentSearcher(),
            ticket_searcher=StubPastTicketSearcher(),
        )

        # Then sources are included in the reply
        assert reply.sources is not None
        assert len(reply.sources) > 0
        assert reply.sources[0]["title"] == "Login Troubleshooting Guide"

    async def test_pipeline_sets_category_from_classification(self, sample_ticket):
        # Given the ticket reviewer classifies the ticket as "account"
        # When running the pipeline
        reply = await support_review.review_ticket(
            ticket=sample_ticket,
            document_searcher=StubDocumentSearcher(),
            ticket_searcher=StubPastTicketSearcher(),
        )

        # Then the reply carries the classified category
        assert reply.category == "account"

    async def test_pipeline_without_searchers_exits_early(self, sample_ticket):
        # Given no document or ticket searchers are configured
        # When running the pipeline
        reply = await support_review.review_ticket(
            ticket=sample_ticket,
            document_searcher=None,
            ticket_searcher=None,
        )

        # Then the reply indicates manual review is needed
        assert reply.ticket_key == sample_ticket.key
        assert "manual review" in reply.suggested_response.lower()
        assert reply.confidence is None

    async def test_pipeline_with_empty_search_results_exits_early(self, sample_ticket):
        # Given searchers that return no results
        # When running the pipeline
        reply = await support_review.review_ticket(
            ticket=sample_ticket,
            document_searcher=EmptyDocumentSearcher(),
            ticket_searcher=EmptyPastTicketSearcher(),
        )

        # Then the pipeline exits early with a fallback response
        assert reply.ticket_key == sample_ticket.key
        assert "no relevant documentation" in reply.suggested_response.lower()

    async def test_pipeline_works_with_only_doc_searcher(self, sample_ticket):
        # Given only a document searcher (no past ticket searcher)
        # When running the pipeline
        reply = await support_review.review_ticket(
            ticket=sample_ticket,
            document_searcher=StubDocumentSearcher(),
            ticket_searcher=None,
        )

        # Then the pipeline still produces a valid drafted response
        assert reply.suggested_response != ""
        assert reply.confidence is not None

    async def test_different_ticket_types_flow_through(self):
        # Given a billing-related ticket (different from the default account ticket)
        billing_ticket = make_ticket(
            ticket_id="20001",
            key="SUPPORT-99",
            summary="Incorrect charge on invoice",
            description="I was billed $500 instead of $50 for last month.",
            reporter="Bob Smith",
            priority="Critical",
            labels=["billing", "escalation"],
        )

        # When running the pipeline
        reply = await support_review.review_ticket(
            ticket=billing_ticket,
            document_searcher=StubDocumentSearcher(),
            ticket_searcher=StubPastTicketSearcher(),
        )

        # Then the pipeline completes for the billing ticket
        assert reply.ticket_key == "SUPPORT-99"
        assert reply.suggested_response != ""
