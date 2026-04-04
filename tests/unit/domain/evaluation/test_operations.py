from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.domain.evaluation import operations


class TestPersistComparisonRun:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_id(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        investigation_record_id = uuid.uuid4()

        # When a comparison run is persisted
        result_id = await operations.persist_comparison_run(
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


class TestPersistEvalRun:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_id(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When an eval run is persisted
        result_id = await operations.persist_eval_run(
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
