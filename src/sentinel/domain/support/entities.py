from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TicketComment(BaseModel):
    author: str
    body: str
    created_at: datetime


class Ticket(BaseModel):
    id: str
    key: str
    summary: str
    description: str
    reporter: str
    priority: str
    created_at: datetime
    labels: list[str] = Field(default_factory=list)
    comments: list[TicketComment] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class DocSource(BaseModel):
    title: str
    url: str
    source_type: Literal["notion", "confluence", "s3", "jira"]
    excerpt: str
    relevance: float = 0.0


class ResponseSuggestion(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    ticket_id: str
    suggested_response: str
    sources: list[DocSource] = Field(default_factory=list)
    confidence_score: float | None = None
    category: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
