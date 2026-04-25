"""
Unit tests for job read operations.
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.domain.jobs import queries


class TestFetchJob:
    @pytest.mark.asyncio
    async def test_returns_dict_when_row_exists(self) -> None:
        # Given a mock database that returns a row
        mock_db = mock.AsyncMock()
        job_id = uuid.uuid4()
        mock_row = mock.MagicMock()
        mock_row._mapping = {
            "id": job_id,
            "job_type": "investigation",
            "status": "pending",
        }
        mock_db.fetch_one.return_value = mock_row

        # When fetching the job by ID
        result = await queries.fetch_job(db=mock_db, job_id=job_id)

        # Then the row is returned as a dict
        assert result is not None
        assert result["id"] == job_id

    @pytest.mark.asyncio
    async def test_returns_none_when_row_absent(self) -> None:
        # Given a mock database that returns no row
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = None

        # When fetching a non-existent job
        result = await queries.fetch_job(
            db=mock_db,
            job_id=uuid.uuid4(),
        )

        # Then None is returned
        assert result is None

    @pytest.mark.asyncio
    async def test_calls_fetch_one_with_core_query(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = None

        # When fetching a job
        await queries.fetch_job(db=mock_db, job_id=uuid.uuid4())

        # Then fetch_one is called exactly once
        mock_db.fetch_one.assert_called_once()
