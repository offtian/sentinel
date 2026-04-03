from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.data import comparison as comparison_persistence


class TestPersistComparisonRun:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_id(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        investigation_record_id = uuid.uuid4()

        # When a comparison run is persisted
        result_id = await comparison_persistence.persist_comparison_run(
            db=mock_db,
            investigation_record_id=investigation_record_id,
            baseline_adapter="holmes",
            challenger_adapter="native_k8s",
            baseline_result_json={"adapter_name": "holmes", "duration_ms": 500},
            challenger_result_json={"adapter_name": "native_k8s", "duration_ms": 200},
            comparison_result_json={"case_id": "test", "winner_by_dimension": {}},
            baseline_duration_ms=500,
            challenger_duration_ms=200,
        )

        # Then a UUID is returned and execute was called
        assert isinstance(result_id, uuid.UUID)
        mock_db.execute.assert_called_once()


class TestFetchComparisonRuns:
    @pytest.mark.asyncio
    async def test_returns_rows_for_investigation_id(self) -> None:
        # Given a mock database with one row
        mock_db = mock.AsyncMock()
        investigation_id = uuid.uuid4()
        mock_db.fetch_all.return_value = [
            {
                "id": uuid.uuid4(),
                "investigation_record_id": investigation_id,
                "baseline_adapter": "holmes",
                "challenger_adapter": "native_k8s",
                "baseline_duration_ms": 500,
                "challenger_duration_ms": 200,
                "created_at": "2026-04-03T12:00:00+00:00",
            },
        ]

        # When fetching by investigation record id
        rows = await comparison_persistence.fetch_comparison_runs(
            db=mock_db,
            investigation_record_id=investigation_id,
        )

        # Then one row is returned with correct data
        assert len(rows) == 1
        assert rows[0]["baseline_adapter"] == "holmes"
