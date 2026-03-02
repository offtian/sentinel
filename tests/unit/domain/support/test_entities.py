from __future__ import annotations

from datetime import UTC, datetime

from sentinel.domain.support import entities


class TestTicket:
    def test_create_ticket(self):
        ticket = entities.Ticket(
            id="12345",
            key="SUPPORT-123",
            summary="Cannot log in to dashboard",
            description="I've been unable to log in since yesterday...",
            reporter="John Doe",
            priority="High",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        assert ticket.key == "SUPPORT-123"
        assert ticket.labels == []
        assert ticket.comments == []

    def test_ticket_with_comments(self):
        comment = entities.TicketComment(
            author="Support Agent",
            body="We're looking into this.",
            created_at=datetime(2024, 1, 2, tzinfo=UTC),
        )
        ticket = entities.Ticket(
            id="12345",
            key="SUPPORT-123",
            summary="Test",
            description="Test",
            reporter="User",
            priority="Medium",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            comments=[comment],
            labels=["billing", "urgent"],
        )
        assert len(ticket.comments) == 1
        assert ticket.comments[0].author == "Support Agent"
        assert ticket.labels == ["billing", "urgent"]


class TestResponseSuggestion:
    def test_create_suggestion(self):
        source = entities.DocSource(
            title="Login Guide",
            url="https://docs.example.com/login",
            source_type="notion",
            excerpt="To reset your password...",
            relevance=0.9,
        )
        suggestion = entities.ResponseSuggestion(
            ticket_id="12345",
            suggested_response="Based on our documentation, you can reset your password...",
            sources=[source],
            confidence_score=0.85,
            category="account",
        )
        assert suggestion.ticket_id == "12345"
        assert len(suggestion.sources) == 1
        assert suggestion.confidence_score == 0.85

    def test_suggestion_defaults(self):
        suggestion = entities.ResponseSuggestion(
            ticket_id="12345",
            suggested_response="Test response",
        )
        assert suggestion.sources == []
        assert suggestion.confidence_score is None
        assert suggestion.category is None
