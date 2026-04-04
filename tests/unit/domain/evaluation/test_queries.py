from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.domain.evaluation import queries


class TestFetchComparisonRuns:
    @pytest.mark.asyncio
    async def test_returns_rows_for_investigation_id(self) -> None:
        # Given a mock database with one comparison row
        mock_db = mock.AsyncMock()
        investigation_id = uuid.uuid4()
        row = mock.MagicMock()
        row._mapping = {
            "id": uuid.uuid4(),
            "investigation_record_id": investigation_id,
            "baseline_adapter": "holmes",
            "challenger_adapter": "native_k8s",
            "baseline_duration_ms": 500,
            "challenger_duration_ms": 200,
            "created_at": "2026-04-03T12:00:00+00:00",
        }
        mock_db.fetch_all.return_value = [row]

        # When fetching by investigation record id
        rows = await queries.fetch_comparison_runs(
            db=mock_db,
            investigation_record_id=investigation_id,
        )

        # Then one row is returned with correct data
        assert len(rows) == 1
        assert rows[0]["baseline_adapter"] == "holmes"
        mock_db.fetch_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_rows(self) -> None:
        # Given a mock database with no rows
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching for a non-existent investigation
        rows = await queries.fetch_comparison_runs(
            db=mock_db,
            investigation_record_id=uuid.uuid4(),
        )

        # Then an empty list is returned
        assert rows == []


class TestFetchEvalRuns:
    @pytest.mark.asyncio
    async def test_returns_rows_for_dataset(self) -> None:
        # Given a mock database with one eval run row
        mock_db = mock.AsyncMock()
        row = mock.MagicMock()
        row._mapping = {
            "id": uuid.uuid4(),
            "dataset_name": "k8s_investigation",
            "total_cases": 3,
            "passed_cases": 3,
            "failed_cases": 0,
            "average_score": 0.9,
            "run_duration_ms": 3000,
            "created_at": "2026-04-03T12:00:00+00:00",
        }
        mock_db.fetch_all.return_value = [row]

        # When fetching by dataset name
        rows = await queries.fetch_eval_runs(
            db=mock_db,
            dataset_name="k8s_investigation",
        )

        # Then one row is returned with correct data
        assert len(rows) == 1
        assert rows[0]["dataset_name"] == "k8s_investigation"
        mock_db.fetch_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_rows(self) -> None:
        # Given a mock database with no rows
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching for a non-existent dataset
        rows = await queries.fetch_eval_runs(
            db=mock_db,
            dataset_name="nonexistent",
        )

        # Then an empty list is returned
        assert rows == []
