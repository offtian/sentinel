from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.data import eval_runs


class TestPersistEvalRun:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_id(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When an eval run is persisted
        result_id = await eval_runs.persist_eval_run(
            db=mock_db,
            dataset_name="k8s_investigation",
            total_cases=3,
            passed_cases=2,
            failed_cases=1,
            average_score=0.75,
            results_json={"cases": [{"id": "1", "passed": True}]},
            run_duration_ms=4200,
        )

        # Then a UUID is returned and execute was called
        assert isinstance(result_id, uuid.UUID)
        mock_db.execute.assert_called_once()


class TestFetchEvalRuns:
    @pytest.mark.asyncio
    async def test_returns_rows_for_dataset(self) -> None:
        # Given a mock database with rows
        mock_db = mock.AsyncMock()
        row_mock = mock.MagicMock()
        row_mock._mapping = {
            "id": uuid.uuid4(),
            "dataset_name": "k8s_investigation",
            "total_cases": 3,
            "passed_cases": 3,
            "failed_cases": 0,
            "average_score": 0.9,
            "run_duration_ms": 3000,
            "created_at": "2026-04-03T12:00:00+00:00",
        }
        mock_db.fetch_all.return_value = [row_mock]

        # When fetching by dataset name
        rows = await eval_runs.fetch_eval_runs(
            db=mock_db,
            dataset_name="k8s_investigation",
        )

        # Then one row is returned
        assert len(rows) == 1
        assert rows[0]["dataset_name"] == "k8s_investigation"
