from __future__ import annotations

import abc
import datetime

import attrs


class UnableToSearchDocumentsError(Exception):
    pass


class UnableToSearchMetricsError(Exception):
    pass


class UnableToSearchTicketsError(Exception):
    pass


@attrs.frozen
class DocumentSearchResult:
    id: str
    title: str
    excerpt: str
    url: str
    relevance: float
    contents: str | None = None
    source_type: str = ""
    last_edited_at: datetime.datetime | None = None


@attrs.frozen
class TicketSearchResult:
    id: str
    key: str
    summary: str
    description: str
    resolution: str | None
    url: str
    relevance: float


@attrs.frozen
class MetricsSearchResult:
    source: str
    query: str
    summary: str
    raw_data: str
    relevance: float


class BaseDocumentSearcher(abc.ABC):
    @abc.abstractmethod
    async def search(
        self,
        *,
        query: str,
        limit: int,
    ) -> list[DocumentSearchResult]:
        """
        Perform a document search query and return a list of results.

        Raises:
            UnableToSearchDocumentsError: If the search could not be completed.
        """


class BaseMetricsSearcher(abc.ABC):
    @abc.abstractmethod
    async def search(
        self,
        *,
        query: str,
        time_range_minutes: int,
        limit: int,
    ) -> list[MetricsSearchResult]:
        """
        Perform a metrics/logs search query and return results.

        Raises:
            UnableToSearchMetricsError: If the search could not be completed.
        """


class BasePastTicketSearcher(abc.ABC):
    @abc.abstractmethod
    async def search(
        self,
        *,
        query: str,
        limit: int,
    ) -> list[TicketSearchResult]:
        """
        Search past resolved tickets for similar issues.

        Raises:
            UnableToSearchTicketsError: If the search could not be completed.
        """
