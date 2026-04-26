"""Unit tests for ``fetch_recorded_replay_bundle`` (F4.7 slice C)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest import mock

import pytest

from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.domain.pipeline import queries
from sentinel.utils import replay_bundle as bundle_mod
from tests import factories


_FIXED_AT = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)


def _build_bundle() -> bundle_mod.ReplayBundle:
    return bundle_mod.ReplayBundle(
        envelope=factories.make_envelope(),
        alert_payload={"alert_id": "P1"},
        runbook_id="k8s-crashloop",
        runbook_version_sha="v1",
        tool_io=(
            bundle_mod.ToolIOEntry(
                tool_name="kubectl_logs",
                inputs={"namespace": "ns"},
                outputs="200 lines",
                evidence_object_id=None,
                at=_FIXED_AT,
            ),
        ),
        llm_io=(
            bundle_mod.LLMIOEntry(
                agent_name="root_cause",
                model_id="openai/gpt-4.1",
                inputs={"prompt": "diagnose"},
                outputs={"text": "OOM"},
                token_usage=None,
                at=_FIXED_AT,
            ),
        ),
        final_outputs={"root_cause": "OOM"},
    )


def _make_row_mapping(
    *,
    run_id: uuid.UUID,
    bundle: bundle_mod.ReplayBundle | None,
    sha_override: str | None = None,
) -> dict[str, Any]:
    """Build a row dict mirroring ``PipelineRunRecord._mapping``."""
    if bundle is None:
        return {"id": run_id, "replay_bundle_json": None, "replay_bundle_sha": None}
    canonical = bundle_mod.to_canonical_json(bundle)
    bundle_dict = json.loads(canonical)
    sha = sha_override if sha_override is not None else bundle.bundle_sha
    return {
        "id": run_id,
        "replay_bundle_json": bundle_dict,
        "replay_bundle_sha": sha,
    }


def _mock_db_returning(mapping: dict[str, Any] | None) -> mock.AsyncMock:
    mock_db = mock.AsyncMock()
    if mapping is None:
        mock_db.fetch_one.return_value = None
    else:
        mock_row = mock.MagicMock()
        mock_row._mapping = mapping
        mock_db.fetch_one.return_value = mock_row
    return mock_db


class TestFetchRecordedReplayBundleHappyPath:
    @pytest.mark.asyncio
    async def test_reconstructs_bundle_when_sha_matches(self) -> None:
        # Given a row with a valid bundle JSON and matching sha
        run_id = uuid.uuid4()
        original = _build_bundle()
        mock_db = _mock_db_returning(
            _make_row_mapping(run_id=run_id, bundle=original),
        )

        # When fetch_recorded_replay_bundle is called
        result = await queries.fetch_recorded_replay_bundle(db=mock_db, run_id=run_id)

        # Then the reconstructed bundle has the same canonical sha as the original
        assert result.bundle_sha == original.bundle_sha
        assert result.envelope.request_id == original.envelope.request_id
        assert result.alert_payload == original.alert_payload
        assert result.runbook_id == original.runbook_id
        assert result.tool_io[0].tool_name == "kubectl_logs"
        assert result.llm_io[0].agent_name == "root_cause"
        assert result.final_outputs == {"root_cause": "OOM"}


class TestFetchRecordedReplayBundleSHAMismatch:
    @pytest.mark.asyncio
    async def test_raises_when_stored_sha_diverges(self) -> None:
        # Given a row with a valid bundle JSON but a deliberately wrong sha
        run_id = uuid.uuid4()
        wrong_sha = "0" * 64
        mock_db = _mock_db_returning(
            _make_row_mapping(
                run_id=run_id,
                bundle=_build_bundle(),
                sha_override=wrong_sha,
            ),
        )

        # When fetch_recorded_replay_bundle is called
        # Then ReplayBundleSHAMismatchError surfaces with both sha values
        with pytest.raises(pipeline_errors.ReplayBundleSHAMismatchError) as exc_info:
            await queries.fetch_recorded_replay_bundle(db=mock_db, run_id=run_id)
        assert exc_info.value.run_id == run_id
        assert exc_info.value.stored_sha == wrong_sha
        assert exc_info.value.recomputed_sha != wrong_sha


class TestFetchRecordedReplayBundleMissingRow:
    @pytest.mark.asyncio
    async def test_raises_when_row_is_missing(self) -> None:
        # Given a database returning no row for the run_id
        run_id = uuid.uuid4()
        mock_db = _mock_db_returning(None)

        # When fetch_recorded_replay_bundle is called
        # Then ReplayBundleNotFoundError surfaces
        with pytest.raises(pipeline_errors.ReplayBundleNotFoundError) as exc_info:
            await queries.fetch_recorded_replay_bundle(db=mock_db, run_id=run_id)
        assert exc_info.value.run_id == run_id

    @pytest.mark.asyncio
    async def test_raises_when_bundle_column_is_null(self) -> None:
        # Given a row that exists but has a null replay_bundle_json column
        # (pre-F4.7 row, or a run that did not opt into capture)
        run_id = uuid.uuid4()
        mock_db = _mock_db_returning(_make_row_mapping(run_id=run_id, bundle=None))

        # When fetch_recorded_replay_bundle is called
        # Then ReplayBundleNotFoundError surfaces
        with pytest.raises(pipeline_errors.ReplayBundleNotFoundError) as exc_info:
            await queries.fetch_recorded_replay_bundle(db=mock_db, run_id=run_id)
        assert exc_info.value.run_id == run_id
